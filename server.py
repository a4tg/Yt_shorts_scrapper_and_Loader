import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from auth_routes import router as auth_router
from billing_routes import router as billing_router
from payment_routes import router as payment_router
from auth_service import (
    PUBLIC_API_PATHS,
    SAFE_METHODS,
    authenticate_request,
    csrf_is_valid,
    origin_is_allowed,
)
from billing_service import InsufficientCreditsError
from database import SessionLocal, check_database
from email_service import email_verification_required
from job_queue import DatabaseJobManager, ProcessedJob
from payment_service import SubscriptionRenewalWorker
from saas_models import Overlay
from yookassa_client import YooKassaClient
from server_core import (
    BASE_DIR,
    create_overlay_preview,
    create_overlay_archive,
    download_short,
    is_supported_overlay,
    normalize_channel_shorts_url,
    normalize_video_url,
    run_channel_import,
)


DATA_DIR = Path(os.getenv("YT_LOADER_DATA_DIR", BASE_DIR / "server_data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
IMPORTS_DIR = DATA_DIR / "imports"
VIDEOS_DIR = DATA_DIR / "videos"
LOGOS_DIR = DATA_DIR / "logos"
WEB_DIR = BASE_DIR / "web"
for directory in (JOBS_DIR, IMPORTS_DIR, VIDEOS_DIR, LOGOS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

AFTER_DOWNLOAD_MINUTES = max(1, int(os.getenv("YT_LOADER_AFTER_DOWNLOAD_MINUTES", "15")))
READY_HOURS = max(1, int(os.getenv("YT_LOADER_READY_HOURS", "24")))
RETENTION_HOURS = max(1, int(os.getenv("YT_LOADER_RETENTION_HOURS", "24")))
MAX_OVERLAY_BYTES = max(1, int(os.getenv("YT_LOADER_MAX_OVERLAY_MB", "256"))) * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


def cleanup_expired_files() -> None:
    cutoff = time.time() - RETENTION_HOURS * 3600
    # Overlays are reusable user assets now tracked in PostgreSQL; deleting them
    # by file mtime could break a queued job. They require an explicit lifecycle.
    for root in (IMPORTS_DIR, VIDEOS_DIR):
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                continue


def cleanup_loop() -> None:
    while True:
        cleanup_expired_files()
        time.sleep(3600)


cleanup_expired_files()
threading.Thread(target=cleanup_loop, daemon=True, name="cleanup-worker").start()


class ChannelRequest(BaseModel):
    channel_url: str = Field(min_length=5, max_length=500)
    limit: int = Field(default=50, ge=0, le=1000)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=10, max_length=500)
    logo_token: str | None = None
    logo_tokens: list[str] = Field(default_factory=list, max_length=10)
    opacity: int = Field(default=35, ge=5, le=100)
    width_percent: int = Field(default=22, ge=5, le=80)
    position_x: int = Field(default=50, ge=0, le=100)
    position_y: int = Field(default=96, ge=0, le=100)
    max_height: int = Field(default=1080)
    metadata_mode: Literal["none", "strip", "synthetic"] = "strip"


def process_media_job(
    job_id: str,
    kind: str,
    args: dict[str, object],
    log,
) -> ProcessedJob:
    if kind == "import":
        json_path = IMPORTS_DIR / f"{job_id}.json"
        csv_path = IMPORTS_DIR / f"{job_id}.csv"
        count = run_channel_import(
            str(args["channel_url"]), json_path, csv_path, int(args["limit"])
        )
        return ProcessedJob(
            result={"count": count},
            files=(json_path, csv_path),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=RETENTION_HOURS),
        )
    if kind != "download":
        raise RuntimeError("Неизвестный тип задания")

    overlay_items = list(args.get("overlays") or [])
    overlay_count = len(overlay_items)
    output_dir = VIDEOS_DIR / job_id
    # A recovered attempt must never mix partial files from the crashed attempt.
    shutil.rmtree(output_dir, ignore_errors=True)
    single_path = (
        Path(str(dict(overlay_items[0])["path"])) if len(overlay_items) == 1 else None
    )
    video_path = download_short(
        str(args["url"]), output_dir, single_path,
        int(args["opacity"]), int(args["width_percent"]),
        int(args["position_x"]), int(args["position_y"]),
        int(args["max_height"]), log,
        "none" if overlay_count > 1 else str(args["metadata_mode"]),
    )
    result: dict[str, object] = {
        "filename": video_path.name,
        "overlay_count": overlay_count,
        "metadata_mode": str(args["metadata_mode"]),
    }
    result_path = video_path
    if overlay_count > 1:
        archive_path = output_dir / "overlay_variants.zip"
        folders = create_overlay_archive(
            video_path,
            [
                (Path(str(dict(item)["path"])), str(dict(item)["name"]))
                for item in overlay_items
            ],
            archive_path,
            int(args["opacity"]), int(args["width_percent"]),
            int(args["position_x"]), int(args["position_y"]), log,
            str(args["metadata_mode"]),
        )
        video_path.unlink(missing_ok=True)
        result_path = archive_path
        result = {
            "filename": archive_path.name,
            "overlay_count": overlay_count,
            "folders": folders,
            "format": "zip",
            "metadata_mode": str(args["metadata_mode"]),
        }
    return ProcessedJob(
        result=result,
        files=(result_path,),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=READY_HOURS),
    )


