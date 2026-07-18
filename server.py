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
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.background import BackgroundTask
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from auth_routes import router as auth_router
from admin_routes import router as admin_router
from billing_routes import router as billing_router
from content_routes import CONTENT_DIR, router as content_router
from asset_review_routes import router as asset_review_router
from approval_routes import router as approval_router
from graph_routes import router as graph_router
from decision_routes import router as decision_router
from messaging_routes import router as messaging_router
from payment_routes import router as payment_router
from beta_routes import router as beta_router
from legal_routes import router as legal_router
from workspace_routes import router as workspace_router
from auth_service import (
    PUBLIC_API_PATHS,
    SAFE_METHODS,
    authenticate_request,
    csrf_is_valid,
    origin_is_allowed,
)
from billing_service import (
    InsufficientCreditsError,
    PlanLimitError,
    SubscriptionRequiredError,
    require_entitlement,
    require_plan_capacity,
)
from ai_service import (
    AIServiceError,
    ai_public_config,
    generate_image,
    generate_text,
    render_vertical_clips,
    select_highlights,
    transcribe_media,
)
from observability import log_request, metrics, prometheus_text, request_id
from database import SessionLocal, check_database
from email_service import email_verification_required
from job_queue import DatabaseJobManager, ProcessedJob
from payment_service import SubscriptionRenewalWorker
from saas_models import ContentAttachment, ContentItem, Overlay, Project, WorkspaceMember
from workspace_service import has_role, membership_for, project_membership
from yookassa_client import YooKassaClient
from server_core import (
    BASE_DIR,
    create_overlay_preview,
    create_overlay_archive,
    download_short,
    is_supported_overlay,
    normalize_channel_shorts_url,
    normalize_source_import_url,
    normalize_source_video_url,
    probe_source_video,
    run_source_import,
)


class SelectiveGZipMiddleware:
    """Compress text payloads while leaving already-compressed media untouched."""

    _COMPRESSED_SUFFIXES = {
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp4",
        ".png",
        ".webm",
        ".webp",
    }

    def __init__(self, app, minimum_size: int = 1024, compresslevel: int = 5) -> None:
        self.app = app
        self.gzip_app = GZipMiddleware(
            app,
            minimum_size=minimum_size,
            compresslevel=compresslevel,
        )

    async def __call__(self, scope, receive, send) -> None:
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if Path(path).suffix.lower() in self._COMPRESSED_SUFFIXES:
            await self.app(scope, receive, send)
            return
        await self.gzip_app(scope, receive, send)


DATA_DIR = Path(os.getenv("YT_LOADER_DATA_DIR", BASE_DIR / "server_data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
IMPORTS_DIR = DATA_DIR / "imports"
VIDEOS_DIR = DATA_DIR / "videos"
LOGOS_DIR = DATA_DIR / "logos"
AI_DIR = DATA_DIR / "ai"
WEB_DIR = BASE_DIR / "web"
for directory in (JOBS_DIR, IMPORTS_DIR, VIDEOS_DIR, LOGOS_DIR, AI_DIR):
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
    project_id: str | None = Field(default=None, max_length=36)


class SourceImportRequest(BaseModel):
    source_url: str = Field(min_length=5, max_length=1000)
    platform: Literal["auto", "youtube", "vk", "rutube"] = "auto"
    limit: int = Field(default=50, ge=0, le=1000)
    project_id: str | None = Field(default=None, max_length=36)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=10, max_length=500)
    project_id: str | None = Field(default=None, max_length=36)
    channel_name: str | None = Field(default=None, max_length=200)
    video_title: str | None = Field(default=None, max_length=300)
    logo_token: str | None = None
    logo_tokens: list[str] = Field(default_factory=list, max_length=10)
    opacity: int = Field(default=35, ge=5, le=100)
    width_percent: int = Field(default=22, ge=5, le=100)
    position_x: int = Field(default=50, ge=0, le=100)
    position_y: int = Field(default=96, ge=0, le=100)
    max_height: int = Field(default=1080)
    metadata_mode: Literal["none", "strip", "synthetic"] = "strip"


class DownloadBatchRequest(BaseModel):
    items: list[DownloadRequest] = Field(min_length=1, max_length=20)


class JobStatusesRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=20)


