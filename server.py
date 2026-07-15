import base64
import json
import os
import queue
import re
import secrets
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from server_core import (
    BASE_DIR,
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
MAX_OVERLAY_BYTES = max(1, int(os.getenv("YT_LOADER_MAX_OVERLAY_MB", "100"))) * 1024 * 1024


def cleanup_expired_files() -> None:
    retention_hours = max(1, int(os.getenv("YT_LOADER_RETENTION_HOURS", "24")))
    cutoff = time.time() - retention_hours * 3600
    for root in (JOBS_DIR, IMPORTS_DIR, VIDEOS_DIR, LOGOS_DIR):
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


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, object]] = {}
        self.lock = threading.Lock()
        self.tasks: queue.Queue[tuple[str, str, dict[str, object]]] = queue.Queue()
        self._load_jobs()
        threading.Thread(target=self._worker, daemon=True, name="media-worker").start()
        threading.Thread(target=self._expiry_worker, daemon=True, name="video-expiry-worker").start()

    def _load_jobs(self) -> None:
        for path in JOBS_DIR.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("status") in {"queued", "running"}:
                    job.update(status="error", message="Задание прервано перезапуском сервера")
                self.jobs[str(job["id"])] = job
                self._save(job)
            except (OSError, ValueError, KeyError):
                continue

    def _save(self, job: dict[str, object]) -> None:
        (JOBS_DIR / f"{job['id']}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def create(self, kind: str, args: dict[str, object]) -> dict[str, object]:
        job_id = uuid.uuid4().hex
        job: dict[str, object] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "message": "В очереди",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            self.jobs[job_id] = job
            self._save(job)
        self.tasks.put((job_id, kind, args))
        return job

    def update(self, job_id: str, **values: object) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(values)
            self._save(job)

    def get(self, job_id: str) -> dict[str, object]:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return dict(self.jobs[job_id])

    def start_download_timer(self, job_id: str) -> dict[str, object]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("kind") != "download" or job.get("status") != "done":
                raise RuntimeError("Видео ещё не готово")
            if not job.get("delete_at"):
                now = datetime.now(timezone.utc)
                job["downloaded_at"] = now.isoformat()
                job["delete_at"] = (now + timedelta(minutes=AFTER_DOWNLOAD_MINUTES)).isoformat()
                self._save(job)
            return dict(job)

    def authorize_download(self, job_id: str) -> dict[str, object]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("kind") != "download" or job.get("status") != "done":
                raise RuntimeError("Видео ещё не готово")
            job["download_ticket_at"] = datetime.now(timezone.utc).isoformat()
            self._save(job)
            return dict(job)

    def delete_download(self, job_id: str, message: str = "Видео удалено") -> dict[str, object]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("kind") != "download":
                raise RuntimeError("Это не задание видео")
            if job.get("status") in {"queued", "running"}:
                raise RuntimeError("Нельзя удалить видео, пока оно обрабатывается")
            if job.get("status") == "deleted":
                return dict(job)
            shutil.rmtree(VIDEOS_DIR / job_id, ignore_errors=True)
            job.update(status="deleted", message=message, deleted_at=datetime.now(timezone.utc).isoformat())
            self._save(job)
            return dict(job)

    def _expire_downloads(self) -> None:
        now = datetime.now(timezone.utc)
        with self.lock:
            candidates = [
                (str(job_id), str(job.get("delete_at") or job.get("ready_expires_at") or ""))
                for job_id, job in self.jobs.items()
                if job.get("kind") == "download" and job.get("status") == "done"
            ]
        for job_id, expires_at in candidates:
            try:
                if expires_at and datetime.fromisoformat(expires_at) <= now:
                    self.delete_download(job_id, "Срок хранения истёк, видео удалено")
            except (KeyError, RuntimeError, ValueError):
                continue

    def _expiry_worker(self) -> None:
        while True:
            self._expire_downloads()
            time.sleep(5)

    def _log(self, job_id: str, message: str) -> None:
        self.update(job_id, message=message[-500:])

    def _worker(self) -> None:
        while True:
            job_id, kind, args = self.tasks.get()
            self.update(job_id, status="running", message="Выполняется")
            try:
                if kind == "import":
                    count = run_channel_import(
                        str(args["channel_url"]),
                        IMPORTS_DIR / f"{job_id}.json",
                        IMPORTS_DIR / f"{job_id}.csv",
                        int(args["limit"]),
                    )
                    result = {"count": count}
                elif kind == "download":
                    overlay_items = list(args.get("overlays") or [])
                    output_dir = VIDEOS_DIR / job_id
                    log = lambda text: self._log(job_id, text)
                    single_path = (
                        Path(str(dict(overlay_items[0])["path"]))
                        if len(overlay_items) == 1 else None
                    )
                    video_path = download_short(
                        str(args["url"]), output_dir, single_path,
                        int(args["opacity"]), int(args["width_percent"]),
                        int(args["position_x"]), int(args["position_y"]),
                        int(args["max_height"]), log,
                    )
                    overlay_count = len(overlay_items)
                    result = {"filename": video_path.name, "overlay_count": overlay_count}
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
                        )
                        video_path.unlink(missing_ok=True)
                        result = {
                            "filename": archive_path.name,
                            "overlay_count": overlay_count,
                            "folders": folders,
                            "format": "zip",
                        }
                else:
                    raise RuntimeError("Неизвестный тип задания")
                ready_expires_at = (
                    (datetime.now(timezone.utc) + timedelta(hours=READY_HOURS)).isoformat()
                    if kind == "download" else None
                )
                values: dict[str, object] = {"status": "done", "message": "Готово", "result": result}
                if ready_expires_at:
                    values["ready_expires_at"] = ready_expires_at
                self.update(job_id, **values)
            except Exception as exc:
                self.update(job_id, status="error", message=str(exc)[-1000:])
            finally:
                self.tasks.task_done()


