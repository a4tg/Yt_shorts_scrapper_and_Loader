import csv
import json
import queue
import re
import shutil
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable
from urllib.parse import urlparse


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
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
LOCAL_FFMPEG = BASE_DIR / "ffmpeg.exe"
LOCAL_FFPROBE = BASE_DIR / "ffprobe.exe"
DOWNLOAD_PATH_MARKER = "__YTLOADER_FILE__:"
STATIC_OVERLAY_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_NVENC_AVAILABLE: bool | None = None


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


def parse_shorts_metadata(output: str) -> list[dict[str, object]]:
    """Parse compact one-object-per-line JSON printed by yt-dlp."""
    records: list[dict[str, object]] = []
    seen: set[str] = set()

    for line in output.splitlines():
        try:
            raw_record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(raw_record, dict):
            continue

        video_id = str(raw_record.get("id") or "").strip()
        if not VIDEO_ID_PATTERN.fullmatch(video_id) or video_id in seen:
            continue

        raw_tags = raw_record.get("tags")
        tags = (
            [str(tag).strip() for tag in raw_tags if str(tag).strip()]
            if isinstance(raw_tags, list)
            else []
        )
        seen.add(video_id)
        records.append(
            {
                "id": video_id,
                "url": f"https://www.youtube.com/shorts/{video_id}",
                "title": str(raw_record.get("title") or ""),
                "description": str(raw_record.get("description") or ""),
                "tags": tags,
                "uploader": str(raw_record.get("uploader") or ""),
                "upload_date": str(raw_record.get("upload_date") or ""),
            }
        )

    return records


def write_shorts_metadata_csv(records: list[dict[str, object]], output_path: Path) -> None:
    """Write metadata in an Excel-friendly UTF-8 CSV file."""
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["url", "title", "description", "tags", "uploader", "upload_date"],
            delimiter=";",
        )
        writer.writeheader()
        for record in records:
            row = dict(record)
            tags = row.get("tags")
            row["tags"] = ", ".join(tags) if isinstance(tags, list) else str(tags or "")
            row.pop("id", None)
            writer.writerow(row)


def find_media_tool(name: str) -> str | None:
    local_path = LOCAL_FFMPEG if name == "ffmpeg" else LOCAL_FFPROBE
    if local_path.exists():
        return str(local_path)
    return shutil.which(name)