class AITextRequest(BaseModel):
    project_id: str = Field(min_length=36, max_length=36)
    action: Literal["post", "ideas", "rewrite", "shorten", "titles", "tags"] = "post"
    prompt: str = Field(min_length=3, max_length=30000)
    context: str | None = Field(default=None, max_length=30000)


class AIImageRequest(BaseModel):
    project_id: str = Field(min_length=36, max_length=36)
    prompt: str = Field(min_length=3, max_length=8000)
    size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024"


class AIClipsRequest(BaseModel):
    project_id: str = Field(min_length=36, max_length=36)
    attachment_id: str = Field(min_length=36, max_length=36)
    count: int = Field(default=3, ge=1, le=5)
    min_seconds: int = Field(default=20, ge=10, le=120)
    max_seconds: int = Field(default=60, ge=15, le=180)


def process_media_job(
    job_id: str,
    kind: str,
    args: dict[str, object],
    log,
) -> ProcessedJob:
    if kind == "ai_text":
        action = str(args.get("action") or "post")
        instructions = {
            "post": "Ты senior-маркетолог. Создай готовый к публикации пост на русском языке.",
            "ideas": "Ты креативный стратег. Предложи конкретные идеи контента с сильными заходами.",
            "rewrite": "Ты редактор. Перепиши текст яснее, живее и убедительнее, сохранив смысл.",
            "shorten": "Ты редактор. Сократи текст без потери ключевого смысла и фактов.",
            "titles": "Ты редактор. Предложи сильные заголовки и коротко объясни лучшие варианты.",
            "tags": "Ты SMM-редактор. Предложи релевантные теги без спама.",
        }[action]
        prompt = str(args["prompt"])
        if args.get("context"):
            prompt += "\n\nКонтекст проекта:\n" + str(args["context"])
        log("Генерирую текст")
        result = generate_text(prompt, instructions)
        return ProcessedJob(result=result, expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    if kind == "ai_image":
        output_dir = VIDEOS_DIR / job_id
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        log("Генерирую изображение")
        image, metadata = generate_image(str(args["prompt"]), size=str(args["size"]))
        target = output_dir / "ai_image.png"
        target.write_bytes(image)
        return ProcessedJob(
            result={**metadata, "filename": target.name, "format": "png"}, files=(target,),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=READY_HOURS),
        )
    if kind == "ai_clips":
        source = Path(str(args["source_path"])).resolve()
        if not source.is_file():
            raise AIServiceError("Исходное видео отсутствует в медиатеке.")
        output_dir = VIDEOS_DIR / job_id
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcribe_media(source, output_dir, log)
        log("Выбираю сильные фрагменты")
        clips = select_highlights(transcript, int(args["count"]), int(args["min_seconds"]), int(args["max_seconds"]))
        archive = render_vertical_clips(source, clips, output_dir, log)
        return ProcessedJob(
            result={"filename": archive.name, "format": "zip", "count": len(clips), "clips": clips},
            files=(archive,), expires_at=datetime.now(timezone.utc) + timedelta(hours=READY_HOURS),
        )
    if kind == "import":
        json_path = IMPORTS_DIR / f"{job_id}.json"
        csv_path = IMPORTS_DIR / f"{job_id}.csv"
        count, platform = run_source_import(
            str(args.get("source_url") or args["channel_url"]),
            json_path,
            csv_path,
            int(args["limit"]),
            str(args.get("platform") or "youtube"),
            progress=log,
        )
        return ProcessedJob(
            result={"count": count, "platform": platform},
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
    title="All As Planned",
    docs_url="/api/docs" if api_docs_enabled else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if api_docs_enabled else None,
    lifespan=application_lifespan,
)
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024, compresslevel=5)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_request: Request, exc: RequestValidationError):
    """Return validation details without reflecting malformed input bytes.

    FastAPI's default handler includes the rejected value in the response. A
    JSON string containing an unpaired surrogate cannot then be encoded as
    UTF-8, turning a client-side 422 into a server-side 500.
    """
    metrics.increment("request_validation_errors_total")
    details = []
    for error in exc.errors():
        details.append({
            "type": str(error.get("type") or "validation_error"),
            "loc": [
                part if isinstance(part, int) else str(part).encode(
                    "utf-8", errors="replace"
                ).decode("utf-8")
                for part in error.get("loc", ())
            ],
            "msg": str(error.get("msg") or "Invalid request data").encode(
                "utf-8", errors="replace"
            ).decode("utf-8"),
        })
    return JSONResponse({"detail": details}, status_code=422)


