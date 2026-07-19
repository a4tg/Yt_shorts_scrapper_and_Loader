from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree


MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TABLE_ROWS = 300
MAX_TABLE_COLUMNS = 80
VIDEO_PROXY_EXTENSIONS = {".m4v", ".mov", ".webm", ".mkv", ".avi"}
VIDEO_PROXY_MAX_WIDTH = max(320, int(os.getenv("YT_LOADER_PREVIEW_MAX_WIDTH", "1920")))
VIDEO_PROXY_CRF = min(35, max(18, int(os.getenv("YT_LOADER_PREVIEW_CRF", "24"))))
VIDEO_PROXY_PRESET = os.getenv("YT_LOADER_PREVIEW_PRESET", "veryfast").strip() or "veryfast"
_VIDEO_PROXY_LOCKS: dict[str, threading.Lock] = {}
_VIDEO_PROXY_LOCKS_GUARD = threading.Lock()
_VIDEO_PROXY_TRANSCODE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".pdf",
}
DATA_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".srt", ".vtt", ".rtf",
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
}


class PreviewError(ValueError):
    pass


def needs_video_proxy(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_PROXY_EXTENSIONS


def video_proxy_path(source: Path) -> Path:
    return source.with_name(f"{source.name}.browser.mp4")


def _proxy_lock(target: Path) -> threading.Lock:
    key = str(target.resolve())
    with _VIDEO_PROXY_LOCKS_GUARD:
        return _VIDEO_PROXY_LOCKS.setdefault(key, threading.Lock())


def ensure_video_proxy(source: Path, name: str) -> Path:
    """Return a browser-safe video, transcoding non-portable containers once."""
    if not needs_video_proxy(name):
        return source
    target = video_proxy_path(source)
    if target.is_file() and target.stat().st_size > 0:
        return target
    with _proxy_lock(target):
        if target.is_file() and target.stat().st_size > 0:
            return target
        with _VIDEO_PROXY_TRANSCODE_LOCK:
            if target.is_file() and target.stat().st_size > 0:
                return target
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise PreviewError("FFmpeg недоступен: браузерное превью видео пока не подготовлено.")
            temporary = target.with_name(f"{target.name}.part.mp4")
            temporary.unlink(missing_ok=True)
            command = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", (
                    f"scale='min({VIDEO_PROXY_MAX_WIDTH},iw)':-2:"
                    "force_original_aspect_ratio=decrease,format=yuv420p"
                ),
                "-c:v", "libx264", "-preset", VIDEO_PROXY_PRESET,
                "-crf", str(VIDEO_PROXY_CRF),
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-sn", "-dn",
                str(temporary),
            ]
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True, check=False, timeout=60 * 60,
                )
                if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                    detail = (completed.stderr or "").strip().splitlines()
                    logger.warning(
                        "Video preview generation failed for %s: %s",
                        source, detail[-1] if detail else f"ffmpeg code {completed.returncode}",
                    )
                    raise PreviewError("Не удалось подготовить H.264-превью этого видео.")
                temporary.replace(target)
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("Video preview generation failed for %s", source, exc_info=True)
                raise PreviewError("Не удалось подготовить H.264-превью этого видео.") from exc
            finally:
                temporary.unlink(missing_ok=True)
        return target


def prepare_video_proxy(source: Path, name: str) -> None:
    """Best-effort background preparation; preview endpoint can retry later."""
    try:
        ensure_video_proxy(source, name)
    except PreviewError:
        logger.warning("Background video preview is unavailable for %s", source)


def preview_kind(name: str) -> str:
    extension = Path(name).suffix.lower()
    if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        return "image"
    if extension in {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}:
        return "video"
    if extension in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
        return "audio"
    if extension == ".pdf":
        return "pdf"
    if extension in {".csv", ".tsv", ".xlsx", ".ods"}:
        return "table"
    if extension in DATA_EXTENSIONS:
        return "text"
    return "unsupported"


def preview_capabilities(name: str) -> dict[str, object]:
    kind = preview_kind(name)
    return {
        "kind": kind,
        "can_preview": kind != "unsupported",
        "inline_url": kind in {"image", "video", "audio", "pdf"},
        "data_url": kind in {"text", "table"},
    }


def _read_limited(path: Path) -> tuple[bytes, bool]:
    with path.open("rb") as source:
        value = source.read(MAX_PREVIEW_BYTES + 1)
    return value[:MAX_PREVIEW_BYTES], len(value) > MAX_PREVIEW_BYTES


def _decode_text(value: bytes) -> str:
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16", errors="replace")
    return value.decode("utf-8-sig", errors="replace")


def _table_payload(rows: list[list[str]], *, truncated: bool = False, label: str | None = None) -> dict[str, object]:
    normalized = [[str(cell)[:20_000] for cell in row[:MAX_TABLE_COLUMNS]] for row in rows[:MAX_TABLE_ROWS]]
    width = max((len(row) for row in normalized), default=0)
    source_columns = normalized[0] if normalized else []
    data = normalized[1:] if normalized else []
    columns: list[str] = []
    used_columns: set[str] = set()
    for index, source_column in enumerate(source_columns):
        column = source_column.strip() or f"Столбец {index + 1}"
        if column in used_columns:
            column = f"{column} ({index + 1})"
        used_columns.add(column)
        columns.append(column)
    for index in range(len(columns), width):
        column = f"Столбец {index + 1}"
        while column in used_columns:
            column += " (доп.)"
        used_columns.add(column)
        columns.append(column)
    data = [row + [""] * (width - len(row)) for row in data]
    return {
        "kind": "table", "columns": columns, "rows": data,
        "truncated": truncated or len(rows) > MAX_TABLE_ROWS or any(len(row) > MAX_TABLE_COLUMNS for row in rows),
        "label": label,
    }


