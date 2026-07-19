import io
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageTk, UnidentifiedImageError

from media_metadata import metadata_movflags, metadata_output_args, process_video_metadata


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "for_cut"
YTDLP_EXE = BASE_DIR / "yt-dlp.exe"
YOUTUBE_COOKIES = BASE_DIR / "cookies" / "www.youtube.com_cookies.txt"
VK_COOKIES = BASE_DIR / "cookies" / "vk.com_cookies.txt"
RESOLUTION_OPTIONS = {
    "1920x1080 (Full HD)": 1080,
    "2560x1440 (2K)": 1440,
    "3840x2160 (4K)": 2160,
}
DEFAULT_RESOLUTION = "1920x1080 (Full HD)"
METADATA_MODE_OPTIONS = {
    "Без очистки": "none",
    "Стандартная очистка": "strip",
    "Экспериментальная подмена": "synthetic",
}
DEFAULT_METADATA_MODE = "Стандартная очистка"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
LOCAL_FFMPEG = BASE_DIR / "ffmpeg.exe"
LOCAL_FFPROBE = BASE_DIR / "ffprobe.exe"
DOWNLOAD_PATH_MARKER = "__YTLOADER_FILE__:"
STATIC_OVERLAY_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
YOUTUBE_RATE_LIMIT_MARKERS = (
    "rate-limited by youtube",
    "this content isn't available, try again later",
)
YOUTUBE_RATE_LIMIT_BUFFER_SECONDS = 5 * 60
YOUTUBE_RATE_LIMIT_MAX_PAUSES = 3
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_NVENC_AVAILABLE: bool | None = None
SUBPROCESS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def detect_cookies(url: str) -> Path | None:
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return YOUTUBE_COOKIES if YOUTUBE_COOKIES.exists() else None
    if "vk.com" in lowered or "vkvideo.ru" in lowered:
        return VK_COOKIES if VK_COOKIES.exists() else None
    return None


def normalize_channel_shorts_url(value: str) -> str:
    """Return the /shorts tab URL for a supported YouTube channel URL."""
    raw_url = value.strip()
    if not raw_url:
        raise ValueError("Вставь ссылку на YouTube-канал.")

    if "://" not in raw_url:
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise ValueError("Нужна ссылка на канал youtube.com.")

    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError("В ссылке не найден канал YouTube.")

    first_part = path_parts[0]
    if first_part.startswith("@") and len(first_part) > 1:
        channel_parts = [first_part]
    elif first_part in {"channel", "c", "user"} and len(path_parts) >= 2:
        channel_parts = path_parts[:2]
    else:
        raise ValueError(
            "Поддерживаются ссылки вида youtube.com/@канал или youtube.com/channel/UC..."
        )

    # Keep only the part that identifies the channel, ignoring any extra path.
    channel_path = "/".join(channel_parts)
    return f"https://www.youtube.com/{channel_path}/shorts"


def extract_video_ids(output: str) -> list[str]:
    """Parse and de-duplicate yt-dlp's one-ID-per-line output."""
    video_ids: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        video_id = line.strip()
        if VIDEO_ID_PATTERN.fullmatch(video_id) and video_id not in seen:
            seen.add(video_id)
            video_ids.append(video_id)
    return video_ids


def extract_video_id_from_url(value: str) -> str | None:
    """Extract a YouTube video ID for robust downloaded-file lookup."""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"} and parts:
        video_id = parts[0]
    elif len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
        video_id = parts[1]
    elif parts == ["watch"]:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    return video_id if VIDEO_ID_PATTERN.fullmatch(video_id) else None


def find_downloaded_video(
    output_dir: Path,
    reported_path: Path | None,
    url: str,
    started_at: float,
) -> Path | None:
    """Recover the real file when yt-dlp's printed Unicode path was decoded incorrectly."""
    if reported_path and reported_path.is_file():
        return reported_path

    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4" and ".tmp." not in path.name
    ]
    video_id = extract_video_id_from_url(url)
    if video_id:
        matching_id = [path for path in candidates if f"[{video_id}]" in path.name]
        if matching_id:
            return max(matching_id, key=lambda path: path.stat().st_mtime)

    recent = [path for path in candidates if path.stat().st_mtime >= started_at - 2]
    return max(recent, key=lambda path: path.stat().st_mtime) if recent else None


def is_paste_shortcut(keysym: str, keycode: int) -> bool:
    """Recognize Ctrl+V by symbol and by the physical V key on Windows."""
    return keysym.lower() in {"v", "cyrillic_em"} or keycode == 86


def is_youtube_rate_limit_message(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in YOUTUBE_RATE_LIMIT_MARKERS)


def youtube_rate_limit_wait_seconds(message: str) -> int | None:
    """Read YouTube's approximate block duration, defaulting to one hour."""
    if not is_youtube_rate_limit_message(message):
        return None

    lowered = message.lower()
    match = re.search(
        r"(?:up to|for)\s+(an|one|\d+)\s*(second|minute|hour)s?",
        lowered,
    )
    if not match:
        return 60 * 60

    raw_amount, unit = match.groups()
    amount = 1 if raw_amount in {"an", "one"} else int(raw_amount)
    multiplier = {"second": 1, "minute": 60, "hour": 60 * 60}[unit]
    return amount * multiplier


def find_media_tool(name: str) -> str | None:
    local_path = LOCAL_FFMPEG if name == "ffmpeg" else LOCAL_FFPROBE
    if local_path.exists():
        return str(local_path)
    return shutil.which(name)


def build_logo_filter(
    video_width: int,
    opacity: int,
    width_percent: int,
    position_x: int = 50,
    position_y: int = 96,
) -> str:
    logo_width = max(16, round(video_width * width_percent / 100))
    alpha = max(0.05, min(opacity / 100, 1.0))
    x_ratio = max(0, min(position_x, 100)) / 100
    y_ratio = max(0, min(position_y, 100)) / 100
    return (
        f"[1:v]scale={logo_width}:-1,format=rgba,"
        f"colorchannelmixer=aa={alpha:.2f}[logo];"
        f"[0:v][logo]overlay=x=(W-w)*{x_ratio:.2f}:y=(H-h)*{y_ratio:.2f}:"
        "shortest=1,"
        "format=yuv420p[v]"
    )


