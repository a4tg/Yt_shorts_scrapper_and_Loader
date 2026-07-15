import csv
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from media_metadata import metadata_movflags, metadata_output_args, process_video_metadata


BASE_DIR = Path(__file__).resolve().parent
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
YOUTUBE_VIDEO_HOSTS = YOUTUBE_HOSTS | {"youtu.be", "www.youtu.be"}
PATH_MARKER = "__YTLOADER_FILE__:"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
STATIC_OVERLAY_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


def resolve_tool(name: str) -> str:
    configured = os.getenv(f"{name.upper().replace('-', '_')}_PATH")
    candidates = [configured, shutil.which(name)]
    if os.name == "nt":
        candidates.append(str(BASE_DIR / f"{name}.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError(f"Не найден {name}. Установи его и добавь в PATH.")


def youtube_cookies() -> Path | None:
    configured = os.getenv("YOUTUBE_COOKIES")
    path = Path(configured) if configured else BASE_DIR / "cookies" / "www.youtube.com_cookies.txt"
    return path if path.is_file() else None


def normalize_channel_shorts_url(value: str) -> str:
    raw_url = value.strip()
    if not raw_url:
        raise ValueError("Вставь ссылку на YouTube-канал.")
    if "://" not in raw_url:
        raw_url = "https://" + raw_url
    parsed = urlparse(raw_url)
    if (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ValueError("Нужна ссылка на канал youtube.com.")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("В ссылке не найден канал YouTube.")
    if parts[0].startswith("@") and len(parts[0]) > 1:
        channel_parts = parts[:1]
    elif parts[0] in {"channel", "c", "user"} and len(parts) >= 2:
        channel_parts = parts[:2]
    else:
        raise ValueError("Поддерживаются ссылки youtube.com/@канал и youtube.com/channel/UC...")
    return f"https://www.youtube.com/{'/'.join(channel_parts)}/shorts"


def extract_video_id(value: str) -> str:
    raw_url = value.strip()
    if "://" not in raw_url:
        raw_url = "https://" + raw_url
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_VIDEO_HOSTS:
        raise ValueError("Разрешены только ссылки YouTube.")
    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"} and parts:
        video_id = parts[0]
    elif len(parts) == 2 and parts[0] == "shorts":
        video_id = parts[1]
    elif parts == ["watch"]:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("Некорректная ссылка YouTube-видео.")
    return video_id


def normalize_video_url(value: str) -> str:
    return f"https://www.youtube.com/watch?v={extract_video_id(value)}"


def parse_metadata_lines(output: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        video_id = str(raw.get("id") or "").strip()
        if not VIDEO_ID_PATTERN.fullmatch(video_id) or video_id in seen:
            continue
        tags = raw.get("tags")
        seen.add(video_id)
        records.append(
            {
                "id": video_id,
                "url": f"https://www.youtube.com/shorts/{video_id}",
                "title": str(raw.get("title") or "Без названия"),
                "description": str(raw.get("description") or ""),
                "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
                "uploader": str(raw.get("uploader") or ""),
                "upload_date": str(raw.get("upload_date") or ""),
                "duration": raw.get("duration"),
                "thumbnail": str(raw.get("thumbnail") or ""),
            }
        )
    return records


def playlist_limit_args(limit: int) -> list[str]:
    return ["--playlist-end", str(limit)] if limit > 0 else []


def run_channel_import(channel_url: str, json_path: Path, csv_path: Path, limit: int = 50) -> int:
    command = [
        resolve_tool("yt-dlp"),
        "--ignore-config",
        "--ignore-errors",
        "--skip-download",
        "--no-warnings",
        "--no-update",
        "--js-runtimes", "node",
        *playlist_limit_args(limit),
        "--print",
        "%(.{id,title,description,tags,uploader,upload_date,duration,thumbnail})j",
    ]
    cookies = youtube_cookies()
    if cookies:
        command.extend(["--cookies", str(cookies)])
    command.append(normalize_channel_shorts_url(channel_url))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    records = parse_metadata_lines(result.stdout)
    if not records and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "yt-dlp не смог прочитать канал")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["url", "title", "description", "tags", "uploader", "upload_date"],
            delimiter=";",
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "url": record["url"],
                    "title": record["title"],
                    "description": record["description"],
                    "tags": ", ".join(record["tags"]),
                    "uploader": record["uploader"],
                    "upload_date": record["upload_date"],
                }
            )
    return len(records)


def probe_width(video_path: Path) -> int:
    result = subprocess.run(
        [
            resolve_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width", "-of", "default=nokey=1:noprint_wrappers=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe не прочитал видео")
    return int(result.stdout.strip())


def build_overlay_input_args(overlay_path: Path) -> list[str]:
    """Build an FFmpeg input that loops animations/video until the main video ends."""
    if overlay_path.suffix.lower() in STATIC_OVERLAY_SUFFIXES:
        return ["-loop", "1", "-i", str(overlay_path)]
    return ["-stream_loop", "-1", "-i", str(overlay_path)]


def is_supported_overlay(overlay_path: Path) -> bool:
    """Return whether FFprobe recognizes the upload as a visual media stream."""
    try:
        result = subprocess.run(
            [
                resolve_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type",
                "-of", "default=nokey=1:noprint_wrappers=1", str(overlay_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "video"


def overlay_preview_seek_seconds(duration: float) -> float:
    """Choose a representative early frame instead of a commonly blank frame zero."""
    if duration <= 0:
        return 0.0
    return min(duration * 0.2, max(0.0, duration - 0.05))


def probe_media_duration(media_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                resolve_tool("ffprobe"), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
        return max(0.0, float(result.stdout.strip())) if result.returncode == 0 else 0.0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return 0.0


def create_overlay_preview(overlay_path: Path, preview_path: Path) -> None:
    """Render a browser-safe first frame without changing the uploaded overlay."""
    temp_path = preview_path.with_name(f"{preview_path.stem}.tmp.png")
    temp_path.unlink(missing_ok=True)
    seek_seconds = overlay_preview_seek_seconds(probe_media_duration(overlay_path))
    seek_args = ["-ss", f"{seek_seconds:.3f}"] if seek_seconds > 0 else []
    result = subprocess.run(
        [
            resolve_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            *seek_args, "-i", str(overlay_path), "-frames:v", "1",
            "-vf", "scale=720:-2:force_original_aspect_ratio=decrease,format=rgba",
            str(temp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=30,
    )
    try:
        if result.returncode != 0 or not temp_path.is_file():
            raise RuntimeError(result.stderr.strip() or "FFmpeg не создал предпросмотр оверлея")
        temp_path.replace(preview_path)
    finally:
        temp_path.unlink(missing_ok=True)


def build_overlay_filter(
    width: int,
    opacity: int,
    position_x: int = 50,
    position_y: int = 96,
) -> str:
    """Build a resolution-independent overlay using the available free space."""
    alpha = max(0.05, min(opacity / 100, 1.0))
    x_ratio = max(0, min(position_x, 100)) / 100
    y_ratio = max(0, min(position_y, 100)) / 100
    return (
        f"[1:v]scale={max(16, width)}:-1,format=rgba,colorchannelmixer=aa={alpha:.2f}[logo];"
        f"[0:v][logo]overlay=x=(W-w)*{x_ratio:.2f}:y=(H-h)*{y_ratio:.2f}:"
        "shortest=1,format=yuv420p[v]"
    )


def overlay_logo_cpu(
    video_path: Path,
    logo_path: Path,
    opacity: int,
    width_percent: int,
    position_x: int,
    position_y: int,
    log: Callable[[str], None],
    metadata_mode: str = "strip",
) -> None:
    width = max(16, round(probe_width(video_path) * width_percent / 100))
    logo_input = build_overlay_input_args(logo_path)
    temp_path = video_path.with_name(f"{video_path.stem}.watermark.tmp.mp4")
    command = [
        resolve_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video_path), *logo_input,
        "-filter_complex",
        build_overlay_filter(width, opacity, position_x, position_y),
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264",
        "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
        "-crf", os.getenv("FFMPEG_CRF", "22"), "-c:a", "copy",
        *metadata_output_args(metadata_mode, str(video_path)),
        "-movflags", metadata_movflags(metadata_mode), "-shortest", str(temp_path),
    ]
    log("Накладываю логотип на CPU…")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        if result.returncode != 0 or not temp_path.is_file():
            raise RuntimeError(result.stderr.strip() or "FFmpeg завершился с ошибкой")
        temp_path.replace(video_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def create_overlay_archive(
    source_path: Path,
    overlays: list[tuple[Path, str]],
    archive_path: Path,
    opacity: int,
    width_percent: int,
    position_x: int,
    position_y: int,
    log: Callable[[str], None],
    metadata_mode: str = "strip",
) -> list[str]:
    """Create one processed variant per overlay and bundle them into a fast ZIP."""
    variants_dir = source_path.parent / "overlay_variants"
    shutil.rmtree(variants_dir, ignore_errors=True)
    variants_dir.mkdir(parents=True, exist_ok=True)
    folder_names: list[str] = []
    used_names: set[str] = set()
    temp_archive = archive_path.with_suffix(".tmp.zip")
    try:
        for index, (overlay_path, display_name) in enumerate(overlays, start=1):
            base_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(display_name).stem).strip(" .")
            if not base_name:
                base_name = f"overlay_{index}"
            if base_name.upper() in {
                "CON", "PRN", "AUX", "NUL",
                *(f"COM{number}" for number in range(1, 10)),
                *(f"LPT{number}" for number in range(1, 10)),
            }:
                base_name = f"{base_name}_overlay"
            folder_name = base_name
            suffix = 2
            while folder_name.casefold() in used_names:
                folder_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(folder_name.casefold())
            folder_names.append(folder_name)

            variant_dir = variants_dir / folder_name
            variant_dir.mkdir()
            variant_path = variant_dir / source_path.name
            shutil.copy2(source_path, variant_path)
            log(f"Оверлей {index}/{len(overlays)}: {display_name}")
            overlay_logo_cpu(
                variant_path, overlay_path, opacity, width_percent,
                position_x, position_y, log, metadata_mode,
            )

        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_STORED) as archive:
            for folder_name in folder_names:
                variant_path = variants_dir / folder_name / source_path.name
                archive.write(variant_path, arcname=f"{folder_name}/{source_path.name}")
        temp_archive.replace(archive_path)
        return folder_names
    finally:
        shutil.rmtree(variants_dir, ignore_errors=True)
        temp_archive.unlink(missing_ok=True)


def find_downloaded_video(
    output_dir: Path,
    reported_path: Path | None,
    video_id: str,
) -> Path | None:
    if reported_path and reported_path.is_file():
        return reported_path
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".mp4"
        and f"[{video_id}]" in path.name
        and ".tmp." not in path.name
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def download_short(
    url: str,
    output_dir: Path,
    logo_path: Path | None,
    opacity: int,
    width_percent: int,
    position_x: int,
    position_y: int,
    max_height: int,
    log: Callable[[str], None],
    metadata_mode: str = "strip",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_url = normalize_video_url(url)
    video_id = extract_video_id(normalized_url)
    command = [
        resolve_tool("yt-dlp"), "--ignore-config",
        "--js-runtimes", "node",
        "--no-update",
        "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
        "--merge-output-format", "mp4", "--remux-video", "mp4",
        "--print", f"after_move:{PATH_MARKER}%(filepath)s",
        "-P", str(output_dir),
    ]
    cookies = youtube_cookies()
    if cookies:
        command.extend(["--cookies", str(cookies)])
    command.append(normalized_url)
    log("Скачиваю видео…")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    downloaded: Path | None = None
    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        clean = line.strip()
        if clean.startswith(PATH_MARKER):
            downloaded = Path(clean[len(PATH_MARKER):])
        elif clean:
            output_lines.append(clean)
            log(clean[-500:])
    return_code = process.wait()
    downloaded = find_downloaded_video(output_dir, downloaded, video_id)
    if return_code != 0:
        details = "\n".join(output_lines[-8:])
        raise RuntimeError(f"yt-dlp завершился с кодом {return_code}: {details}".strip())
    if not downloaded:
        details = "\n".join(output_lines[-5:])
        raise RuntimeError(
            "Видео скачалось, но итоговый MP4 не найден в папке задания. " + details
        )
    log(f"Видео сохранено: {downloaded.name}")
    if logo_path:
        overlay_logo_cpu(
            downloaded, logo_path, opacity, width_percent,
            position_x, position_y, log, metadata_mode,
        )
    elif metadata_mode != "none" and not process_video_metadata(
        downloaded, resolve_tool("ffmpeg"), metadata_mode, log, CREATE_NO_WINDOW,
    ):
        raise RuntimeError("FFmpeg не смог обработать метаданные видео")
    return downloaded