def _archive_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise PreviewError("В документе нет данных для предварительного просмотра.") from exc
    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise PreviewError("Предварительный просмотр этой части документа слишком велик.")
    with archive.open(info) as source:
        value = source.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
    if len(value) > MAX_ARCHIVE_MEMBER_BYTES:
        raise PreviewError("Предварительный просмотр этой части документа слишком велик.")
    return value


def _xml_text(value: bytes, paragraph_tags: set[str]) -> str:
    root = ElementTree.fromstring(value)
    lines: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in paragraph_tags:
            text = "".join(child.text or "" for child in node.iter() if child.tag.rsplit("}", 1)[-1] in {"t", "span"}).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _docx(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        text = _xml_text(_archive_member(archive, "word/document.xml"), {"p"})
    return {"kind": "text", "text": text, "truncated": False, "label": "Документ Word"}


def _xlsx(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(_archive_member(archive, "xl/sharedStrings.xml"))
            for item in root:
                shared.append("".join(node.text or "" for node in item.iter() if node.tag.rsplit("}", 1)[-1] == "t"))
        sheet_names = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        if not sheet_names:
            raise PreviewError("В книге нет листов для предварительного просмотра.")
        root = ElementTree.fromstring(_archive_member(archive, sheet_names[0]))
        rows: list[list[str]] = []
        for row_node in (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "row"):
            row: list[str] = []
            for cell in (node for node in row_node if node.tag.rsplit("}", 1)[-1] == "c"):
                reference = cell.attrib.get("r", "")
                column_match = re.match(r"([A-Z]+)", reference.upper())
                if column_match:
                    column_index = 0
                    for character in column_match.group(1):
                        column_index = column_index * 26 + ord(character) - ord("A") + 1
                    column_index -= 1
                    if column_index >= MAX_TABLE_COLUMNS:
                        continue
                    if column_index > len(row):
                        row.extend([""] * (column_index - len(row)))
                cell_type = cell.attrib.get("t")
                value_node = next((node for node in cell.iter() if node.tag.rsplit("}", 1)[-1] in {"v", "t"}), None)
                value = value_node.text or "" if value_node is not None else ""
                if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
                if column_match and column_index < len(row):
                    row[column_index] = value
                else:
                    row.append(value)
            rows.append(row)
            if len(rows) > MAX_TABLE_ROWS:
                break
    return _table_payload(rows, truncated=len(rows) > MAX_TABLE_ROWS, label="Первый лист книги")


def _pptx(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        slides = sorted(name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
        lines = []
        for index, name in enumerate(slides[:100]):
            root = ElementTree.fromstring(_archive_member(archive, name))
            text = " ".join(node.text or "" for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "t").strip()
            if text:
                lines.append(f"Слайд {index + 1}\n{text}")
    return {"kind": "text", "text": "\n\n".join(lines), "truncated": len(slides) > 100, "label": "Текст презентации"}


def _odf(path: Path, extension: str) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(_archive_member(archive, "content.xml"))
    if extension == ".ods":
        rows = []
        for row_node in (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "table-row"):
            row = []
            for cell in (node for node in row_node if node.tag.rsplit("}", 1)[-1] == "table-cell"):
                row.append(" ".join(value.strip() for value in cell.itertext() if value.strip()))
            rows.append(row)
            if len(rows) > MAX_TABLE_ROWS:
                break
        return _table_payload(rows, truncated=len(rows) > MAX_TABLE_ROWS, label="Первая таблица")
    lines = [" ".join(value.strip() for value in node.itertext() if value.strip())
             for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"p", "h"}]
    return {"kind": "text", "text": "\n".join(value for value in lines if value), "truncated": False,
            "label": "Текст документа" if extension == ".odt" else "Текст презентации"}


def _rtf_text(value: str) -> str:
    value = re.sub(r"\\u(-?\d+)\??", lambda match: chr(int(match.group(1)) % 65536), value)
    value = re.sub(r"\\'[0-9a-fA-F]{2}", "", value)
    value = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", value)
    return re.sub(r"[{}]", "", value).strip()


def build_preview_data(path: Path, name: str) -> dict[str, object]:
    extension = Path(name).suffix.lower()
    if extension not in DATA_EXTENSIONS:
        raise PreviewError("Для этого формата используется встроенный медиапросмотрщик.")
    try:
        if extension == ".docx":
            return _docx(path)
        if extension == ".xlsx":
            return _xlsx(path)
        if extension == ".pptx":
            return _pptx(path)
        if extension in {".odt", ".ods", ".odp"}:
            return _odf(path, extension)
        value, truncated = _read_limited(path)
        text = _decode_text(value)
        if extension in {".csv", ".tsv"}:
            rows = list(csv.reader(io.StringIO(text), delimiter="\t" if extension == ".tsv" else ","))
            return _table_payload(rows, truncated=truncated, label="Табличные данные")
        if extension == ".json":
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        elif extension == ".rtf":
            text = _rtf_text(text)
        return {"kind": "text", "text": text, "truncated": truncated, "label": "Текстовый документ"}
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise PreviewError("Не удалось безопасно прочитать данные документа.") from exc
