import hashlib
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


METADATA_MODES = ("none", "strip", "synthetic")


def normalize_metadata_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in METADATA_MODES:
        raise ValueError(f"Неизвестный режим метаданных: {mode}")
    return normalized


def _synthetic_profile(seed: str) -> tuple[str, str, str]:
    """Return a stable synthetic profile so retries produce identical metadata."""
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).digest()
    devices = (
        ("iPhone 13", "iOS 17.5.1"),
        ("iPhone 14 Pro", "iOS 17.6.1"),
        ("iPhone 15", "iOS 18.1.1"),
        ("iPhone 15 Pro", "iOS 18.2.1"),
        ("iPhone 16 Pro", "iOS 18.3.2"),
    )
    model, software = devices[digest[0] % len(devices)]
    start = datetime(2023, 1, 1, 9, 0, tzinfo=timezone.utc)
    seconds = int.from_bytes(digest[1:5], "big") % (3 * 365 * 24 * 60 * 60)
    created = (start + timedelta(seconds=seconds)).replace(microsecond=0)
    return model, software, created.isoformat().replace("+00:00", "Z")


def metadata_output_args(mode: str, seed: str) -> list[str]:
    """Build output-side FFmpeg arguments for the selected privacy mode."""
    mode = normalize_metadata_mode(mode)
    if mode == "none":
        return []

    args = [
        "-fflags", "+bitexact",
        "-map_metadata", "-1",
        "-map_metadata:s", "-1",
        "-map_chapters", "-1",
        "-metadata", "title=",
        "-metadata", "artist=",
        "-metadata", "comment=",
        "-metadata", "description=",
        "-metadata", "copyright=",
        "-metadata", "encoder=",
    ]
    if mode == "synthetic":
        model, software, created = _synthetic_profile(seed)
        args.extend(
            [
                "-metadata", "make=Apple",
                "-metadata", f"model={model}",
                "-metadata", f"software={software}",
                "-metadata", f"creation_time={created}",
                "-metadata", "com.apple.quicktime.make=Apple",
                "-metadata", f"com.apple.quicktime.model={model}",
                "-metadata", f"com.apple.quicktime.software={software}",
                "-metadata", f"com.apple.quicktime.creationdate={created}",
            ]
        )
    return args


def metadata_movflags(mode: str) -> str:
    mode = normalize_metadata_mode(mode)
    return "+faststart+use_metadata_tags" if mode == "synthetic" else "+faststart"


def process_video_metadata(
    video_path: Path,
    ffmpeg_path: str,
    mode: str,
    log: Callable[[str], None],
    creationflags: int | None = None,
) -> bool:
    """Rebuild an MP4 without re-encoding and replace it only after success."""
    mode = normalize_metadata_mode(mode)
    if mode == "none":
        return True

    temp_path = video_path.with_name(f"{video_path.stem}.metadata.tmp.mp4")
    command = [
        ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video_path),
        "-map", "0:v:0", "-map", "0:a?",
        "-c", "copy",
        *metadata_output_args(mode, str(video_path)),
        "-movflags", metadata_movflags(mode),
        str(temp_path),
    ]
    mode_name = "стандартная очистка" if mode == "strip" else "экспериментальная подмена"
    log(f"Метаданные: {mode_name}…")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if creationflags is None else creationflags,
    )
    try:
        if result.returncode != 0 or not temp_path.is_file():
            log(f"Не удалось обработать метаданные: {result.stderr.strip()}")
            return False
        temp_path.replace(video_path)
        log("Метаданные обработаны.")
        return True
    finally:
        temp_path.unlink(missing_ok=True)