def build_logo_filter(video_width: int, opacity: int, width_percent: int) -> str:
    logo_width = max(16, round(video_width * width_percent / 100))
    alpha = max(0.05, min(opacity / 100, 1.0))
    return (
        f"[1:v]scale={logo_width}:-1,format=rgba,"
        f"colorchannelmixer=aa={alpha:.2f}[logo];"
        "[0:v][logo]overlay=(W-w)/2:H-h-H*0.03:format=auto:shortest=1,"
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
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "video"


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
        creationflags=subprocess.CREATE_NO_WINDOW,
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
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _NVENC_AVAILABLE = result.returncode == 0 and "h264_nvenc" in result.stdout
    return _NVENC_AVAILABLE


def overlay_logo(
    video_path: Path,
    logo_path: Path,
    opacity: int,
    width_percent: int,
    log: Callable[[str], None],
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
        build_logo_filter(video_width, opacity, width_percent),
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
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
                creationflags=subprocess.CREATE_NO_WINDOW,
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

        if overlay_logo(variant_path, logo_path, opacity, width_percent, log):
            completed_paths.append(variant_path)
        else:
            try:
                variant_path.unlink()
            except OSError:
                pass

    return completed_paths


class DownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("YT Loader")
        self.root.geometry("900x860")

        self.log_queue: queue.Queue[str | tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.output_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.resolution_var = tk.StringVar(value=DEFAULT_RESOLUTION)
        self.channel_url_var = tk.StringVar()
        self.logo_paths: list[Path] = []
        self.logo_opacity_var = tk.IntVar(value=35)
        self.logo_width_var = tk.IntVar(value=22)
        self.status_var = tk.StringVar(value="Готово к загрузке")

        self._build_ui()
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

        channel_entry = ttk.Entry(
            channel_frame,
            textvariable=self.channel_url_var,
        )
        channel_entry.pack(side="left", fill="x", expand=True)
        channel_entry.bind("<Return>", lambda _event: self.start_shorts_import())

        self.shorts_button = ttk.Button(
            channel_frame,
            text="Получить ссылки + метаданные",
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

        self.urls_text = tk.Text(links_frame, wrap="word", height=14, font=("Consolas", 10))
        self.urls_text.pack(fill="both", expand=True)

        logo_frame = ttk.LabelFrame(
            frame, text="Изображение/анимация поверх видео — можно выбрать несколько", padding=12
        )
        logo_frame.pack(fill="x", pady=(16, 0))

        logo_file_frame = ttk.Frame(logo_frame)
        logo_file_frame.pack(fill="x")
        self.logo_listbox = tk.Listbox(logo_file_frame, height=3, exportselection=False)
        self.logo_listbox.pack(side="left", fill="x", expand=True)
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

        logo_settings_frame = ttk.Frame(logo_frame)
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
            to=80,
            increment=1,
            textvariable=self.logo_width_var,
            width=5,
        )
        self.logo_width_spinbox.pack(side="left", padx=(6, 0))
        ttk.Label(
            logo_settings_frame,
            text="Логотип будет по центру снизу с небольшим отступом.",
        ).pack(side="left", padx=(18, 0))

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
            log_frame, wrap="word", height=14, state="disabled", font=("Consolas", 10)
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

    def clear_logos(self) -> None:
        self.logo_paths.clear()
        self.logo_listbox.delete(0, "end")

    def paste_from_clipboard(self) -> None:
        try:
            clipboard_text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "В буфере обмена нет текста.")
            return

        if clipboard_text.strip():
            self.urls_text.insert("end", clipboard_text.strip() + "\n")

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
        self.status_var.set("Получаю ссылки, описания и теги...")
        self._log(f"Получение Shorts и метаданных: {shorts_url}")
        self._log("Полные метаданные читаются отдельно для каждого ролика — это может занять время.")

        self.worker = threading.Thread(
            target=self._shorts_import_worker,
            args=(shorts_url, output_dir),
            daemon=True,
        )
        self.worker.start()

    def _shorts_import_worker(self, shorts_url: str, output_dir: Path) -> None:
        command = [
            str(YTDLP_EXE),
            "--ignore-config",
            "--ignore-errors",
            "--skip-download",
            "--no-warnings",
            "--no-update",
            "--print",
            "%(.{id,title,description,tags,uploader,upload_date})j",
            "--js-runtimes",
            "node",
        ]

        cookies_path = detect_cookies(shorts_url)
        if cookies_path:
            command.extend(["--cookies", str(cookies_path)])
        command.append(shorts_url)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            self.log_queue.put(("SHORTS_ERROR", f"Ошибка запуска yt-dlp: {exc}"))
            return

        records = parse_shorts_metadata(result.stdout)
        if result.returncode != 0 and not records:
            error_text = result.stderr.strip() or f"yt-dlp завершился с кодом {result.returncode}"
            self.log_queue.put(("SHORTS_ERROR", error_text))
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        metadata_path = output_dir / f"shorts_metadata_{timestamp}.csv"
        try:
            write_shorts_metadata_csv(records, metadata_path)
        except OSError as exc:
            self.log_queue.put(("SHORTS_ERROR", f"Не удалось сохранить CSV: {exc}"))
            return

        self.log_queue.put(
            (
                "SHORTS_RESULT",
                {
                    "links": [str(record["url"]) for record in records],
                    "metadata_path": metadata_path,
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
        urls = [line.strip() for line in self.urls_text.get("1.0", "end").splitlines() if line.strip()]
        logo_paths = list(self.logo_paths)
        try:
            logo_opacity = max(5, min(int(self.logo_opacity_var.get()), 100))
            logo_width = max(5, min(int(self.logo_width_var.get()), 80))
        except (tk.TclError, ValueError):
            messagebox.showwarning("Параметры логотипа", "Проверь прозрачность и размер логотипа.")
            return

        if not urls:
            messagebox.showwarning("Нет ссылок", "Добавь хотя бы одну ссылку.")
            return

        if not YTDLP_EXE.exists():
            messagebox.showerror("Не найден yt-dlp", f"Файл не найден:\n{YTDLP_EXE}")
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
        self._set_logo_controls_state("disabled")
        self.status_var.set("Идет загрузка...")
        self._log(f"Старт очереди загрузок, разрешение: {resolution_label}")

        self.worker = threading.Thread(
            target=self._download_worker,
            args=(urls, output_dir, max_height, logo_paths, logo_opacity, logo_width),
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
    ) -> None:
        success_count = 0
        download_dir = output_dir / ".ytloader_downloads" if logo_paths else output_dir
        download_dir.mkdir(parents=True, exist_ok=True)

        for index, url in enumerate(urls, start=1):
            cookies_path = detect_cookies(url)
            self._log(f"[{index}/{len(urls)}] Обработка: {url}")

            command = [
                str(YTDLP_EXE),
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
                command.extend(["--js-runtimes", "node"])

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
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as exc:
                self._log(f"Ошибка запуска: {exc}")
                continue

            assert process.stdout is not None
            downloaded_path: Path | None = None
            for line in process.stdout:
                clean_line = line.rstrip()
                if clean_line.startswith(DOWNLOAD_PATH_MARKER):
                    downloaded_path = Path(clean_line[len(DOWNLOAD_PATH_MARKER) :])
                    self._log(f"Файл загружен: {downloaded_path.name}")
                else:
                    self._log(clean_line)

            return_code = process.wait()
            if return_code == 0:
                if logo_paths:
                    if downloaded_path and downloaded_path.is_file():
                        completed_variants = create_logo_variants(
                            downloaded_path,
                            output_dir,
                            logo_paths,
                            logo_opacity,
                            logo_width,
                            self._log,
                        )
                        if completed_variants:
                            downloaded_path.unlink()
                            self._log(
                                f"Создано вариантов: {len(completed_variants)}/{len(logo_paths)}"
                            )
                        else:
                            fallback_path = output_dir / downloaded_path.name
                            downloaded_path.replace(fallback_path)
                            self._log(
                                "Не удалось создать ни одного варианта; исходное видео "
                                f"сохранено как {fallback_path.name}."
                            )
                    else:
                        self._log("Не удалось определить скачанный файл — логотип не наложен.")
                success_count += 1
                self._log(f"Готово: {url}")
            else:
                self._log(f"Ошибка загрузки, код {return_code}: {url}")

        if logo_paths:
            try:
                download_dir.rmdir()
            except OSError:
                pass
        self.log_queue.put(f"__DONE__:{success_count}/{len(urls)}")

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
                    metadata_path = result_data.get("metadata_path")
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
                    if metadata_path:
                        self._append_log(f"Метаданные сохранены: {metadata_path}")
                        messagebox.showinfo(
                            "Импорт Shorts завершён",
                            f"{status}\n\nОписания и теги сохранены в:\n{metadata_path}",
                        )
                    continue

            if message.startswith("__DONE__:"):
                result = message.split(":", 1)[1]
                self.status_var.set(f"Завершено: {result}")
                self.download_button.config(state="normal")
                self.shorts_button.config(state="normal")
                self.resolution_combo.config(state="readonly")
                self._set_logo_controls_state("normal")
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
        ):
            control.config(state=state)


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
