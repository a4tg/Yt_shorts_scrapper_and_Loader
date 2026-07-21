import tempfile
import time
import uuid
import json
import os
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select
from fastapi.testclient import TestClient

import server
from job_queue import DatabaseJobManager, ProcessedJob, _public_result, utc_now
from saas_models import CreditLedger, Job, JobFile, User
from auth_service import attempt_limiter
from billing_service import credit_snapshot
from billing_service import InsufficientCreditsError


def create_user() -> str:
    user = User(
        email=f"queue-{uuid.uuid4().hex}@example.com",
        password_hash="test-only-hash",
        credit_balance=100,
    )
    with server.SessionLocal() as db:
        db.add(user)
        db.commit()
        return user.id


def make_manager(root: Path, processor=None, *, auto_start: bool = False) -> DatabaseJobManager:
    return DatabaseJobManager(
        lambda: server.SessionLocal(),
        processor or (lambda *_args: ProcessedJob(result={})),
        root / "jobs",
        root / "videos",
        auto_start=auto_start,
    )


def delete_user(user_id: str) -> None:
    with server.SessionLocal() as db:
        db.execute(delete(CreditLedger).where(CreditLedger.user_id == user_id))
        user = db.get(User, user_id)
        if user:
            db.delete(user)
            db.commit()


def test_public_job_result_hides_provider_billing_internals() -> None:
    result = _public_result({
        "text": "Ready",
        "usage": {
            "input_tokens": 10,
            "cost_rub": 1.25,
            "balance": 998.75,
            "nested": [{"cost_rub_total": 2.5, "seconds": 60}],
        },
        "_internal_note": "private",
    })
    assert result == {
        "text": "Ready",
        "usage": {"input_tokens": 10, "nested": [{"seconds": 60}]},
    }


def test_job_is_persistent_between_manager_instances() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        user_id = create_user()
        first = make_manager(root)
        second = make_manager(root)
        try:
            created = first.create("import", {"limit": 5}, user_id)
            restored = second.get(str(created["id"]))
            assert restored["status"] == "queued"
            assert restored["owner_id"] == user_id
        finally:
            delete_user(user_id)


def test_expired_worker_lease_is_requeued_and_claimed_again() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        user_id = create_user()
        first = make_manager(root)
        second = make_manager(root)
        try:
            job = first.create("import", {"limit": 5}, user_id)
            first_claim = first._claim_next()
            assert first_claim is not None
            with server.SessionLocal() as db:
                record = db.get(Job, job["id"])
                record.lease_expires_at = utc_now() - timedelta(seconds=1)
                db.commit()

            second_claim = second._claim_next()
            assert second_claim is not None
            assert second_claim[0] == job["id"]
            recovered = second.get(str(job["id"]))
            assert recovered["status"] == "running"
            assert recovered["attempts"] == 2
        finally:
            delete_user(user_id)


def test_exhausted_crashed_job_becomes_error_instead_of_looping_forever() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        user_id = create_user()
        manager = make_manager(root)
        try:
            job = manager.create("import", {"limit": 5}, user_id)
            with server.SessionLocal() as db:
                record = db.get(Job, job["id"])
                record.status = "running"
                record.attempts = record.max_attempts
                record.worker_id = "dead-worker"
                record.lease_expires_at = utc_now() - timedelta(seconds=1)
                db.commit()

            assert manager._claim_next() is None
            failed = manager.get(str(job["id"]))
            assert failed["status"] == "error"
            assert "исчерпало попытки" in str(failed["message"])
            with server.SessionLocal() as db:
                assert credit_snapshot(db, user_id).available == 100
        finally:
            delete_user(user_id)


def test_worker_completes_job_and_registers_result_file() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        output = root / "result.mp4"
        user_id = create_user()

        def processor(job_id, kind, args, log):
            assert job_id
            assert kind == "download"
            assert args["url"] == "https://example.test/video"
            log("Почти готово")
            output.write_bytes(b"video")
            return ProcessedJob(
                result={"filename": output.name},
                files=(output,),
                expires_at=utc_now() + timedelta(hours=1),
            )

        manager = make_manager(root, processor, auto_start=True)
        try:
            job = manager.create(
                "download",
                {"url": "https://example.test/video"},
                user_id,
            )
            deadline = time.monotonic() + 5
            current = manager.get(str(job["id"]))
            while current["status"] not in {"done", "error"} and time.monotonic() < deadline:
                time.sleep(0.05)
                current = manager.get(str(job["id"]))

            assert current["status"] == "done", current
            assert current["result"] == {"filename": "result.mp4"}
            with server.SessionLocal() as db:
                file_count = db.scalar(
                    select(func.count()).select_from(JobFile).where(JobFile.job_id == job["id"])
                )
                assert file_count == 1
        finally:
            manager.stop()
            delete_user(user_id)


def test_download_batch_is_created_atomically_with_positions_and_deduplication() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        user_id = create_user()
        manager = make_manager(root)
        try:
            batch = manager.create_batch(
                "download",
                [
                    {"url": "https://example.test/one", "overlays": []},
                    {"url": "https://example.test/two", "overlays": []},
                    {"url": "https://example.test/two", "overlays": []},
                ],
                user_id,
            )
            assert batch["created_count"] == 2
            assert batch["duplicate_count"] == 1
            assert batch["credits_reserved"] == 2
            positions = [job["queue_position"] for job in batch["jobs"]]
            assert positions[1] == positions[0] + 1
            assert positions[2] == positions[1]
            assert batch["jobs"][1]["id"] == batch["jobs"][2]["id"]
            assert all(job["batch_id"] == batch["batch_id"] for job in batch["jobs"])
            with server.SessionLocal() as db:
                assert credit_snapshot(db, user_id).available == 98
        finally:
            delete_user(user_id)


