from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path


class FileValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedFile:
    mime_type: str
    category: str
    extension: str


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".rtf",
    ".txt", ".md", ".csv", ".tsv", ".json", ".srt", ".vtt",
}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | DOCUMENT_EXTENSIONS


EXTENSION_MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".mp4": "video/mp4",
    ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation", ".rtf": "application/rtf",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".tsv": "text/tab-separated-values", ".json": "application/json",
    ".srt": "application/x-subrip", ".vtt": "text/vtt",
}


def _category(extension: str) -> str:
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return "document"


def _validate_zip(path: Path, extension: str) -> None:
    expected_prefix = {".docx": "word/", ".xlsx": "xl/", ".pptx": "ppt/"}.get(extension)
    odf_type = {
        ".odt": "application/vnd.oasis.opendocument.text",
        ".ods": "application/vnd.oasis.opendocument.spreadsheet",
        ".odp": "application/vnd.oasis.opendocument.presentation",
    }.get(extension)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) > 10_000:
                raise FileValidationError("Архив документа содержит слишком много элементов.")
            if expected_prefix:
                if "[Content_Types].xml" not in names or not any(name.startswith(expected_prefix) for name in names):
                    raise FileValidationError("Содержимое файла не соответствует формату документа.")
            elif odf_type:
                try:
                    actual = archive.read("mimetype").decode("ascii").strip()
                except (KeyError, UnicodeDecodeError):
                    actual = ""
                if actual != odf_type:
                    raise FileValidationError("Содержимое файла не соответствует формату документа.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileValidationError("Повреждённый или неподдерживаемый офисный документ.") from exc


def _validate_text(path: Path, extension: str) -> None:
    with path.open("rb") as source:
        sample = source.read(256 * 1024)
    if not sample:
        return
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
        if b"\x00" in sample:
            raise FileValidationError("Файл выглядит как бинарный, а не текстовый документ.")
    try:
        sample.decode(encoding)
    except UnicodeDecodeError as exc:
        raise FileValidationError("Текстовый документ должен быть в UTF-8 или UTF-16.") from exc
    if extension == ".json" and path.stat().st_size <= 4 * 1024 * 1024:
        try:
            json.loads(path.read_text(encoding=encoding))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FileValidationError("JSON-файл содержит некорректные данные.") from exc


def validate_file(path: Path, original_name: str) -> ValidatedFile:
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            "Поддерживаются изображения, видео, аудио, PDF, современные офисные и текстовые документы."
        )
    with path.open("rb") as source:
        header = source.read(64)
    valid = False
    if extension in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif extension == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension == ".gif":
        valid = header.startswith((b"GIF87a", b"GIF89a"))
    elif extension == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    elif extension == ".bmp":
        valid = header.startswith(b"BM")
    elif extension in {".tif", ".tiff"}:
        valid = header.startswith((b"II*\x00", b"MM\x00*"))
    elif extension == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif extension in {".mp4", ".m4v", ".mov", ".m4a"}:
        valid = len(header) >= 12 and header[4:8] == b"ftyp"
    elif extension in {".webm", ".mkv"}:
        valid = header.startswith(b"\x1aE\xdf\xa3")
    elif extension == ".avi":
        valid = header.startswith(b"RIFF") and header[8:12] == b"AVI "
    elif extension == ".wav":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    elif extension == ".mp3":
        valid = header.startswith(b"ID3") or (len(header) > 1 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)
    elif extension == ".flac":
        valid = header.startswith(b"fLaC")
    elif extension in {".ogg", ".opus"}:
        valid = header.startswith(b"OggS")
    elif extension == ".aac":
        valid = len(header) > 1 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0
    elif extension in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}:
        _validate_zip(path, extension)
        valid = True
    elif extension == ".rtf":
        valid = header.lstrip().startswith(b"{\\rtf")
    else:
        _validate_text(path, extension)
        valid = True
    if not valid:
        raise FileValidationError("Содержимое файла не соответствует его расширению.")
    return ValidatedFile(EXTENSION_MIME_TYPES[extension], _category(extension), extension)