@app.exception_handler(SubscriptionRequiredError)
async def subscription_required_handler(_request: Request, exc: SubscriptionRequiredError):
    return JSONResponse({"detail": str(exc)}, status_code=402)


@app.exception_handler(PlanLimitError)
async def plan_limit_handler(_request: Request, exc: PlanLimitError):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    trace_id = request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = trace_id
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = trace_id
        return response
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        metrics.increment("http_requests_total")
        metrics.increment(f"http_responses_{status_code // 100}xx_total")
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        user = getattr(request.state, "user", None)
        if request.url.path not in {"/api/health", "/api/health/live"}:
            log_request(
                request_id=trace_id, method=request.method, path=route_path,
                status=status_code, duration_ms=duration_ms,
                user_id=str(user.id) if user else None,
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
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(content_router)
app.include_router(asset_review_router)
app.include_router(approval_router)
app.include_router(graph_router)
app.include_router(decision_router)
app.include_router(messaging_router)
app.include_router(payment_router)
app.include_router(beta_router)
app.include_router(legal_router)
app.include_router(workspace_router)


@app.middleware("http")
async def browser_security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = (
        "SAMEORIGIN" if path.startswith("/api/content-attachments/") and path.endswith("/preview") else "DENY"
    )
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path.startswith("/assets/"):
        if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}:
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
        else:
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    elif path in {
        "/", "/app", "/app/", "/privacy", "/terms", "/offer",
        "/personal-data-consent", "/refund-policy", "/storage-policy",
    }:
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    if path != "/api/docs":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://i.ytimg.com; media-src 'self' blob:; "
            "frame-src 'self' https://www.youtube-nocookie.com; connect-src 'self'"
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


@app.get("/api/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/ready")
def readiness(response: Response) -> dict[str, object]:
    database_status = "ok" if check_database() else "error"
    workers_status = "ok" if manager.healthy() and renewal_worker.healthy() else "error"
    disk = shutil.disk_usage(DATA_DIR)
    minimum_free_mb = max(256, int(os.getenv("YT_LOADER_MIN_FREE_DISK_MB", "2048")))
    free_mb = disk.free // 1024 // 1024
    disk_status = "ok" if free_mb >= minimum_free_mb else "error"
    try:
        queue = manager.queue_counts()
    except SQLAlchemyError:
        queue = {}
        database_status = "error"
    ready = all(value == "ok" for value in (database_status, workers_status, disk_status))
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "degraded", "database": database_status,
        "workers": workers_status, "disk": disk_status, "disk_free_mb": free_mb,
        "queue": queue,
    }


@app.get("/api/metrics", response_class=PlainTextResponse)
def application_metrics(request: Request) -> PlainTextResponse:
    expected = os.getenv("YT_LOADER_METRICS_TOKEN", "").strip()
    supplied = request.headers.get("Authorization", "")
    if not expected or not secrets.compare_digest(supplied, f"Bearer {expected}"):
        raise HTTPException(404, "Not found")
    try:
        counts = manager.queue_counts()
    except SQLAlchemyError:
        counts = {}
    extra = {f"jobs_{status}": count for status, count in counts.items()}
    return PlainTextResponse(prometheus_text(extra), media_type="text/plain; version=0.0.4")


def resolve_media_project(project_id: str | None, request: Request) -> tuple[str | None, str | None]:
    """Resolve and authorize the project used by an import or render job."""
    if not project_id:
        return None, None
    with SessionLocal() as db:
        access = project_membership(db, project_id, str(request.state.user.id))
        if access is None:
            raise HTTPException(404, "Проект не найден")
        project, member = access
        if not has_role(member, "editor"):
            raise HTTPException(403, "Для создания медиазадач нужна роль редактора")
        return project.workspace_id, project.id