manager = DatabaseJobManager(
    lambda: SessionLocal(), process_media_job, JOBS_DIR, VIDEOS_DIR, auto_start=False
)
renewal_worker = SubscriptionRenewalWorker(
    lambda: SessionLocal(), lambda: YooKassaClient()
)


@asynccontextmanager
async def application_lifespan(_app: FastAPI):
    manager.start()
    renewal_worker.start()
    try:
        yield
    finally:
        manager.stop()
        renewal_worker.stop()


api_docs_enabled = os.getenv("YT_LOADER_ENABLE_API_DOCS", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
app = FastAPI(
    title="YT Shorts Loader",
    docs_url="/api/docs" if api_docs_enabled else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if api_docs_enabled else None,
    lifespan=application_lifespan,
)
allowed_hosts = [
    host.strip()
    for host in os.getenv("YT_LOADER_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    legacy_enabled = os.getenv("YT_LOADER_LEGACY_BASIC_AUTH", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not legacy_enabled:
        return await call_next(request)
    username = os.getenv("YT_LOADER_USERNAME")
    password = os.getenv("YT_LOADER_PASSWORD")
    if not username or not password:
        return await call_next(request)
    try:
        scheme, encoded = request.headers.get("Authorization", "").split(" ", 1)
        supplied_user, supplied_password = base64.b64decode(encoded).decode().split(":", 1)
        valid = scheme.lower() == "basic" and secrets.compare_digest(supplied_user, username) and secrets.compare_digest(supplied_password, password)
    except (ValueError, UnicodeDecodeError):
        valid = False
    if not valid:
        return JSONResponse({"detail": "Требуется авторизация"}, status_code=401, headers={"WWW-Authenticate": "Basic"})
    return await call_next(request)


@app.middleware("http")
async def user_session_auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api") and request.method not in SAFE_METHODS and not origin_is_allowed(request):
        return JSONResponse({"detail": "Недоверенный источник запроса"}, status_code=403)
    if not path.startswith("/api") or path in PUBLIC_API_PATHS:
        return await call_next(request)
    try:
        with SessionLocal() as db:
            user = authenticate_request(db, request)
    except SQLAlchemyError:
        return JSONResponse({"detail": "База данных временно недоступна"}, status_code=503)
    if user is None:
        return JSONResponse({"detail": "Требуется вход в аккаунт"}, status_code=401)
    unverified_allowed = {
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/verification/request",
        "/api/auth/password/change",
    }
    if (
        email_verification_required()
        and user.email_verified_at is None
        and path not in unverified_allowed
    ):
        return JSONResponse(
            {"detail": "Сначала подтвердите email. Новое письмо можно запросить в аккаунте."},
            status_code=403,
        )
    if request.method not in SAFE_METHODS and not csrf_is_valid(request):
        return JSONResponse({"detail": "CSRF-токен отсутствует или недействителен"}, status_code=403)
    request.state.user = user
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(payment_router)


@app.middleware("http")
async def browser_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path != "/api/docs":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://i.ytimg.com; media-src 'self' blob:; "
            "frame-src https://www.youtube-nocookie.com; connect-src 'self'"
        )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/api/health")
def health(response: Response) -> dict[str, str]:
    database_status = "ok" if check_database() else "error"
    workers_status = "ok" if manager.healthy() and renewal_worker.healthy() else "error"
    if database_status != "ok" or workers_status != "ok":
        response.status_code = 503
    return {
        "status": "ok" if database_status == "ok" and workers_status == "ok" else "degraded",
        "database": database_status,
        "workers": workers_status,
    }


@app.post("/api/channels/import", status_code=202)
def import_channel(payload: ChannelRequest, request: Request) -> dict[str, object]:
    try:
        channel_url = normalize_channel_shorts_url(payload.channel_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return manager.create(
            "import",
            {"channel_url": channel_url, "limit": payload.limit},
            owner_id=str(request.state.user.id),
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


def require_job_access(job_id: str, request: Request) -> dict[str, object]:
    """Return a job only to its owner (or an administrator)."""
    try:
        job = manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    user = request.state.user
    # Jobs created before user accounts existed intentionally have no owner.
    # Only administrators may recover those legacy results.
    if str(job.get("owner_id") or "") != str(user.id) and not user.is_admin:
        raise HTTPException(404, "Задание не найдено")
    return job


def decorate_job_urls(job: dict[str, object]) -> dict[str, object]:
    job_id = str(job["id"])
    if job.get("status") == "done":
        if job.get("kind") == "import":
            job["items_url"] = f"/api/imports/{job_id}/items"
            job["csv_url"] = f"/api/imports/{job_id}/metadata.csv"
        elif job.get("kind") == "download":
            job["download_ticket_url"] = f"/api/videos/{job_id}/download-ticket"
            job["delete_url"] = f"/api/videos/{job_id}"
    return job


@app.get("/api/jobs")
def list_jobs(
    request: Request,
    kind: Literal["import", "download"] | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit должен быть от 1 до 200")
    user = request.state.user
    return [
        decorate_job_urls(job)
        for job in manager.list_for_user(
            str(user.id), is_admin=bool(user.is_admin), kind=kind, limit=limit
        )
    ]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, object]:
    return decorate_job_urls(require_job_access(job_id, request))


def load_import(job_id: str) -> list[dict[str, object]]:
    path = IMPORTS_DIR / f"{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "Результат импорта не найден")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/imports/{job_id}/items")
def import_items(job_id: str, request: Request) -> list[dict[str, object]]:
    require_job_access(job_id, request)
    return load_import(job_id)


@app.get("/api/imports/{job_id}/metadata.csv")
def import_csv(job_id: str, request: Request) -> FileResponse:
    require_job_access(job_id, request)
    path = IMPORTS_DIR / f"{job_id}.csv"
    if not path.is_file():
        raise HTTPException(404, "CSV не найден")
    return FileResponse(path, filename="shorts_metadata.csv", media_type="text/csv")


@app.get("/api/imports/{job_id}/{video_id}/metadata.txt")
def item_metadata(job_id: str, video_id: str, request: Request) -> PlainTextResponse:
    require_job_access(job_id, request)
    if not video_id.isascii() or len(video_id) != 11:
        raise HTTPException(400, "Некорректный ID")
    item = next((item for item in load_import(job_id) if item.get("id") == video_id), None)
    if not item:
        raise HTTPException(404, "Shorts не найден")
    text = (
        f"Название: {item['title']}\n"
        f"Ссылка: {item['url']}\n"
        f"Канал: {item['uploader']}\n"
        f"Дата: {item['upload_date']}\n\n"
        f"Теги:\n{', '.join(item['tags'])}\n\n"
        f"Описание:\n{item['description']}\n"
    )
    headers = {"Content-Disposition": f'attachment; filename="{video_id}_metadata.txt"'}
    return PlainTextResponse(text, headers=headers, media_type="text/plain; charset=utf-8")


@app.post("/api/logos")
async def upload_logo(request: Request, file: UploadFile) -> dict[str, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if not suffix or len(suffix) > 11 or not suffix[1:].isalnum():
        suffix = ".media"
    token = uuid.uuid4().hex
    original_name = Path(file.filename or f"overlay{suffix}").name
    safe_stem = re.sub(r'[^\w.-]+', "_", Path(original_name).stem, flags=re.UNICODE).strip(" ._")
    safe_stem = (safe_stem or "overlay")[:60]
    user_logos_dir = LOGOS_DIR / str(request.state.user.id)
    user_logos_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = user_logos_dir / f"{token}_{safe_stem}{suffix}"
    preview_path = user_logos_dir / f"{token}_preview.png"
    size_bytes = 0
    try:
        with overlay_path.open("wb") as destination:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size_bytes += len(chunk)
                if size_bytes > MAX_OVERLAY_BYTES:
                    raise HTTPException(
                        413,
                        f"Оверлей больше {MAX_OVERLAY_BYTES // 1024 // 1024} МБ",
                    )
                destination.write(chunk)
    except Exception:
        overlay_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    if not size_bytes:
        overlay_path.unlink(missing_ok=True)
        raise HTTPException(400, "Загружен пустой файл")
    if not await asyncio.to_thread(is_supported_overlay, overlay_path):
        overlay_path.unlink(missing_ok=True)
        raise HTTPException(
            400,
            "FFmpeg не смог прочитать файл. Выбери изображение или анимацию/видео "
            "в поддерживаемом формате.",
        )
    try:
        await asyncio.to_thread(create_overlay_preview, overlay_path, preview_path)
    except (OSError, RuntimeError) as exc:
        overlay_path.unlink(missing_ok=True)
        preview_path.unlink(missing_ok=True)
        raise HTTPException(400, "Не удалось создать предпросмотр оверлея") from exc
    try:
        with SessionLocal() as db:
            db.add(
                Overlay(
                    id=token,
                    user_id=str(request.state.user.id),
                    original_name=original_name,
                    storage_path=str(overlay_path.resolve()),
                    mime_type=file.content_type,
                    size_bytes=size_bytes,
                )
            )
            db.commit()
    except SQLAlchemyError as exc:
        overlay_path.unlink(missing_ok=True)
        preview_path.unlink(missing_ok=True)
        raise HTTPException(503, "Не удалось сохранить оверлей в базе данных") from exc
    return {
        "token": token,
        "name": original_name,
        "preview_url": f"/api/logos/{token}/preview",
    }


def resolve_overlay_token(token: str, index: int, owner_id: str) -> tuple[Path, str]:
    try:
        uuid.UUID(hex=token)
    except ValueError as exc:
        raise HTTPException(400, "Некорректный оверлей") from exc
    with SessionLocal() as db:
        overlay = db.scalar(
            select(Overlay).where(
                Overlay.id == token,
                Overlay.user_id == owner_id,
                Overlay.deleted_at.is_(None),
            )
        )
    overlay_path = Path(overlay.storage_path).resolve() if overlay else None
    owner_directory = (LOGOS_DIR / owner_id).resolve()
    if (
        overlay_path is None
        or not overlay_path.is_file()
        or not overlay_path.is_relative_to(owner_directory)
    ):
        raise HTTPException(404, "Оверлей не найден")
    display_name = str(overlay.original_name or f"overlay_{index}{overlay_path.suffix}")
    return overlay_path, display_name


@app.get("/api/logos/{token}/preview")
def overlay_preview(token: str, request: Request) -> FileResponse:
    overlay_path, _ = resolve_overlay_token(token, 1, str(request.state.user.id))
    preview_path = overlay_path.with_name(f"{token}_preview.png")
    owner_directory = (LOGOS_DIR / str(request.state.user.id)).resolve()
    if (
        not preview_path.is_file()
        or not preview_path.resolve().is_relative_to(owner_directory)
    ):
        raise HTTPException(404, "Предпросмотр оверлея не найден")
    return FileResponse(
        preview_path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.post("/api/videos/download", status_code=202)
def create_download(payload: DownloadRequest, request: Request) -> dict[str, object]:
    try:
        url = normalize_video_url(payload.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.max_height not in {720, 1080, 1440, 2160}:
        raise HTTPException(400, "Некорректное разрешение")
    tokens = list(dict.fromkeys(
        ([payload.logo_token] if payload.logo_token else []) + payload.logo_tokens
    ))
    if len(tokens) > 10:
        raise HTTPException(400, "За одно задание можно выбрать не более 10 оверлеев")
    overlays = []
    for index, token in enumerate(tokens, start=1):
        overlay_path, display_name = resolve_overlay_token(
            token, index, str(request.state.user.id)
        )
        overlays.append({"path": str(overlay_path), "name": display_name})
    try:
        return manager.create(
            "download",
            {
                "url": url,
                "overlays": overlays,
                "opacity": payload.opacity,
                "width_percent": payload.width_percent,
                "position_x": payload.position_x,
                "position_y": payload.position_y,
                "max_height": payload.max_height,
                "metadata_mode": payload.metadata_mode,
            },
            owner_id=str(request.state.user.id),
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.get("/api/videos/{job_id}/download")
def download_result(job_id: str, request: Request) -> FileResponse:
    require_job_access(job_id, request)
    try:
        job = manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    if job.get("status") == "deleted":
        raise HTTPException(410, "Видео уже удалено")
    if job.get("kind") != "download" or job.get("status") != "done":
        raise HTTPException(409, "Видео ещё не готово")
    if not job.get("download_ticket_at"):
        raise HTTPException(409, "Сначала запросите скачивание")
    if job.get("delete_at") and datetime.fromisoformat(str(job["delete_at"])) <= datetime.now(timezone.utc):
        manager.delete_download(job_id, "Срок хранения истёк, видео удалено")
        raise HTTPException(410, "Срок скачивания истёк")
    filename = str(dict(job.get("result") or {}).get("filename") or "")
    path = VIDEOS_DIR / job_id / filename
    if not filename or not path.is_file() or path.parent != (VIDEOS_DIR / job_id):
        raise HTTPException(404, "Видео не найдено")
    background = (
        None
        if job.get("delete_at")
        else BackgroundTask(manager.start_download_timer, job_id, AFTER_DOWNLOAD_MINUTES)
    )
    media_type = "application/zip" if path.suffix.lower() == ".zip" else "video/mp4"
    return FileResponse(path, filename=filename, media_type=media_type, background=background)


@app.post("/api/videos/{job_id}/download-ticket")
def create_download_ticket(job_id: str, request: Request) -> dict[str, object]:
    require_job_access(job_id, request)
    try:
        job = manager.authorize_download(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    filename = str(dict(job.get("result") or {}).get("filename") or "")
    if not filename or not (VIDEOS_DIR / job_id / filename).is_file():
        raise HTTPException(410, "Видео уже удалено")
    response: dict[str, object] = {
        "download_url": f"/api/videos/{job_id}/download",
        "delete_url": f"/api/videos/{job_id}",
        "timer_minutes": AFTER_DOWNLOAD_MINUTES,
    }
    if job.get("delete_at"):
        response["delete_at"] = job["delete_at"]
    return response


@app.delete("/api/videos/{job_id}")
def delete_video(job_id: str, request: Request) -> dict[str, object]:
    require_job_access(job_id, request)
    try:
        job = manager.delete_download(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": job["status"], "message": job["message"]}


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