def test_download_batch_reserves_all_credits_or_creates_nothing() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        user_id = create_user()
        with server.SessionLocal() as db:
            user = db.get(User, user_id)
            user.credit_balance = 2
            db.commit()
        manager = make_manager(root)
        try:
            with pytest.raises(InsufficientCreditsError):
                manager.create_batch(
                    "download",
                    [
                        {"url": "https://example.test/one", "overlays": []},
                        {"url": "https://example.test/two", "overlays": []},
                        {"url": "https://example.test/three", "overlays": []},
                    ],
                    user_id,
                )
            with server.SessionLocal() as db:
                count = db.scalar(
                    select(func.count(Job.id)).where(Job.user_id == user_id)
                )
                assert count == 0
                assert credit_snapshot(db, user_id).available == 2
        finally:
            delete_user(user_id)


def test_download_batch_runs_sequentially_and_reports_accumulated_wait() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        user_id = create_user()
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def processor(_job_id, kind, _args, _log):
            nonlocal active, maximum_active
            assert kind == "download"
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return ProcessedJob(result={"filename": "ready.mp4"})

        with patch.dict(os.environ, {"YT_LOADER_JOB_POLL_SECONDS": "0.01"}):
            manager = make_manager(root, processor, auto_start=True)
        try:
            batch = manager.create_batch(
                "download",
                [
                    {"url": f"https://example.test/{index}", "overlays": []}
                    for index in range(3)
                ],
                user_id,
            )
            deadline = time.monotonic() + 5
            jobs = [manager.get(job["id"]) for job in batch["jobs"]]
            while (
                any(job["status"] not in {"done", "error"} for job in jobs)
                and time.monotonic() < deadline
            ):
                time.sleep(0.03)
                jobs = [manager.get(job["id"]) for job in batch["jobs"]]

            assert [job["status"] for job in jobs] == ["done", "done", "done"]
            assert maximum_active == 1
            waits = [float(job["queue_seconds"]) for job in jobs]
            assert waits[1] >= waits[0] + 0.05
            assert waits[2] >= waits[1] + 0.05
        finally:
            manager.stop()
            delete_user(user_id)


def test_legacy_json_is_imported_once_then_removed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        jobs_dir = root / "jobs"
        jobs_dir.mkdir()
        user_id = create_user()
        job_id = uuid.uuid4().hex
        source = jobs_dir / f"{job_id}.json"
        source.write_text(
            json.dumps(
                {
                    "id": job_id,
                    "owner_id": user_id,
                    "kind": "download",
                    "status": "done",
                    "message": "Готово",
                    "created_at": utc_now().isoformat(),
                    "result": {"filename": "legacy.mp4"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = make_manager(root)
        try:
            manager._import_legacy_jobs()
            restored = manager.get(job_id)
            assert restored["status"] == "done"
            assert restored["result"] == {"filename": "legacy.mp4"}
            assert not source.exists()
        finally:
            delete_user(user_id)


def test_expired_import_result_is_deleted_and_registry_is_updated() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        jobs_dir = root / "jobs"
        jobs_dir.mkdir()
        result_path = root / "imports" / "result.csv"
        result_path.parent.mkdir()
        result_path.write_bytes(b"metadata")
        user_id = create_user()
        manager = make_manager(root)
        try:
            job = manager.create("import", {"limit": 1}, user_id)
            manager.update(
                str(job["id"]),
                status="done",
                result={"count": 1},
                ready_expires_at=(utc_now() - timedelta(seconds=1)).isoformat(),
            )
            with server.SessionLocal() as db:
                db.add(
                    JobFile(
                        user_id=user_id,
                        job_id=str(job["id"]),
                        kind="result",
                        original_name=result_path.name,
                        storage_path=str(result_path.resolve()),
                        size_bytes=result_path.stat().st_size,
                    )
                )
                db.commit()

            manager._expire_downloads()
            assert manager.get(str(job["id"]))["status"] == "deleted"
            assert not result_path.exists()
            with server.SessionLocal() as db:
                file_record = db.scalar(
                    select(JobFile).where(JobFile.job_id == str(job["id"]))
                )
                assert file_record is not None and file_record.deleted_at is not None
        finally:
            delete_user(user_id)


def test_fastapi_lifespan_starts_database_worker() -> None:
    original_processor = server.manager.processor

    def processor(_job_id, kind, args, _log):
        assert kind == "import"
        assert args["limit"] == 1
        return ProcessedJob(result={"count": 0})

    server.manager.processor = processor
    try:
        with TestClient(server.app) as client:
            registered = client.post(
                "/api/auth/register",
                headers={"Origin": "http://testserver"},
                json={
                    "email": f"lifespan-{uuid.uuid4().hex}@example.com",
                    "password": "correct horse battery staple",
                },
            )
            attempt_limiter.clear("register:testclient")
            assert registered.status_code == 201
            created = client.post(
                "/api/channels/import",
                headers={
                    "Origin": "http://testserver",
                    "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
                },
                json={
                    "channel_url": "https://youtube.com/@example/shorts",
                    "limit": 1,
                },
            )
            assert created.status_code == 202
            job_id = created.json()["id"]
            deadline = time.monotonic() + 5
            current = client.get(f"/api/jobs/{job_id}").json()
            while current["status"] not in {"done", "error"} and time.monotonic() < deadline:
                time.sleep(0.05)
                current = client.get(f"/api/jobs/{job_id}").json()
            assert current["status"] == "done", current
    finally:
        server.manager.processor = original_processor
