import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from billing_service import (
    actual_job_credit_cost,
    charge_job_credits,
    job_credit_cost,
    release_job_reservation,
    reserve_credits,
    require_plan_capacity,
)
from saas_models import AccountToken, Job, JobFile, User, UserSession, WebhookEvent
from observability import metrics


TERMINAL_STATUSES = {"done", "error", "deleted"}
DOWNLOADABLE_JOB_KINDS = {"download", "ai_image", "ai_clips"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    normalized = as_utc(value)
    return normalized.isoformat() if normalized else None


def parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


@dataclass(frozen=True)
class ProcessedJob:
    result: dict[str, Any]
    files: tuple[Path, ...] = ()
    expires_at: datetime | None = None


Processor = Callable[[str, str, dict[str, Any], Callable[[str], None]], ProcessedJob]


class DatabaseJobManager:
    """A PostgreSQL-backed queue with leases and crash recovery.

    PostgreSQL uses ``FOR UPDATE SKIP LOCKED`` so multiple worker processes may
    poll safely. SQLite is retained for local development with a single worker.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        processor: Processor,
        jobs_dir: Path,
        videos_dir: Path,
        *,
        auto_start: bool = True,
    ) -> None:
        self.session_factory = session_factory
        self.processor = processor
        self.jobs_dir = jobs_dir
        self.videos_dir = videos_dir
        self.worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = max(30, int(os.getenv("YT_LOADER_JOB_LEASE_SECONDS", "120")))
        self.poll_seconds = max(0.1, float(os.getenv("YT_LOADER_JOB_POLL_SECONDS", "0.5")))
        self._stop = threading.Event()
        self._legacy_imported = False
        self._last_database_cleanup = 0.0
        self._threads: list[threading.Thread] = []
        if auto_start:
            self.start()

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name="database-media-worker"),
            threading.Thread(target=self._expiry_worker, daemon=True, name="database-expiry-worker"),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads = []

    def healthy(self) -> bool:
        # Before application lifespan starts there is nothing to supervise.
        return not self._threads or all(thread.is_alive() for thread in self._threads)

    def queue_counts(self) -> dict[str, int]:
        with self.session_factory() as db:
            rows = db.execute(
                select(Job.status, func.count(Job.id)).group_by(Job.status)
            ).all()
        return {str(status): int(count) for status, count in rows}

    @staticmethod
    def _public(job: Job) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": job.id,
            "kind": job.kind,
            "owner_id": job.user_id,
            "workspace_id": job.workspace_id,
            "project_id": job.project_id,
            "status": job.status,
            "message": job.message or "",
            "created_at": iso(job.created_at),
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "credits_reserved": job.credits_reserved,
            "credits_spent": job.credits_spent,
        }
        optional = {
            "started_at": iso(job.started_at),
            "finished_at": iso(job.finished_at),
            "ready_expires_at": iso(job.expires_at),
            "download_ticket_at": iso(job.download_ticket_at),
            "downloaded_at": iso(job.downloaded_at),
            "delete_at": iso(job.delete_at),
            "deleted_at": iso(job.deleted_at),
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        if job.result_payload is not None:
            payload["result"] = dict(job.result_payload)
        if job.error_message:
            payload["error"] = job.error_message
        created_at = as_utc(job.created_at)
        started_at = as_utc(job.started_at)
        finished_at = as_utc(job.finished_at)
        if created_at and started_at:
            payload["queue_seconds"] = round(
                max(0.0, (started_at - created_at).total_seconds()), 3
            )
        if started_at and finished_at:
            payload["processing_seconds"] = round(
                max(0.0, (finished_at - started_at).total_seconds()), 3
            )
        if job.kind == "download" and job.request_payload:
            payload["source_url"] = str(job.request_payload.get("url") or "")
            if job.request_payload.get("batch_id"):
                payload["batch_id"] = str(job.request_payload["batch_id"])
        return payload

    def _public_with_queue_position(self, db: Session, job: Job) -> dict[str, object]:
        payload = self._public(job)
        if job.kind == "download" and job.status == "queued":
            ahead = db.scalar(
                select(func.count(Job.id)).where(
                    Job.kind == "download",
                    Job.status == "queued",
                    or_(
                        Job.created_at < job.created_at,
                        (Job.created_at == job.created_at) & (Job.id < job.id),
                    ),
                )
            ) or 0
            payload["queue_position"] = int(ahead) + 1
        elif job.kind == "download" and job.status == "running":
            payload["queue_position"] = 0
        return payload

    def create(
        self,
        kind: str,
        args: dict[str, object],
        owner_id: str,
        *,
        job_id: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, object]:
        with self.session_factory() as db, db.begin():
            active_jobs = db.scalar(
                select(func.count(Job.id)).where(
                    Job.user_id == owner_id, Job.status.in_(["queued", "running"])
                )
            ) or 0
            require_plan_capacity(db, owner_id, "active_jobs", int(active_jobs))
            cost = job_credit_cost(kind, args)
            reserve_credits(db, owner_id, cost)
            record = Job(
                id=job_id or uuid.uuid4().hex,
                user_id=owner_id,
                workspace_id=workspace_id,
                project_id=project_id,
                kind=kind,
                status="queued",
                request_payload=dict(args),
                message="В очереди",
                available_at=utc_now(),
                credits_reserved=cost,
            )
            db.add(record)
            db.flush()
            return self._public_with_queue_position(db, record)

    def create_batch(
        self,
        kind: str,
        args_list: list[dict[str, object]],
        owner_id: str,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        maximum: int = 20,
    ) -> dict[str, object]:
        if not 1 <= len(args_list) <= maximum:
            raise ValueError(f"Пакет должен содержать от 1 до {maximum} заданий.")
        batch_id = uuid.uuid4().hex
        ordered_job_ids: list[str] = []
        created_count = 0
        duplicate_count = 0
        total_reserved = 0
        with self.session_factory() as db, db.begin():
            # Locking the user serializes double-clicks and concurrent batch
            # requests before duplicate detection and credit reservation.
            user = db.scalar(
                select(User).where(User.id == owner_id).with_for_update()
            )
            if user is None:
                raise KeyError(owner_id)
            active = db.scalars(
                select(Job)
                .where(
                    Job.user_id == owner_id,
                    Job.status.in_(["queued", "running"]),
                )
                .order_by(Job.created_at, Job.id)
                .with_for_update()
            ).all()
            active_by_url = {
                str(record.request_payload.get("url") or ""): record
                for record in active
                if record.kind == kind and record.request_payload
            }
            new_by_url: dict[str, Job] = {}
            batch_created_at = utc_now()
            for raw_args in args_list:
                args = dict(raw_args)
                source_url = str(args.get("url") or "")
                existing = active_by_url.get(source_url) or new_by_url.get(source_url)
                if existing is not None:
                    ordered_job_ids.append(existing.id)
                    duplicate_count += 1
                    continue
                args["batch_id"] = batch_id
                cost = job_credit_cost(kind, args)
                record = Job(
                    id=uuid.uuid4().hex,
                    user_id=owner_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=kind,
                    status="queued",
                    request_payload=args,
                    message="В очереди",
                    created_at=batch_created_at + timedelta(microseconds=created_count),
                    available_at=batch_created_at,
                    credits_reserved=cost,
                )
                db.add(record)
                new_by_url[source_url] = record
                ordered_job_ids.append(record.id)
                total_reserved += cost
                created_count += 1
            require_plan_capacity(
                db,
                owner_id,
                "active_jobs",
                len(active),
                increment=created_count,
            )
            reserve_credits(db, owner_id, total_reserved)
            db.flush()
        jobs = [self.get(job_id) for job_id in ordered_job_ids]
        return {
            "batch_id": batch_id,
            "jobs": jobs,
            "created_count": created_count,
            "duplicate_count": duplicate_count,
            "credits_reserved": total_reserved,
        }

    def get(self, job_id: str) -> dict[str, object]:
        with self.session_factory() as db:
            record = db.get(Job, job_id)
            if record is None:
                raise KeyError(job_id)
            return self._public_with_queue_position(db, record)

    def list_for_user(
        self,
        user_id: str,
        *,
        is_admin: bool = False,
        workspace_ids: list[str] | None = None,
        project_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        with self.session_factory() as db:
            statement = select(Job)
            if not is_admin:
                access_filters = [Job.user_id == user_id]
                if workspace_ids:
                    access_filters.append(Job.workspace_id.in_(workspace_ids))
                statement = statement.where(or_(*access_filters))
            if project_id:
                statement = statement.where(Job.project_id == project_id)
            if kind:
                statement = statement.where(Job.kind == kind)
            records = db.scalars(
                statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
            ).all()
            return [self._public_with_queue_position(db, record) for record in records]

    def update(self, job_id: str, **values: object) -> None:
        """Compatibility helper for lifecycle actions and focused tests."""
        with self.session_factory() as db:
            record = db.get(Job, job_id)
            if record is None:
                raise KeyError(job_id)
            for key, value in values.items():
                if key == "result":
                    record.result_payload = dict(value or {})
                elif key == "ready_expires_at":
                    record.expires_at = parse_datetime(value)
                elif key in {
                    "download_ticket_at", "downloaded_at", "delete_at", "deleted_at",
                    "started_at", "finished_at",
                }:
                    setattr(record, key, parse_datetime(value))
                elif hasattr(record, key):
                    setattr(record, key, value)
            db.commit()

    def start_download_timer(self, job_id: str, minutes: int) -> dict[str, object]:
        with self.session_factory() as db, db.begin():
            record = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if record is None:
                raise KeyError(job_id)
            if record.kind not in DOWNLOADABLE_JOB_KINDS or record.status != "done":
                raise RuntimeError("Видео ещё не готово")
            if record.delete_at is None:
                now = utc_now()
                record.downloaded_at = now
                record.delete_at = now + timedelta(minutes=minutes)
            result = self._public(record)
        return result

    def authorize_download(self, job_id: str) -> dict[str, object]:
        with self.session_factory() as db, db.begin():
            record = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if record is None:
                raise KeyError(job_id)
            if record.kind not in DOWNLOADABLE_JOB_KINDS or record.status != "done":
                raise RuntimeError("Видео ещё не готово")
            record.download_ticket_at = utc_now()
            result = self._public(record)
        return result

    def delete_download(self, job_id: str, message: str = "Видео удалено") -> dict[str, object]:
        with self.session_factory() as db, db.begin():
            record = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if record is None:
                raise KeyError(job_id)
            if record.kind not in DOWNLOADABLE_JOB_KINDS:
                raise RuntimeError("Это не задание видео")
            if record.status in {"queued", "running"}:
                raise RuntimeError("Нельзя удалить видео, пока оно обрабатывается")
            if record.status == "deleted":
                return self._public(record)
            shutil.rmtree(self.videos_dir / job_id, ignore_errors=True)
            now = utc_now()
            record.status = "deleted"
            record.message = message
            record.deleted_at = now
            record.finished_at = record.finished_at or now
            db.execute(
                update(JobFile)
                .where(JobFile.job_id == job_id, JobFile.deleted_at.is_(None))
                .values(deleted_at=now)
                .execution_options(synchronize_session=False)
            )
            result = self._public(record)
        return result

    def _recover_expired_leases(self, db: Session, now: datetime) -> None:
        records = db.scalars(
            select(Job)
            .where(
                Job.status == "running",
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for record in records:
            record.worker_id = None
            record.lease_expires_at = None
            if record.request_payload and record.attempts < record.max_attempts:
                record.status = "queued"
                record.available_at = now
                record.message = "Возвращено в очередь после перезапуска worker"
            else:
                release_job_reservation(db, record)
                record.status = "error"
                record.message = "Задание прервано и исчерпало попытки восстановления"
                record.error_message = record.message
                record.finished_at = now

    def _claim_next(self) -> tuple[str, str, dict[str, Any]] | None:
        now = utc_now()
        with self.session_factory() as db, db.begin():
            self._recover_expired_leases(db, now)
            # The next SELECT must see rows re-queued in this same transaction.
            db.flush()
            record = db.scalar(
                select(Job)
                .where(Job.status == "queued", Job.available_at <= now)
                .order_by(Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if record is None:
                return None
            if not record.request_payload:
                release_job_reservation(db, record)
                record.status = "error"
                record.message = "Для задания отсутствуют параметры запуска"
                record.error_message = record.message
                record.finished_at = now
                return None
            record.status = "running"
            record.message = "Выполняется"
            record.started_at = record.started_at or now
            record.worker_id = self.worker_id
            record.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            record.attempts += 1
            return record.id, record.kind, dict(record.request_payload)

    def _heartbeat(self, job_id: str, stopped: threading.Event) -> None:
        interval = max(5.0, self.lease_seconds / 3)
        while not stopped.wait(interval):
            try:
                with self.session_factory() as db:
                    db.execute(
                        update(Job)
                        .where(
                            Job.id == job_id,
                            Job.status == "running",
                            Job.worker_id == self.worker_id,
                        )
                        .values(lease_expires_at=utc_now() + timedelta(seconds=self.lease_seconds))
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
            except SQLAlchemyError:
                continue

    def _log(self, job_id: str, message: str) -> None:
        try:
            with self.session_factory() as db:
                db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status == "running",
                        Job.worker_id == self.worker_id,
                    )
                    .values(message=message[-500:])
                    .execution_options(synchronize_session=False)
                )
                db.commit()
        except SQLAlchemyError:
            return

    def _finish_success(self, job_id: str, processed: ProcessedJob) -> None:
        now = utc_now()
        with self.session_factory() as db, db.begin():
            record = db.scalar(
                select(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.worker_id == self.worker_id,
                )
                .with_for_update()
            )
            if record is None:
                return
            record.status = "done"
            record.message = "Готово"
            record.result_payload = dict(processed.result)
            record.finished_at = now
            record.expires_at = processed.expires_at
            record.worker_id = None
            record.lease_expires_at = None
            actual_cost = actual_job_credit_cost(
                record.kind,
                dict(record.request_payload or {}),
                dict(processed.result),
            )
            charge_job_credits(db, record, actual_cost)
            db.execute(delete(JobFile).where(JobFile.job_id == job_id))
            if record.user_id:
                for path in processed.files:
                    if path.is_file():
                        db.add(
                            JobFile(
                                user_id=record.user_id,
                                job_id=record.id,
                                kind="result",
                                original_name=path.name,
                                storage_path=str(path.resolve()),
                                size_bytes=path.stat().st_size,
                                expires_at=processed.expires_at,
                            )
                        )
        metrics.increment("jobs_completed_total")

    def _finish_error(self, job_id: str, error: Exception) -> None:
        message = str(error)[-1000:] or error.__class__.__name__
        with self.session_factory() as db, db.begin():
            record = db.scalar(
                select(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.worker_id == self.worker_id,
                )
                .with_for_update()
            )
            if record is None:
                return
            release_job_reservation(db, record)
            record.status = "error"
            record.message = message
            record.error_message = message
            record.finished_at = utc_now()
            record.worker_id = None
            record.lease_expires_at = None
        metrics.increment("jobs_failed_total")

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._legacy_imported:
                    self._import_legacy_jobs()
                claimed = self._claim_next()
            except SQLAlchemyError:
                self._stop.wait(2)
                continue
            if claimed is None:
                self._stop.wait(self.poll_seconds)
                continue
            job_id, kind, args = claimed
            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=self._heartbeat,
                args=(job_id, heartbeat_stop),
                daemon=True,
                name=f"heartbeat-{job_id[:8]}",
            )
            heartbeat.start()
            try:
                processed = self.processor(job_id, kind, args, lambda text: self._log(job_id, text))
                self._finish_success(job_id, processed)
            except Exception as exc:
                self._finish_error(job_id, exc)
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=1)

    def _expire_downloads(self) -> None:
        now = utc_now()
        with self.session_factory() as db:
            candidates = db.execute(
                select(Job.id, Job.kind).where(
                    Job.status == "done",
                    ((Job.delete_at.is_not(None)) & (Job.delete_at <= now))
                    | ((Job.delete_at.is_(None)) & (Job.expires_at.is_not(None)) & (Job.expires_at <= now)),
                )
            ).all()
        for job_id, kind in candidates:
            try:
                if kind == "download":
                    self.delete_download(str(job_id), "Срок хранения истёк, видео удалено")
                else:
                    self._delete_expired_result(str(job_id), now)
            except (KeyError, RuntimeError):
                continue

    def _delete_expired_result(self, job_id: str, now: datetime) -> None:
        data_root = self.jobs_dir.parent.resolve()
        with self.session_factory() as db, db.begin():
            record = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if record is None:
                raise KeyError(job_id)
            if record.status != "done":
                return
            files = db.scalars(
                select(JobFile).where(JobFile.job_id == job_id, JobFile.deleted_at.is_(None))
            ).all()
            for file_record in files:
                path = Path(file_record.storage_path).resolve()
                if path.is_relative_to(data_root):
                    path.unlink(missing_ok=True)
                file_record.deleted_at = now
            record.status = "deleted"
            record.message = "Срок хранения результата истёк"
            record.deleted_at = now

    def _expiry_worker(self) -> None:
        while not self._stop.wait(5):
            try:
                self._expire_downloads()
                if time.monotonic() - self._last_database_cleanup >= 60 * 60:
                    self._cleanup_security_records()
                    self._last_database_cleanup = time.monotonic()
            except SQLAlchemyError:
                continue

    def _cleanup_security_records(self) -> None:
        now = utc_now()
        try:
            webhook_days = max(
                30, min(int(os.getenv("YT_LOADER_WEBHOOK_RETENTION_DAYS", "90")), 3650)
            )
        except ValueError:
            webhook_days = 90
        session_cutoff = now - timedelta(days=30)
        webhook_cutoff = now - timedelta(days=webhook_days)
        with self.session_factory() as db, db.begin():
            db.execute(
                delete(AccountToken).where(
                    (AccountToken.expires_at < now)
                    | (
                        AccountToken.used_at.is_not(None)
                        & (AccountToken.used_at < session_cutoff)
                    )
                )
            )
            db.execute(
                delete(UserSession).where(
                    (UserSession.expires_at < session_cutoff)
                    | (
                        UserSession.revoked_at.is_not(None)
                        & (UserSession.revoked_at < session_cutoff)
                    )
                )
            )
            db.execute(
                delete(WebhookEvent).where(WebhookEvent.received_at < webhook_cutoff)
            )

    def _import_legacy_jobs(self) -> None:
        migrated_paths: list[Path] = []
        with self.session_factory() as db, db.begin():
            for path in self.jobs_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    job_id = str(payload["id"])
                except (OSError, ValueError, KeyError):
                    continue
                if db.get(Job, job_id) is not None:
                    migrated_paths.append(path)
                    continue
                owner_id = str(payload.get("owner_id") or "") or None
                if owner_id and db.get(User, owner_id) is None:
                    owner_id = None
                status = str(payload.get("status") or "error")
                message = str(payload.get("message") or "")[:1000]
                if status in {"queued", "running"}:
                    status = "error"
                    message = "Старое задание невозможно восстановить: параметры запуска не сохранены"
                db.add(
                    Job(
                        id=job_id,
                        user_id=owner_id,
                        kind=str(payload.get("kind") or "legacy")[:32],
                        status=status,
                        request_payload=None,
                        result_payload=dict(payload.get("result") or {}) or None,
                        message=message,
                        error_message=message if status == "error" else None,
                        created_at=parse_datetime(payload.get("created_at")) or utc_now(),
                        finished_at=(utc_now() if status in TERMINAL_STATUSES else None),
                        expires_at=parse_datetime(payload.get("ready_expires_at")),
                        download_ticket_at=parse_datetime(payload.get("download_ticket_at")),
                        downloaded_at=parse_datetime(payload.get("downloaded_at")),
                        delete_at=parse_datetime(payload.get("delete_at")),
                        deleted_at=parse_datetime(payload.get("deleted_at")),
                    )
                )
                migrated_paths.append(path)
        # Only remove a legacy source after the surrounding DB transaction committed.
        for path in migrated_paths:
            path.unlink(missing_ok=True)
        self._legacy_imported = True
