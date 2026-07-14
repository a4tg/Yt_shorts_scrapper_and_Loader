import csv
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
YOUTUBE_VIDEO_HOSTS = YOUTUBE_HOSTS | {"youtu.be", "www.youtu.be"}
PATH_MARKER = "__YTLOADER_FILE__:"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


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


def overlay_logo_cpu(
    video_path: Path,
    logo_path: Path,
    opacity: int,
    width_percent: int,
    log: Callable[[str], None],
) -> None:
    width = max(16, round(probe_width(video_path) * width_percent / 100))
    alpha = max(0.05, min(opacity / 100, 1.0))
    logo_input = (
        ["-stream_loop", "-1", "-i", str(logo_path)]
        if logo_path.suffix.lower() == ".gif"
        else ["-loop", "1", "-i", str(logo_path)]
    )
    temp_path = video_path.with_name(f"{video_path.stem}.watermark.tmp.mp4")
    command = [
        resolve_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video_path), *logo_input,
        "-filter_complex",
        f"[1:v]scale={width}:-1,format=rgba,colorchannelmixer=aa={alpha:.2f}[logo];"
        "[0:v][logo]overlay=(W-w)/2:H-h-H*0.03:shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264",
        "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
        "-crf", os.getenv("FFMPEG_CRF", "22"), "-c:a", "copy",
        "-movflags", "+faststart", "-shortest", str(temp_path),
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
    max_height: int,
    log: Callable[[str], None],
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
        overlay_logo_cpu(downloaded, logo_path, opacity, width_percent, log)
    return downloaded