manager = JobManager()
app = FastAPI(title="YT Shorts Loader", docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/channels/import", status_code=202)
def import_channel(payload: ChannelRequest) -> dict[str, object]:
    try:
        channel_url = normalize_channel_shorts_url(payload.channel_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return manager.create("import", {"channel_url": channel_url, "limit": payload.limit})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    try:
        job = manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    if job.get("status") == "done":
        if job.get("kind") == "import":
            job["items_url"] = f"/api/imports/{job_id}/items"
            job["csv_url"] = f"/api/imports/{job_id}/metadata.csv"
        elif job.get("kind") == "download":
            job["download_ticket_url"] = f"/api/videos/{job_id}/download-ticket"
            job["delete_url"] = f"/api/videos/{job_id}"
    return job


def load_import(job_id: str) -> list[dict[str, object]]:
    path = IMPORTS_DIR / f"{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "Результат импорта не найден")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/imports/{job_id}/items")
def import_items(job_id: str) -> list[dict[str, object]]:
    return load_import(job_id)


@app.get("/api/imports/{job_id}/metadata.csv")
def import_csv(job_id: str) -> FileResponse:
    path = IMPORTS_DIR / f"{job_id}.csv"
    if not path.is_file():
        raise HTTPException(404, "CSV не найден")
    return FileResponse(path, filename="shorts_metadata.csv", media_type="text/csv")


@app.get("/api/imports/{job_id}/{video_id}/metadata.txt")
def item_metadata(job_id: str, video_id: str) -> PlainTextResponse:
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
async def upload_logo(file: UploadFile) -> dict[str, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if not suffix or len(suffix) > 11 or not suffix[1:].isalnum():
        suffix = ".media"
    content = await file.read(MAX_OVERLAY_BYTES + 1)
    await file.close()
    if len(content) > MAX_OVERLAY_BYTES:
        raise HTTPException(413, f"Оверлей больше {MAX_OVERLAY_BYTES // 1024 // 1024} МБ")
    token = uuid.uuid4().hex
    original_name = Path(file.filename or f"overlay{suffix}").name
    safe_stem = re.sub(r'[^\w.-]+', "_", Path(original_name).stem, flags=re.UNICODE).strip(" ._")
    safe_stem = (safe_stem or "overlay")[:60]
    overlay_path = LOGOS_DIR / f"{token}_{safe_stem}{suffix}"
    overlay_path.write_bytes(content)
    if not is_supported_overlay(overlay_path):
        overlay_path.unlink(missing_ok=True)
        raise HTTPException(
            400,
            "FFmpeg не смог прочитать файл. Выбери изображение или анимацию/видео "
            "в поддерживаемом формате.",
        )
    return {"token": token, "name": original_name}


def resolve_overlay_token(token: str, index: int) -> tuple[Path, str]:
    try:
        uuid.UUID(hex=token)
    except ValueError as exc:
        raise HTTPException(400, "Некорректный оверлей") from exc
    overlay_path = next(
        (path for path in LOGOS_DIR.glob(f"{token}*") if path.is_file()),
        None,
    )
    if not overlay_path:
        raise HTTPException(404, "Оверлей не найден")
    prefix = f"{token}_"
    display_name = overlay_path.name[len(prefix):] if overlay_path.name.startswith(prefix) else f"overlay_{index}{overlay_path.suffix}"
    return overlay_path, display_name


@app.post("/api/videos/download", status_code=202)
def create_download(payload: DownloadRequest) -> dict[str, object]:
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
        overlay_path, display_name = resolve_overlay_token(token, index)
        overlays.append({"path": str(overlay_path), "name": display_name})
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
        },
    )


@app.get("/api/videos/{job_id}/download")
def download_result(job_id: str) -> FileResponse:
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
    background = None if job.get("delete_at") else BackgroundTask(manager.start_download_timer, job_id)
    media_type = "application/zip" if path.suffix.lower() == ".zip" else "video/mp4"
    return FileResponse(path, filename=filename, media_type=media_type, background=background)


@app.post("/api/videos/{job_id}/download-ticket")
def create_download_ticket(job_id: str) -> dict[str, object]:
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
def delete_video(job_id: str) -> dict[str, object]:
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