def resolve_overlay_project(project_id: str | None, request: Request) -> str:
    """Resolve the permanent project library used for a reusable overlay."""
    if project_id:
        _, resolved_project_id = resolve_media_project(project_id, request)
        if resolved_project_id:
            return resolved_project_id
    user_id = str(request.state.user.id)
    with SessionLocal() as db:
        rows = db.execute(
            select(Project, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Project.workspace_id)
            .where(
                WorkspaceMember.user_id == user_id,
                Project.status == "active",
            )
            .order_by(Project.created_at, Project.id)
        ).all()
        for project, member in rows:
            if has_role(member, "editor"):
                return project.id
    raise HTTPException(
        409,
        "Создайте проект с правами редактора, чтобы сохранить оверлей в медиатеке.",
    )


@app.get("/api/ai/config")
def ai_config() -> dict[str, object]:
    return ai_public_config()


@app.post("/api/ai/text", status_code=202)
def create_ai_text(payload: AITextRequest, request: Request) -> dict[str, object]:
    workspace_id, project_id = resolve_media_project(payload.project_id, request)
    try:
        return manager.create(
            "ai_text",
            {"action": payload.action, "prompt": payload.prompt, "context": payload.context},
            owner_id=str(request.state.user.id), workspace_id=workspace_id, project_id=project_id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.post("/api/ai/images", status_code=202)
def create_ai_image(payload: AIImageRequest, request: Request) -> dict[str, object]:
    workspace_id, project_id = resolve_media_project(payload.project_id, request)
    try:
        return manager.create(
            "ai_image", {"prompt": payload.prompt, "size": payload.size},
            owner_id=str(request.state.user.id), workspace_id=workspace_id, project_id=project_id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.post("/api/ai/clips", status_code=202)
def create_ai_clips(payload: AIClipsRequest, request: Request) -> dict[str, object]:
    if payload.min_seconds >= payload.max_seconds:
        raise HTTPException(400, "Максимальная длительность должна быть больше минимальной.")
    workspace_id, project_id = resolve_media_project(payload.project_id, request)
    with SessionLocal() as db:
        require_plan_capacity(db, str(request.state.user.id), "clips_per_job", 0, increment=payload.count)
        attachment = db.get(ContentAttachment, payload.attachment_id)
        if attachment is None or attachment.project_id != project_id:
            raise HTTPException(404, "Видео не найдено в медиатеке проекта.")
        source_path = Path(attachment.storage_path).resolve()
        content_root = CONTENT_DIR.resolve()
        if not source_path.is_file() or not source_path.is_relative_to(content_root):
            raise HTTPException(404, "Файл видео отсутствует в хранилище.")
        if not (attachment.mime_type or "").startswith("video/"):
            raise HTTPException(400, "Для нарезки выберите видеофайл.")
    try:
        return manager.create(
            "ai_clips",
            {
                "source_path": str(source_path), "attachment_id": payload.attachment_id,
                "count": payload.count, "min_seconds": payload.min_seconds,
                "max_seconds": payload.max_seconds,
            },
            owner_id=str(request.state.user.id), workspace_id=workspace_id, project_id=project_id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.post("/api/channels/import", status_code=202)
def import_channel(payload: ChannelRequest, request: Request) -> dict[str, object]:
    try:
        channel_url = normalize_channel_shorts_url(payload.channel_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    workspace_id, project_id = resolve_media_project(payload.project_id, request)
    try:
        return manager.create(
            "import",
            {"channel_url": channel_url, "limit": payload.limit},
            owner_id=str(request.state.user.id),
            workspace_id=workspace_id,
            project_id=project_id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.post("/api/sources/import", status_code=202)
def import_source(payload: SourceImportRequest, request: Request) -> dict[str, object]:
    try:
        source_url, platform = normalize_source_import_url(payload.source_url, payload.platform)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    workspace_id, project_id = resolve_media_project(payload.project_id, request)
    try:
        return manager.create(
            "import",
            {"source_url": source_url, "platform": platform, "limit": payload.limit},
            owner_id=str(request.state.user.id),
            workspace_id=workspace_id,
            project_id=project_id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.get("/api/sources/preview")
def source_preview(url: str) -> dict[str, object]:
    if not 10 <= len(url) <= 1000:
        raise HTTPException(400, "Некорректная длина ссылки")
    try:
        return probe_source_video(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/sources/thumbnail")
def source_thumbnail(url: str) -> Response:
    """Proxy thumbnails from a strict CDN allowlist so CSP stays self-only."""
    if not 10 <= len(url) <= 2000:
        raise HTTPException(400, "Некорректная ссылка изображения")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed_suffixes = (".ytimg.com", ".userapi.com", ".rutubelist.ru", ".rutube.ru")
    if parsed.scheme != "https" or not any(host == suffix[1:] or host.endswith(suffix) for suffix in allowed_suffixes):
        raise HTTPException(400, "Домен изображения не разрешён")
    try:
        with httpx.stream("GET", url, timeout=12, follow_redirects=False) as upstream:
            if upstream.status_code != 200:
                raise HTTPException(404, "Изображение источника недоступно")
            media_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
            if not media_type.startswith("image/"):
                raise HTTPException(415, "Источник вернул не изображение")
            chunks: list[bytes] = []
            size = 0
            for chunk in upstream.iter_bytes():
                size += len(chunk)
                if size > 5 * 1024 * 1024:
                    raise HTTPException(413, "Изображение слишком большое")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Не удалось загрузить изображение источника") from exc
    return Response(
        content=b"".join(chunks), media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


def require_job_access(job_id: str, request: Request) -> dict[str, object]:
    """Return a job to its owner, workspace members, or an administrator."""
    try:
        job = manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    user = request.state.user
    # Jobs created before user accounts existed intentionally have no owner.
    # Only administrators may recover those legacy results.
    if str(job.get("owner_id") or "") != str(user.id) and not user.is_admin:
        workspace_id = str(job.get("workspace_id") or "")
        with SessionLocal() as db:
            membership = membership_for(db, workspace_id, str(user.id)) if workspace_id else None
        if membership is not None:
            return job
        raise HTTPException(404, "Задание не найдено")
    return job


def decorate_job_urls(job: dict[str, object]) -> dict[str, object]:
    job_id = str(job["id"])
    if job.get("kind") == "import" and job.get("status") in {"running", "done"}:
        job["items_url"] = f"/api/imports/{job_id}/items"
    if job.get("status") == "done":
        if job.get("kind") == "import":
            job["csv_url"] = f"/api/imports/{job_id}/metadata.csv"
        elif job.get("kind") in {"download", "ai_image", "ai_clips"}:
            job["download_ticket_url"] = f"/api/videos/{job_id}/download-ticket"
            job["delete_url"] = f"/api/videos/{job_id}"
    return job


@app.post("/api/jobs/statuses")
def get_job_statuses(
    payload: JobStatusesRequest,
    request: Request,
) -> list[dict[str, object]]:
    unique_ids = list(dict.fromkeys(payload.ids))
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id) for job_id in unique_ids):
        raise HTTPException(400, "Некорректный ID задания")
    return [
        decorate_job_urls(require_job_access(job_id, request))
        for job_id in unique_ids
    ]


@app.get("/api/jobs")
def list_jobs(
    request: Request,
    kind: Literal["import", "download", "ai_text", "ai_image", "ai_clips"] | None = None,
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit должен быть от 1 до 200")
    user = request.state.user
    with SessionLocal() as db:
        workspace_ids = list(
            db.scalars(
                select(WorkspaceMember.workspace_id).where(
                    WorkspaceMember.user_id == str(user.id)
                )
            ).all()
        )
        if project_id and project_membership(db, project_id, str(user.id)) is None and not user.is_admin:
            raise HTTPException(404, "Проект не найден")
    return [
        decorate_job_urls(job)
        for job in manager.list_for_user(
            str(user.id), is_admin=bool(user.is_admin), workspace_ids=workspace_ids,
            project_id=project_id, kind=kind, limit=limit
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
def import_items(
    job_id: str,
    request: Request,
    page: int | None = None,
    page_size: int = 12,
) -> list[dict[str, object]] | dict[str, object]:
    require_job_access(job_id, request)
    items = load_import(job_id)
    # Calls without pagination remain backward-compatible with the desktop app.
    if page is None:
        return items
    if page < 1:
        raise HTTPException(400, "page должен быть не меньше 1")
    if not 1 <= page_size <= 48:
        raise HTTPException(400, "page_size должен быть от 1 до 48")
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    return {
        "items": items[offset : offset + page_size],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_previous": page > 1,
            "has_next": page < pages,
        },
    }


@app.get("/api/imports/{job_id}/metadata.csv")
def import_csv(job_id: str, request: Request) -> FileResponse:
    require_job_access(job_id, request)
    path = IMPORTS_DIR / f"{job_id}.csv"
    if not path.is_file():
        raise HTTPException(404, "CSV не найден")
    return FileResponse(path, filename="source_metadata.csv", media_type="text/csv")


@app.get("/api/imports/{job_id}/{video_id}/metadata.txt")
def item_metadata(job_id: str, video_id: str, request: Request) -> PlainTextResponse:
    require_job_access(job_id, request)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", video_id):
        raise HTTPException(400, "Некорректный ID")
    item = next((item for item in load_import(job_id) if item.get("id") == video_id), None)
    if not item:
        raise HTTPException(404, "Видео не найдено")
    text = (
        f"Название: {item['title']}\n"
        f"Ссылка: {item['url']}\n"
        f"Канал: {item['uploader']}\n"
        f"Дата публикации: {item.get('published_at') or item.get('upload_date') or 'неизвестно'}\n"
        f"Просмотры: {item.get('view_count') if item.get('view_count') is not None else 'неизвестно'}\n\n"
        f"Теги:\n{', '.join(item['tags'])}\n\n"
        f"Описание:\n{item['description']}\n"
    )
    headers = {"Content-Disposition": f'attachment; filename="{video_id}_metadata.txt"'}
    return PlainTextResponse(text, headers=headers, media_type="text/plain; charset=utf-8")


@app.post("/api/logos")
async def upload_logo(
    request: Request,
    file: UploadFile,
    project_id: str | None = Form(default=None),
) -> dict[str, object]:
    resolved_project_id = resolve_overlay_project(project_id, request)
    with SessionLocal() as db:
        entitlement = require_entitlement(db, str(request.state.user.id))
        storage_limit = entitlement.limits.get("storage_mb", 0) * 1024 * 1024
        used_storage = db.scalar(
            select(func.coalesce(func.sum(ContentAttachment.size_bytes), 0)).where(
                ContentAttachment.uploaded_by_user_id == str(request.state.user.id)
            )
        ) or 0
    suffix = Path(file.filename or "").suffix.lower()
    if not suffix or len(suffix) > 11 or not suffix[1:].isalnum():
        suffix = ".media"
    token = uuid.uuid4().hex
    original_name = Path(file.filename or f"overlay{suffix}").name
    safe_stem = re.sub(r'[^\w.-]+', "_", Path(original_name).stem, flags=re.UNICODE).strip(" ._")
    safe_stem = (safe_stem or "overlay")[:60]
    project_files_dir = CONTENT_DIR / resolved_project_id / "files"
    project_files_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = project_files_dir / f"{token}_{safe_stem}{suffix}"
    preview_path = project_files_dir / f"{token}_preview.png"
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
                if storage_limit and int(used_storage) + size_bytes > storage_limit:
                    raise HTTPException(
                        413,
                        "Оверлей не помещается в лимит хранилища текущего тарифа.",
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
            overlay = Overlay(
                id=token,
                user_id=str(request.state.user.id),
                original_name=original_name,
                storage_path=str(overlay_path.resolve()),
                mime_type=file.content_type,
                size_bytes=size_bytes,
            )
            attachment = ContentAttachment(
                project_id=resolved_project_id,
                uploaded_by_user_id=str(request.state.user.id),
                original_name=original_name,
                storage_path=str(overlay_path.resolve()),
                mime_type=file.content_type,
                source_type="overlay",
                size_bytes=size_bytes,
                asset_key=token,
            )
            db.add_all((overlay, attachment))
            db.flush()
            attachment_id = attachment.id
            db.commit()
    except SQLAlchemyError as exc:
        overlay_path.unlink(missing_ok=True)
        preview_path.unlink(missing_ok=True)
        raise HTTPException(503, "Не удалось сохранить оверлей в базе данных") from exc
    return {
        "token": token,
        "name": original_name,
        "preview_url": f"/api/logos/{token}/preview",
        "library_attachment_id": attachment_id,
        "project_id": resolved_project_id,
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
    content_directory = CONTENT_DIR.resolve()
    if (
        overlay_path is None
        or not overlay_path.is_file()
        or not (
            overlay_path.is_relative_to(owner_directory)
            or overlay_path.is_relative_to(content_directory)
        )
    ):
        raise HTTPException(404, "Оверлей не найден")
    display_name = str(overlay.original_name or f"overlay_{index}{overlay_path.suffix}")
    return overlay_path, display_name


@app.get("/api/logos/{token}/preview")
def overlay_preview(token: str, request: Request) -> FileResponse:
    overlay_path, _ = resolve_overlay_token(token, 1, str(request.state.user.id))
    preview_path = overlay_path.with_name(f"{token}_preview.png")
    owner_directory = (LOGOS_DIR / str(request.state.user.id)).resolve()
    content_directory = CONTENT_DIR.resolve()
    if (
        not preview_path.is_file()
        or not (
            preview_path.resolve().is_relative_to(owner_directory)
            or preview_path.resolve().is_relative_to(content_directory)
        )
    ):
        raise HTTPException(404, "Предпросмотр оверлея не найден")
    return FileResponse(
        preview_path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


def prepare_download_job(
    payload: DownloadRequest,
    request: Request,
) -> tuple[str, str, dict[str, object]]:
    try:
        url, _platform = normalize_source_video_url(payload.url)
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
    workspace_id, project_id = resolve_media_project(payload.project_id, request)
    return workspace_id, project_id, {
        "url": url,
        "channel_name": (payload.channel_name or "").strip(),
        "video_title": (payload.video_title or "").strip(),
        "overlays": overlays,
        "opacity": payload.opacity,
        "width_percent": payload.width_percent,
        "position_x": payload.position_x,
        "position_y": payload.position_y,
        "max_height": payload.max_height,
        "metadata_mode": payload.metadata_mode,
    }


@app.post("/api/videos/download", status_code=202)
def create_download(payload: DownloadRequest, request: Request) -> dict[str, object]:
    workspace_id, project_id, args = prepare_download_job(payload, request)
    try:
        batch = manager.create_batch(
            "download",
            [args],
            owner_id=str(request.state.user.id),
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return dict(batch["jobs"][0])
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc


@app.post("/api/videos/download/batch", status_code=202)
def create_download_batch(
    payload: DownloadBatchRequest,
    request: Request,
) -> dict[str, object]:
    prepared = [prepare_download_job(item, request) for item in payload.items]
    project_ids = {item[1] for item in prepared}
    workspace_ids = {item[0] for item in prepared}
    if len(project_ids) != 1 or len(workspace_ids) != 1:
        raise HTTPException(400, "Все ролики пакета должны относиться к одному проекту")
    try:
        return manager.create_batch(
            "download",
            [item[2] for item in prepared],
            owner_id=str(request.state.user.id),
            workspace_id=prepared[0][0],
            project_id=prepared[0][1],
            maximum=20,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(402, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/videos/library")
def video_library(request: Request) -> list[dict[str, object]]:
    """List the completed download files still stored for the current profile."""
    stored: list[dict[str, object]] = []
    for job in manager.list_stored_downloads_for_owner(str(request.state.user.id)):
        job_id = str(job["id"])
        filename = str(dict(job.get("result") or {}).get("filename") or "")
        job_directory = (VIDEOS_DIR / job_id).resolve()
        path = (job_directory / filename).resolve() if filename else job_directory
        if not filename or path.parent != job_directory or not path.is_file():
            continue
        item = decorate_job_urls(job)
        item["stored_filename"] = filename
        item["stored_size_bytes"] = path.stat().st_size
        item["channel_name"] = str(item.get("channel_name") or "").strip() or "Без канала"
        item["video_title"] = str(item.get("video_title") or "").strip() or filename
        stored.append(item)
    return stored


@app.get("/api/videos/{job_id}/download")
def download_result(job_id: str, request: Request) -> FileResponse:
    require_job_access(job_id, request)
    try:
        job = manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    if job.get("status") == "deleted":
        raise HTTPException(410, "Видео уже удалено")
    if job.get("kind") not in {"download", "ai_image", "ai_clips"} or job.get("status") != "done":
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


@app.get("/app", response_class=FileResponse)
@app.get("/app/", response_class=FileResponse)
def application_index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/", response_class=FileResponse)
def landing() -> FileResponse:
    return FileResponse(WEB_DIR / "landing.html")


@app.get("/favicon.ico", response_class=FileResponse)
def favicon() -> FileResponse:
    return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")