def build_variant_directories(output_dir: Path, logo_paths: list[Path]) -> list[Path]:
    """Return unique Windows-safe output directories named after each logo file."""
    directories: list[Path] = []
    used_names: set[str] = set()

    for logo_path in logo_paths:
        folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", logo_path.stem).strip(" .")
        if not folder_name:
            folder_name = "gif"
        if folder_name.upper() in WINDOWS_RESERVED_NAMES:
            folder_name = f"{folder_name}_gif"

        candidate = folder_name
        suffix = 2
        while candidate.casefold() in used_names:
            candidate = f"{folder_name}_{suffix}"
            suffix += 1

        used_names.add(candidate.casefold())
        directories.append(output_dir / candidate)

    return directories


def build_overlay_input_args(overlay_path: Path) -> list[str]:
    """Build an FFmpeg input that lasts until the main video ends."""
    if overlay_path.suffix.lower() in STATIC_OVERLAY_SUFFIXES:
        return ["-loop", "1", "-i", str(overlay_path)]
    return ["-stream_loop", "-1", "-i", str(overlay_path)]


def is_supported_overlay(overlay_path: Path, ffprobe_path: str) -> bool:
    """Accept any file that FFprobe recognizes as a visual stream."""
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(overlay_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=SUBPROCESS_CREATION_FLAGS,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "video"


def load_overlay_preview(overlay_path: Path) -> Image.Image | None:
    """Load the first visual frame with Pillow, falling back to FFmpeg for video."""
    try:
        with Image.open(overlay_path) as source:
            source.seek(0)
            preview = source.convert("RGBA")
    except (OSError, UnidentifiedImageError):
        ffmpeg_path = find_media_tool("ffmpeg")
        if not ffmpeg_path:
            return None
        result = subprocess.run(
            [
                ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-i", str(overlay_path), "-frames:v", "1",
                "-f", "image2pipe", "-vcodec", "png", "pipe:1",
            ],
            capture_output=True,
            creationflags=SUBPROCESS_CREATION_FLAGS,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        try:
            with Image.open(io.BytesIO(result.stdout)) as source:
                preview = source.convert("RGBA")
        except (OSError, UnidentifiedImageError):
            return None

    preview.thumbnail((900, 1600), Image.Resampling.LANCZOS)
    return preview


def probe_video_width(video_path: Path, ffprobe_path: str) -> int:
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=SUBPROCESS_CREATION_FLAGS,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFprobe не смог определить размер видео")
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("FFprobe вернул некорректную ширину видео") from exc


def has_nvenc(ffmpeg_path: str) -> bool:
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is None:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=SUBPROCESS_CREATION_FLAGS,
        )
        _NVENC_AVAILABLE = result.returncode == 0 and "h264_nvenc" in result.stdout
    return _NVENC_AVAILABLE


def overlay_logo(
    video_path: Path,
    logo_path: Path,
    opacity: int,
    width_percent: int,
    log: Callable[[str], None],
    position_x: int = 50,
    position_y: int = 96,
    metadata_mode: str = "strip",
) -> bool:
    ffmpeg_path = find_media_tool("ffmpeg")
    ffprobe_path = find_media_tool("ffprobe")
    if not ffmpeg_path or not ffprobe_path:
        log("FFmpeg/FFprobe не найдены — логотип не наложен.")
        return False

    try:
        video_width = probe_video_width(video_path, ffprobe_path)
    except RuntimeError as exc:
        log(f"Не удалось определить размер видео: {exc}")
        return False

    output_path = video_path.with_name(f"{video_path.stem}.with_logo.tmp.mp4")
    logo_input = build_overlay_input_args(logo_path)
    common_command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(video_path),
        *logo_input,
        "-filter_complex",
        build_logo_filter(
            video_width, opacity, width_percent, position_x, position_y,
        ),
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:a",
        "copy",
        *metadata_output_args(metadata_mode, str(video_path)),
        "-movflags",
        metadata_movflags(metadata_mode),
        "-shortest",
    ]

    encoders = []
    if has_nvenc(ffmpeg_path):
        encoders.append(("NVENC", ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "22"]))
    encoders.append(("CPU", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20"]))

    try:
        for encoder_name, encoder_args in encoders:
            log(f"Наложение логотипа: {video_path.name} ({encoder_name})")
            result = subprocess.run(
                [*common_command, *encoder_args, str(output_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=SUBPROCESS_CREATION_FLAGS,
            )
            if result.returncode == 0 and output_path.exists():
                output_path.replace(video_path)
                log(f"Логотип наложен: {video_path.name}")
                return True
            log(f"Кодировщик {encoder_name} не сработал, пробую следующий.")

        log(f"Не удалось наложить логотип: {result.stderr.strip()}")
        return False
    finally:
        if output_path.exists():
            output_path.unlink()


def create_logo_variants(
    source_path: Path,
    output_dir: Path,
    logo_paths: list[Path],
    opacity: int,
    width_percent: int,
    log: Callable[[str], None],
    position_x: int = 50,
    position_y: int = 96,
    metadata_mode: str = "strip",
) -> list[Path]:
    """Create one processed copy per logo in a folder named after that logo."""
    completed_paths: list[Path] = []
    variant_dirs = build_variant_directories(output_dir, logo_paths)

    for index, (logo_path, variant_dir) in enumerate(
        zip(logo_paths, variant_dirs, strict=True), start=1
    ):
        variant_dir.mkdir(parents=True, exist_ok=True)
        variant_path = variant_dir / source_path.name
        log(
            f"Вариант {index}/{len(logo_paths)}: {logo_path.name} → "
            f"{variant_dir.name}\\{variant_path.name}"
        )
        try:
            shutil.copy2(source_path, variant_path)
        except OSError as exc:
            log(f"Не удалось создать копию для {logo_path.name}: {exc}")
            continue

        if overlay_logo(
            variant_path, logo_path, opacity, width_percent, log,
            position_x, position_y, metadata_mode=metadata_mode,
        ):
            completed_paths.append(variant_path)
        else:
            try:
                variant_path.unlink()
            except OSError:
                pass

    return completed_paths


def create_logo_variants_batch(
    source_paths: list[Path],
    output_dir: Path,
    logo_paths: list[Path],
    opacity: int,
    width_percent: int,
    log: Callable[[str], None],
    progress: Callable[[int, int], None],
    position_x: int = 50,
    position_y: int = 96,
    metadata_mode: str = "strip",
) -> int:
    """Process downloaded sources only after phase one has fully completed."""
    fully_processed = 0
    total = len(source_paths)
    for source_index, source_path in enumerate(source_paths, start=1):
        progress(source_index, total)
        if not source_path.is_file():
            log(f"Исходник не найден, пропускаю: {source_path.name}")
            continue

        completed_variants = create_logo_variants(
            source_path,
            output_dir,
            logo_paths,
            opacity,
            width_percent,
            log,
            position_x,
            position_y,
            metadata_mode,
        )
        if len(completed_variants) == len(logo_paths):
            source_path.unlink()
            fully_processed += 1
            log(
                f"Все варианты созданы: {source_path.name} "
                f"({len(completed_variants)}/{len(logo_paths)})"
            )
        else:
            log(
                f"Исходник сохранён для повторной обработки: {source_path.name}. "
                f"Готово вариантов: {len(completed_variants)}/{len(logo_paths)}"
            )
    return fully_processed


class DownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("YT Loader")
        self.root.geometry("1050x940")
        self.root.minsize(900, 760)

        self.log_queue: queue.Queue[str | tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.output_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.resolution_var = tk.StringVar(value=DEFAULT_RESOLUTION)
        self.metadata_mode_var = tk.StringVar(value=DEFAULT_METADATA_MODE)
        self.channel_url_var = tk.StringVar()
        self.logo_paths: list[Path] = []
        self.logo_opacity_var = tk.IntVar(value=35)
        self.logo_width_var = tk.IntVar(value=22)
        self.logo_position_x_var = tk.IntVar(value=50)
        self.logo_position_y_var = tk.IntVar(value=96)
        self.overlay_preview_source: Image.Image | None = None
        self.overlay_preview_photo: ImageTk.PhotoImage | None = None
        self.overlay_editor_gesture: dict[str, float | str] | None = None
        self.status_var = tk.StringVar(value="Готово к загрузке")

        self._build_ui()
        for variable in (
            self.logo_opacity_var,
            self.logo_width_var,
            self.logo_position_x_var,
            self.logo_position_y_var,
        ):
            variable.trace_add("write", self._on_overlay_setting_changed)
        self._redraw_overlay_editor()
        self.root.after(150, self._flush_log_queue)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(
            frame,
            text="Загрузка видео по списку ссылок",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w")

        subtitle = ttk.Label(
            frame,
            text="Поддерживает импорт Shorts с канала, вставку ссылок, .txt и cookies для YouTube и VK.",
        )
        subtitle.pack(anchor="w", pady=(4, 16))

        output_frame = ttk.LabelFrame(frame, text="Куда сохранять", padding=12)
        output_frame.pack(fill="x")

        output_entry = ttk.Entry(output_frame, textvariable=self.output_dir_var)
        output_entry.pack(side="left", fill="x", expand=True)

        browse_output_button = ttk.Button(
            output_frame, text="Выбрать папку", command=self.choose_output_dir
        )
        browse_output_button.pack(side="left", padx=(8, 0))

        channel_frame = ttk.LabelFrame(
            frame, text="Импорт Shorts с YouTube-канала", padding=12
        )
        channel_frame.pack(fill="x", pady=(16, 0))

        self.channel_entry = ttk.Entry(
            channel_frame,
            textvariable=self.channel_url_var,
        )
        self.channel_entry.pack(side="left", fill="x", expand=True)
        self.channel_entry.bind("<Return>", lambda _event: self.start_shorts_import())
        self._bind_paste_shortcuts(self.channel_entry)

        self.shorts_button = ttk.Button(
            channel_frame,
            text="Получить ссылки Shorts",
            command=self.start_shorts_import,
        )
        self.shorts_button.pack(side="left", padx=(8, 0))

        links_frame = ttk.LabelFrame(frame, text="Ссылки", padding=12)
        links_frame.pack(fill="both", expand=True, pady=(16, 0))

        links_toolbar = ttk.Frame(links_frame)
        links_toolbar.pack(fill="x", pady=(0, 8))

        ttk.Button(
            links_toolbar, text="Вставить из буфера", command=self.paste_from_clipboard
        ).pack(side="left")
        ttk.Button(
            links_toolbar, text="Загрузить из txt", command=self.load_urls_file
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            links_toolbar, text="Очистить", command=self.clear_urls
        ).pack(side="left", padx=(8, 0))

        self.urls_text = tk.Text(links_frame, wrap="word", height=8, font=("Consolas", 10))
        self.urls_text.pack(fill="both", expand=True)
        self._bind_paste_shortcuts(self.urls_text)

        self.paste_menu = tk.Menu(self.root, tearoff=False)
        self.paste_menu.add_command(label="Вставить", command=self._paste_from_context_menu)
        self._paste_target: tk.Widget = self.urls_text
        self.channel_entry.bind("<Button-3>", self._show_paste_menu)
        self.urls_text.bind("<Button-3>", self._show_paste_menu)

        logo_frame = ttk.LabelFrame(
            frame, text="Изображение/анимация поверх видео — можно выбрать несколько", padding=12
        )
        logo_frame.pack(fill="x", pady=(16, 0))

        logo_body = ttk.Frame(logo_frame)
        logo_body.pack(fill="x")
        logo_controls = ttk.Frame(logo_body)
        logo_controls.pack(side="left", fill="both", expand=True)
        editor_frame = ttk.LabelFrame(
            logo_body, text="Конструктор 9:16", padding=10
        )
        editor_frame.pack(side="right", padx=(18, 0), anchor="n")

        logo_file_frame = ttk.Frame(logo_controls)
        logo_file_frame.pack(fill="x")
        self.logo_listbox = tk.Listbox(logo_file_frame, height=3, exportselection=False)
        self.logo_listbox.pack(side="left", fill="x", expand=True)
        self.logo_listbox.bind("<<ListboxSelect>>", self._select_overlay_preview)
        self.logo_choose_button = ttk.Button(
            logo_file_frame,
            text="Выбрать оверлеи",
            command=self.choose_logos,
        )
        self.logo_choose_button.pack(side="left", padx=(8, 0))
        self.logo_clear_button = ttk.Button(
            logo_file_frame,
            text="Убрать",
            command=self.clear_logos,
        )
        self.logo_clear_button.pack(side="left", padx=(8, 0))

        logo_settings_frame = ttk.Frame(logo_controls)
        logo_settings_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(logo_settings_frame, text="Непрозрачность, %:").pack(side="left")
        self.logo_opacity_spinbox = ttk.Spinbox(
            logo_settings_frame,
            from_=5,
            to=100,
            increment=5,
            textvariable=self.logo_opacity_var,
            width=5,
        )
        self.logo_opacity_spinbox.pack(side="left", padx=(6, 18))
        ttk.Label(logo_settings_frame, text="Ширина от кадра, %:").pack(side="left")
        self.logo_width_spinbox = ttk.Spinbox(
            logo_settings_frame,
            from_=5,
            to=100,
            increment=1,
            textvariable=self.logo_width_var,
            width=5,
        )
        self.logo_width_spinbox.pack(side="left", padx=(6, 0))
        ttk.Label(
            logo_settings_frame,
            text="Позиция и размер берутся из конструктора справа.",
        ).pack(side="left", padx=(18, 0))
        self._build_overlay_editor(editor_frame)

        options_frame = ttk.LabelFrame(frame, text="Параметры", padding=12)
        options_frame.pack(fill="x", pady=(16, 0))

        resolution_frame = ttk.Frame(options_frame)
        resolution_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(resolution_frame, text="Разрешение:").pack(side="left")

        self.resolution_combo = ttk.Combobox(
            resolution_frame,
            textvariable=self.resolution_var,
            values=list(RESOLUTION_OPTIONS),
            state="readonly",
            width=22,
        )
        self.resolution_combo.pack(side="left", padx=(8, 0))

        ttk.Label(resolution_frame, text="Метаданные:").pack(side="left", padx=(24, 0))
        self.metadata_mode_combo = ttk.Combobox(
            resolution_frame,
            textvariable=self.metadata_mode_var,
            values=list(METADATA_MODE_OPTIONS),
            state="readonly",
            width=28,
        )
        self.metadata_mode_combo.pack(side="left", padx=(8, 0))

        options_text = (
            "Формат: лучшее видео в выбранном разрешении или ниже + m4a-аудио, итоговый контейнер mp4. "
            "YouTube запускается с node, для VK cookies подставляются автоматически."
        )
        ttk.Label(options_frame, text=options_text, wraplength=820).pack(anchor="w")

        actions_frame = ttk.Frame(frame)
        actions_frame.pack(fill="x", pady=(16, 0))

        self.download_button = ttk.Button(
            actions_frame, text="Скачать", command=self.start_download
        )
        self.download_button.pack(side="left")

        ttk.Label(actions_frame, textvariable=self.status_var).pack(side="left", padx=(12, 0))

        log_frame = ttk.LabelFrame(frame, text="Лог", padding=12)
        log_frame.pack(fill="both", expand=True, pady=(16, 0))

        self.log_text = tk.Text(
            log_frame, wrap="word", height=8, state="disabled", font=("Consolas", 10)
        )
        self.log_text.pack(fill="both", expand=True)

    def choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(BASE_DIR))
        if selected:
            self.output_dir_var.set(selected)

    def choose_logos(self) -> None:
        selected_files = filedialog.askopenfilenames(
            title="Выбери изображения или анимированные оверлеи",
            filetypes=[
                (
                    "Изображения и видео",
                    "*.png *.jpg *.jpeg *.bmp *.gif *.apng *.webp *.mov *.mp4 *.m4v *.webm *.mkv *.avi *.mpeg *.mpg",
                ),
                ("Все файлы", "*.*"),
            ],
            initialdir=str(BASE_DIR),
        )
        if selected_files:
            self.logo_paths = [Path(selected_file) for selected_file in selected_files]
            self.logo_listbox.delete(0, "end")
            for logo_path in self.logo_paths:
                self.logo_listbox.insert("end", logo_path.name)
            self.logo_listbox.selection_set(0)
            self._load_selected_overlay_preview(0)

    def clear_logos(self) -> None:
        self.logo_paths.clear()
        self.logo_listbox.delete(0, "end")
        self.overlay_preview_source = None
        self._redraw_overlay_editor()

    def _build_overlay_editor(self, parent: ttk.LabelFrame) -> None:
        self.overlay_canvas_width = 180
        self.overlay_canvas_height = 320
        self.overlay_canvas = tk.Canvas(
            parent,
            width=self.overlay_canvas_width,
            height=self.overlay_canvas_height,
            bg="#101722",
            highlightthickness=1,
            highlightbackground="#53647a",
            cursor="hand2",
            takefocus=True,
        )
        self.overlay_canvas.pack()
        for x in range(45, self.overlay_canvas_width, 45):
            self.overlay_canvas.create_line(
                x, 0, x, self.overlay_canvas_height, fill="#1d2938"
            )
        for y in range(40, self.overlay_canvas_height, 40):
            self.overlay_canvas.create_line(
                0, y, self.overlay_canvas_width, y, fill="#1d2938"
            )
        self.overlay_canvas.create_rectangle(
            5, 5, self.overlay_canvas_width - 5, self.overlay_canvas_height - 5,
            outline="#3b4c65", dash=(4, 4),
        )
        self.overlay_canvas.create_line(
            0, self.overlay_canvas_height // 2,
            self.overlay_canvas_width, self.overlay_canvas_height // 2,
            fill="#4d477d", dash=(3, 5),
        )
        self.overlay_canvas.bind("<ButtonPress-1>", self._overlay_canvas_press)
        self.overlay_canvas.bind("<B1-Motion>", self._overlay_canvas_motion)
        self.overlay_canvas.bind("<ButtonRelease-1>", self._overlay_canvas_release)
        self.overlay_canvas.bind("<KeyPress>", self._overlay_canvas_key)

        self.overlay_position_label = ttk.Label(parent, anchor="center")
        self.overlay_position_label.pack(fill="x", pady=(7, 5))
        self.overlay_reset_button = ttk.Button(
            parent, text="Сбросить позицию", command=self._reset_overlay_position
        )
        self.overlay_reset_button.pack(fill="x")
        ttk.Label(
            parent,
            text="Тяни оверлей или его маркер.\n"
                 "Стрелки: 1%, Shift+стрелки: 5%.",
            justify="center",
        ).pack(pady=(6, 0))

    def _select_overlay_preview(self, _event: tk.Event | None = None) -> None:
        selection = self.logo_listbox.curselection()
        if selection:
            self._load_selected_overlay_preview(int(selection[0]))

    def _load_selected_overlay_preview(self, index: int) -> None:
        if not 0 <= index < len(self.logo_paths):
            return
        self.overlay_preview_source = load_overlay_preview(self.logo_paths[index])
        if self.overlay_preview_source is None:
            self._log(f"Не удалось показать предпросмот: {self.logo_paths[index].name}")
        self._redraw_overlay_editor()

    def _on_overlay_setting_changed(self, *_args: object) -> None:
        if hasattr(self, "overlay_canvas"):
            self._redraw_overlay_editor()

    def _redraw_overlay_editor(self) -> None:
        if not hasattr(self, "overlay_canvas"):
            return
        self.overlay_canvas.delete("preview")
        try:
            opacity = max(5, min(int(self.logo_opacity_var.get()), 100))
            width_percent = max(5, min(int(self.logo_width_var.get()), 100))
            position_x = max(0, min(int(self.logo_position_x_var.get()), 100))
            position_y = max(0, min(int(self.logo_position_y_var.get()), 100))
        except (tk.TclError, ValueError):
            return

        self.overlay_position_label.config(
            text=f"X {position_x}%   Y {position_y}%   Ширина {width_percent}%"
        )
        if self.overlay_preview_source is None:
            self.overlay_canvas.create_text(
                self.overlay_canvas_width // 2,
                self.overlay_canvas_height // 2 - 12,
                text="9:16",
                fill="#8494aa",
                font=("Segoe UI", 20, "bold"),
                tags="preview",
            )
            self.overlay_canvas.create_text(
                self.overlay_canvas_width // 2,
                self.overlay_canvas_height // 2 + 18,
                text="Выбери оверлей",
                fill="#718198",
                tags="preview",
            )
            self.overlay_preview_bounds = None
            return

        display_width = max(9, round(self.overlay_canvas_width * width_percent / 100))
        ratio = self.overlay_preview_source.height / max(1, self.overlay_preview_source.width)
        display_height = max(1, round(display_width * ratio))
        rendered = self.overlay_preview_source.resize(
            (display_width, display_height), Image.Resampling.LANCZOS
        )
        alpha = rendered.getchannel("A").point(
            lambda value: round(value * opacity / 100)
        )
        rendered.putalpha(alpha)
        self.overlay_preview_photo = ImageTk.PhotoImage(rendered)

        max_left = max(0, self.overlay_canvas_width - display_width)
        max_top = max(0, self.overlay_canvas_height - display_height)
        left = round(max_left * position_x / 100)
        top = round(max_top * position_y / 100)
        right = left + display_width
        bottom = top + display_height
        self.overlay_preview_bounds = (left, top, right, bottom)
        self.overlay_canvas.create_image(
            left, top, image=self.overlay_preview_photo, anchor="nw", tags="preview"
        )
        self.overlay_canvas.create_rectangle(
            left, top, right, bottom, outline="#a598ff", width=2, tags="preview"
        )
        handle_x = min(self.overlay_canvas_width - 7, right)
        handle_y = min(self.overlay_canvas_height - 7, bottom)
        self.overlay_canvas.create_oval(
            handle_x - 7, handle_y - 7, handle_x + 7, handle_y + 7,
            fill="#7c6cff", outline="white", width=2,
            tags=("preview", "resize_handle"),
        )

    def _overlay_canvas_press(self, event: tk.Event) -> None:
        self.overlay_canvas.focus_set()
        bounds = getattr(self, "overlay_preview_bounds", None)
        if not bounds:
            return
        left, top, right, bottom = bounds
        handle_x = min(self.overlay_canvas_width - 7, right)
        handle_y = min(self.overlay_canvas_height - 7, bottom)
        resize = abs(event.x - handle_x) <= 13 and abs(event.y - handle_y) <= 13
        if not resize and not (left <= event.x <= right and top <= event.y <= bottom):
            return
        self.overlay_editor_gesture = {
            "mode": "resize" if resize else "move",
            "offset_x": event.x - left,
            "offset_y": event.y - top,
            "start_x": event.x,
            "start_width": int(self.logo_width_var.get()),
        }

    def _overlay_canvas_motion(self, event: tk.Event) -> None:
        gesture = self.overlay_editor_gesture
        bounds = getattr(self, "overlay_preview_bounds", None)
        if not gesture or not bounds:
            return
        if gesture["mode"] == "resize":
            width = round(
                float(gesture["start_width"])
                + (event.x - float(gesture["start_x"])) / self.overlay_canvas_width * 100
            )
            self.logo_width_var.set(max(5, min(width, 100)))
            return

        left, top, right, bottom = bounds
        display_width = right - left
        display_height = bottom - top
        max_left = max(0, self.overlay_canvas_width - display_width)
        max_top = max(0, self.overlay_canvas_height - display_height)
        new_left = max(0, min(event.x - float(gesture["offset_x"]), max_left))
        new_top = max(0, min(event.y - float(gesture["offset_y"]), max_top))
        self.logo_position_x_var.set(round(new_left / max_left * 100) if max_left else 0)
        self.logo_position_y_var.set(round(new_top / max_top * 100) if max_top else 0)

    def _overlay_canvas_release(self, _event: tk.Event) -> None:
        self.overlay_editor_gesture = None

    def _overlay_canvas_key(self, event: tk.Event) -> str | None:
        step = 5 if event.state & 0x0001 else 1
        if event.keysym == "Left":
            self.logo_position_x_var.set(max(0, self.logo_position_x_var.get() - step))
        elif event.keysym == "Right":
            self.logo_position_x_var.set(min(100, self.logo_position_x_var.get() + step))
        elif event.keysym == "Up":
            self.logo_position_y_var.set(max(0, self.logo_position_y_var.get() - step))
        elif event.keysym == "Down":
            self.logo_position_y_var.set(min(100, self.logo_position_y_var.get() + step))
        else:
            return None
        return "break"

    def _reset_overlay_position(self) -> None:
        self.logo_position_x_var.set(50)
        self.logo_position_y_var.set(96)
        self.logo_width_var.set(22)

    def _bind_paste_shortcuts(self, widget: tk.Widget) -> None:
        widget.bind("<Control-KeyPress>", self._handle_control_key)
        widget.bind("<Shift-Insert>", self._handle_paste_event)

    def _handle_control_key(self, event: tk.Event) -> str | None:
        if is_paste_shortcut(str(event.keysym), int(event.keycode)):
            return self._handle_paste_event(event)
        return None

    def _handle_paste_event(self, event: tk.Event) -> str:
        self._paste_into_widget(event.widget)
        return "break"

    def _show_paste_menu(self, event: tk.Event) -> str:
        self._paste_target = event.widget
        self._paste_target.focus_set()
        try:
            self.paste_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.paste_menu.grab_release()
        return "break"

    def _paste_from_context_menu(self) -> None:
        self._paste_into_widget(self._paste_target)

    def _paste_into_widget(self, widget: tk.Widget) -> bool:
        try:
            clipboard_text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "В буфере обмена нет текста.")
            return False

        try:
            if isinstance(widget, tk.Text):
                if widget.tag_ranges("sel"):
                    widget.delete("sel.first", "sel.last")
            elif isinstance(widget, (tk.Entry, ttk.Entry)) and widget.selection_present():
                widget.delete("sel.first", "sel.last")
            widget.insert("insert", clipboard_text)
        except tk.TclError:
            return False
        return True

    def paste_from_clipboard(self) -> None:
        self.urls_text.focus_set()
        self.urls_text.mark_set("insert", "end-1c")
        if self._paste_into_widget(self.urls_text):
            current_text = self.urls_text.get("1.0", "end-1c")
            if current_text and not current_text.endswith("\n"):
                self.urls_text.insert("end", "\n")

    def load_urls_file(self) -> None:
        selected_file = filedialog.askopenfilename(
            title="Выбери txt со ссылками",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(BASE_DIR),
        )
        if not selected_file:
            return

        content = Path(selected_file).read_text(encoding="utf-8")
        self.urls_text.delete("1.0", "end")
        self.urls_text.insert("1.0", content)
        self._log(f"Загружен список ссылок: {selected_file}")

    def clear_urls(self) -> None:
        self.urls_text.delete("1.0", "end")

    def start_shorts_import(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(
                "Операция уже идет",
                "Дождись завершения текущей операции.",
            )
            return

        try:
            shorts_url = normalize_channel_shorts_url(self.channel_url_var.get())
        except ValueError as exc:
            messagebox.showwarning("Не удалось распознать канал", str(exc))
            return

        if not YTDLP_EXE.exists():
            messagebox.showerror("Не найден yt-dlp", f"Файл не найден:\n{YTDLP_EXE}")
            return

        output_dir = Path(self.output_dir_var.get().strip())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Не удалось создать папку", str(exc))
            return

        self.download_button.config(state="disabled")
        self.shorts_button.config(state="disabled")
        self.status_var.set("Получаю список Shorts...")
        self._log(f"Получение списка Shorts: {shorts_url}")

        self.worker = threading.Thread(
            target=self._shorts_import_worker,
            args=(shorts_url,),
            daemon=True,
        )
        self.worker.start()

    def _shorts_import_worker(self, shorts_url: str) -> None:
        list_command = [
            str(YTDLP_EXE),
            "--encoding",
            "utf-8",
            "--ignore-config",
            "--ignore-errors",
            "--flat-playlist",
            "--skip-download",
            "--no-warnings",
            "--no-update",
            "--print",
            "%(id)s",
            "--js-runtimes",
            "node",
        ]

        cookies_path = detect_cookies(shorts_url)
        if cookies_path:
            list_command.extend(["--cookies", str(cookies_path)])
        list_command.append(shorts_url)

        try:
            list_result = subprocess.run(
                list_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=SUBPROCESS_CREATION_FLAGS,
            )
        except Exception as exc:
            self.log_queue.put(("SHORTS_ERROR", f"Ошибка запуска yt-dlp: {exc}"))
            return

        video_ids = extract_video_ids(list_result.stdout)
        if not video_ids:
            error_text = list_result.stderr.strip() or "Не удалось получить список Shorts канала"
            self.log_queue.put(("SHORTS_ERROR", error_text))
            return

        self._log(f"Найдено Shorts во вкладке канала: {len(video_ids)}")

        self.log_queue.put(
            (
                "SHORTS_RESULT",
                {
                    "links": [
                        f"https://www.youtube.com/shorts/{video_id}"
                        for video_id in video_ids
                    ],
                },
            )
        )

    def start_download(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Загрузка уже идет", "Дождись завершения текущей очереди.")
            return

        output_dir = Path(self.output_dir_var.get().strip())
        resolution_label = self.resolution_var.get()
        max_height = RESOLUTION_OPTIONS.get(resolution_label, RESOLUTION_OPTIONS[DEFAULT_RESOLUTION])
        metadata_mode = METADATA_MODE_OPTIONS.get(
            self.metadata_mode_var.get(), METADATA_MODE_OPTIONS[DEFAULT_METADATA_MODE]
        )
        urls = [line.strip() for line in self.urls_text.get("1.0", "end").splitlines() if line.strip()]
        logo_paths = list(self.logo_paths)
        try:
            logo_opacity = max(5, min(int(self.logo_opacity_var.get()), 100))
            logo_width = max(5, min(int(self.logo_width_var.get()), 100))
            logo_position_x = max(0, min(int(self.logo_position_x_var.get()), 100))
            logo_position_y = max(0, min(int(self.logo_position_y_var.get()), 100))
        except (tk.TclError, ValueError):
            messagebox.showwarning(
                "Параметры оверлея",
                "Проверь прозрачность, размер и позицию оверлея.",
            )
            return

        if not urls:
            messagebox.showwarning("Нет ссылок", "Добавь хотя бы одну ссылку.")
            return

        if not YTDLP_EXE.exists():
            messagebox.showerror("Не найден yt-dlp", f"Файл не найден:\n{YTDLP_EXE}")
            return

        if logo_paths or metadata_mode != "none":
            if not find_media_tool("ffmpeg"):
                messagebox.showerror(
                    "Не найден FFmpeg",
                    "Для выбранной обработки нужен ffmpeg.exe в PATH или рядом с app.py.",
                )
                return

        if logo_paths:
            ffprobe_path = find_media_tool("ffprobe")
            invalid_logos = [
                path
                for path in logo_paths
                if not path.is_file()
                or not ffprobe_path
                or not is_supported_overlay(path, ffprobe_path)
            ]
            if invalid_logos:
                messagebox.showwarning(
                    "Логотип не найден",
                    "FFprobe не смог прочитать эти изображения/анимации:\n"
                    + "\n".join(path.name for path in invalid_logos),
                )
                return
            if not find_media_tool("ffmpeg") or not find_media_tool("ffprobe"):
                messagebox.showerror(
                    "Не найден FFmpeg",
                    "Для наложения логотипа нужны ffmpeg.exe и ffprobe.exe в PATH или рядом с app.py.",
                )
                return

        output_dir.mkdir(parents=True, exist_ok=True)
        self.download_button.config(state="disabled")
        self.shorts_button.config(state="disabled")
        self.resolution_combo.config(state="disabled")
        self.metadata_mode_combo.config(state="disabled")
        self._set_logo_controls_state("disabled")
        self.status_var.set("Идет загрузка...")
        self._log(f"Старт очереди загрузок, разрешение: {resolution_label}")

        self.worker = threading.Thread(
            target=self._download_worker,
            args=(
                urls, output_dir, max_height, logo_paths, logo_opacity, logo_width,
                logo_position_x, logo_position_y, metadata_mode,
            ),
            daemon=True,
        )
        self.worker.start()

    def _download_worker(
        self,
        urls: list[str],
        output_dir: Path,
        max_height: int,
        logo_paths: list[Path],
        logo_opacity: int,
        logo_width: int,
        logo_position_x: int,
        logo_position_y: int,
        metadata_mode: str,
    ) -> None:
        success_count = 0
        rate_limit_index: int | None = None
        rate_limit_pauses = 0
        downloaded_sources: list[Path] = []
        downloaded_source_keys: set[str] = set()
        download_dir = output_dir / ".ytloader_downloads" if logo_paths else output_dir
        download_dir.mkdir(parents=True, exist_ok=True)

        index = 1
        while index <= len(urls):
            url = urls[index - 1]
            cookies_path = detect_cookies(url)
            self._log(f"[{index}/{len(urls)}] Обработка: {url}")
            download_started_at = time.time()

            command = [
                str(YTDLP_EXE),
                "--encoding",
                "utf-8",
                "-f",
                f"bv*[height<={max_height}]+ba[ext=m4a]/bv*[height<={max_height}]+ba/b[height<={max_height}][ext=mp4]/b[height<={max_height}]",
                "--merge-output-format",
                "mp4",
                "--remux-video",
                "mp4",
                "--print",
                f"after_move:{DOWNLOAD_PATH_MARKER}%(filepath)s",
                "-P",
                str(download_dir),
            ]

            if "youtube.com" in url.lower() or "youtu.be" in url.lower():
                command.extend(
                    [
                        "--js-runtimes",
                        "node",
                        "-t",
                        "sleep",
                    ]
                )

            if cookies_path:
                command.extend(["--cookies", str(cookies_path)])
                self._log(f"Используются cookies: {cookies_path.name}")
            else:
                self._log("Cookies не найдены для этого домена, пробую без них")

            command.append(url)

            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=SUBPROCESS_CREATION_FLAGS,
                )
            except Exception as exc:
                self._log(f"Ошибка запуска: {exc}")
                index += 1
                continue

            assert process.stdout is not None
            downloaded_path: Path | None = None
            rate_limit_seconds: int | None = None
            for line in process.stdout:
                clean_line = line.rstrip()
                parsed_wait = youtube_rate_limit_wait_seconds(clean_line)
                if parsed_wait is not None:
                    rate_limit_seconds = max(rate_limit_seconds or 0, parsed_wait)
                if clean_line.startswith(DOWNLOAD_PATH_MARKER):
                    downloaded_path = Path(clean_line[len(DOWNLOAD_PATH_MARKER) :])
                else:
                    self._log(clean_line)

            return_code = process.wait()
            if return_code == 0:
                downloaded_path = find_downloaded_video(
                    download_dir,
                    downloaded_path,
                    url,
                    download_started_at,
                )
                if downloaded_path:
                    self._log(f"Файл загружен: {downloaded_path.name}")
                    if not logo_paths and metadata_mode != "none":
                        ffmpeg_path = find_media_tool("ffmpeg")
                        if not ffmpeg_path or not process_video_metadata(
                            downloaded_path, ffmpeg_path, metadata_mode, self._log,
                            SUBPROCESS_CREATION_FLAGS,
                        ):
                            self._log("Файл скачан, но обработка метаданных не завершена.")
                            index += 1
                            continue
                    source_key = str(downloaded_path.resolve()).casefold()
                    if source_key not in downloaded_source_keys:
                        downloaded_source_keys.add(source_key)
                        downloaded_sources.append(downloaded_path)
                else:
                    self._log("Не удалось определить скачанный файл.")
                success_count += 1
                self._log(f"Исходник готов: {url}")
                rate_limit_pauses = 0
                index += 1
            else:
                self._log(f"Ошибка загрузки, код {return_code}: {url}")
                if rate_limit_seconds is not None:
                    rate_limit_pauses += 1
                    if rate_limit_pauses > YOUTUBE_RATE_LIMIT_MAX_PAUSES:
                        rate_limit_index = index
                        self._log(
                            "YouTube не снял ограничение после нескольких автоматических "
                            "пауз. Очередь остановлена."
                        )
                        break

                    wait_seconds = rate_limit_seconds + YOUTUBE_RATE_LIMIT_BUFFER_SECONDS
                    self._log(
                        f"YouTube ограничил сессию примерно на {rate_limit_seconds // 60} мин. "
                        f"Автопродолжение через {wait_seconds // 60} мин "
                        f"(пауза {rate_limit_pauses}/{YOUTUBE_RATE_LIMIT_MAX_PAUSES})."
                    )
                    self._wait_for_youtube_rate_limit(wait_seconds, index, len(urls))
                    self._log(f"Пауза завершена. Повторяю позицию {index}: {url}")
                    continue
                index += 1

        if rate_limit_index is None and logo_paths:
            self.log_queue.put(
                f"__PHASE__:Создание вариантов: 0/{len(downloaded_sources)}"
            )
            self._log(
                f"Все загрузки завершены. Начинаю создание вариантов для "
                f"{len(downloaded_sources)} видео."
            )
            fully_processed = create_logo_variants_batch(
                downloaded_sources,
                output_dir,
                logo_paths,
                logo_opacity,
                logo_width,
                self._log,
                lambda current, total: self.log_queue.put(
                    f"__PHASE__:Создание вариантов: {current}/{total}"
                ),
                logo_position_x,
                logo_position_y,
                metadata_mode,
            )
            self._log(
                f"Пакетная обработка завершена: {fully_processed}/"
                f"{len(downloaded_sources)} исходников полностью готовы."
            )

        if logo_paths:
            try:
                download_dir.rmdir()
            except OSError:
                pass
        if rate_limit_index is not None:
            self.log_queue.put(
                f"__RATE_LIMIT__:{success_count}/{len(urls)}:{rate_limit_index}"
            )
        else:
            self.log_queue.put(f"__DONE__:{success_count}/{len(urls)}")

    def _wait_for_youtube_rate_limit(
        self,
        wait_seconds: int,
        index: int,
        total: int,
    ) -> None:
        deadline = time.monotonic() + wait_seconds
        last_logged_minutes: int | None = None
        while True:
            remaining_seconds = max(0, int(deadline - time.monotonic()))
            if remaining_seconds <= 0:
                self.log_queue.put(f"__WAIT__:{index}/{total}:0")
                return

            remaining_minutes = (remaining_seconds + 59) // 60
            self.log_queue.put(f"__WAIT__:{index}/{total}:{remaining_minutes}")
            if (
                remaining_minutes != last_logged_minutes
                and (remaining_minutes <= 5 or remaining_minutes % 10 == 0)
            ):
                self._log(f"До автоматического продолжения: {remaining_minutes} мин.")
                last_logged_minutes = remaining_minutes
            time.sleep(min(60, remaining_seconds))

    def _flush_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if isinstance(message, tuple):
                event_name, payload = message
                self.download_button.config(state="normal")
                self.shorts_button.config(state="normal")

                if event_name == "SHORTS_ERROR":
                    error_text = str(payload)
                    self.status_var.set("Ошибка получения Shorts")
                    self._append_log(error_text)
                    messagebox.showerror("Не удалось получить Shorts", error_text)
                    continue

                if event_name == "SHORTS_RESULT":
                    result_data = payload if isinstance(payload, dict) else {}
                    raw_links = result_data.get("links", [])
                    links = list(raw_links) if isinstance(raw_links, list) else []
                    existing = {
                        line.strip()
                        for line in self.urls_text.get("1.0", "end").splitlines()
                        if line.strip()
                    }
                    new_links = [link for link in links if link not in existing]
                    if new_links:
                        current_text = self.urls_text.get("1.0", "end-1c")
                        if current_text and not current_text.endswith("\n"):
                            self.urls_text.insert("end", "\n")
                        self.urls_text.insert("end", "\n".join(new_links) + "\n")

                    duplicate_count = len(links) - len(new_links)
                    status = f"Найдено Shorts: {len(links)}, добавлено: {len(new_links)}"
                    if duplicate_count:
                        status += f", дублей: {duplicate_count}"
                    self.status_var.set(status)
                    self._append_log(status)
                    messagebox.showinfo("Импорт Shorts завершён", status)
                    continue

            if message.startswith("__DONE__:"):
                result = message.split(":", 1)[1]
                self.status_var.set(f"Завершено: {result}")
                self.download_button.config(state="normal")
                self.shorts_button.config(state="normal")
                self.resolution_combo.config(state="readonly")
                self.metadata_mode_combo.config(state="readonly")
                self._set_logo_controls_state("normal")
                continue

            if message.startswith("__WAIT__:"):
                _, position, remaining_minutes = message.split(":", 2)
                if remaining_minutes == "0":
                    self.status_var.set(f"Возобновляю загрузку с позиции {position}...")
                else:
                    self.status_var.set(
                        f"Пауза YouTube: {remaining_minutes} мин, позиция {position}"
                    )
                continue

            if message.startswith("__PHASE__:"):
                phase_status = message.split(":", 1)[1]
                self.status_var.set(phase_status)
                continue

            if message.startswith("__RATE_LIMIT__:"):
                _, result, stopped_at = message.split(":", 2)
                self.status_var.set(f"Остановлено лимитом YouTube: {result}")
                self.download_button.config(state="normal")
                self.shorts_button.config(state="normal")
                self.resolution_combo.config(state="readonly")
                self.metadata_mode_combo.config(state="readonly")
                self._set_logo_controls_state("normal")
                messagebox.showwarning(
                    "YouTube временно ограничил загрузку",
                    f"Очередь остановлена на позиции {stopped_at}.\n\n"
                    "Автоматические паузы были исчерпаны. Подожди дольше или обнови "
                    "cookies перед следующим запуском.",
                )
                continue

            self._append_log(message)

        self.root.after(150, self._flush_log_queue)

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _append_log(self, message: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _set_logo_controls_state(self, state: str) -> None:
        for control in (
            self.logo_choose_button,
            self.logo_clear_button,
            self.logo_listbox,
            self.logo_opacity_spinbox,
            self.logo_width_spinbox,
            self.overlay_reset_button,
        ):
            control.config(state=state)
        self.overlay_canvas.config(state=state)


def main() -> None:
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = DownloaderApp(root)
    app._log(f"Рабочая папка: {BASE_DIR}")
    app._log("Добавь ссылки и нажми 'Скачать'")
    root.mainloop()


if __name__ == "__main__":
    main()
