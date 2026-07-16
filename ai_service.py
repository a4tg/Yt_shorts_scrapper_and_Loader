import base64
import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

from server_core import resolve_tool


class AIServiceError(RuntimeError):
    pass


def ai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def ai_public_config() -> dict[str, object]:
    return {
        "enabled": ai_enabled(),
        "provider": "openai",
        "features": ["text", "image", "transcription", "clips"] if ai_enabled() else [],
        "text_model": os.getenv("OPENAI_TEXT_MODEL", "gpt-5.4-mini"),
        "image_model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
        "transcription_model": os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1"),
    }


def _headers() -> dict[str, str]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise AIServiceError("AI-функции не настроены: добавьте OPENAI_API_KEY на сервере.")
    return {"Authorization": f"Bearer {key}"}


def _base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _raise_api_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("error", {}).get("message")
    except (ValueError, AttributeError):
        detail = None
    raise AIServiceError(f"AI-провайдер вернул ошибку {response.status_code}: {detail or 'без описания'}")


def generate_text(prompt: str, instructions: str, *, max_output_tokens: int = 1800) -> dict[str, Any]:
    payload = {
        "model": os.getenv("OPENAI_TEXT_MODEL", "gpt-5.4-mini"),
        "instructions": instructions,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    try:
        response = httpx.post(
            f"{_base_url()}/responses", headers={**_headers(), "Content-Type": "application/json"},
            json=payload, timeout=90,
        )
    except httpx.HTTPError as exc:
        raise AIServiceError("Не удалось связаться с AI-провайдером.") from exc
    _raise_api_error(response)
    data = response.json()
    text_parts = [
        part.get("text", "")
        for item in data.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    ]
    result = "\n".join(part for part in text_parts if part).strip()
    if not result:
        raise AIServiceError("AI-провайдер не вернул текст.")
    return {"text": result, "model": data.get("model") or payload["model"], "usage": data.get("usage") or {}}


def generate_image(prompt: str, *, size: str = "1024x1024") -> tuple[bytes, dict[str, Any]]:
    payload = {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
        "prompt": prompt,
        "size": size,
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
        "output_format": "png",
    }
    try:
        response = httpx.post(
            f"{_base_url()}/images/generations",
            headers={**_headers(), "Content-Type": "application/json"}, json=payload, timeout=180,
        )
    except httpx.HTTPError as exc:
        raise AIServiceError("Не удалось связаться с сервисом генерации изображений.") from exc
    _raise_api_error(response)
    data = response.json()
    item = (data.get("data") or [{}])[0]
    encoded = item.get("b64_json")
    if not encoded:
        raise AIServiceError("AI-провайдер не вернул изображение.")
    try:
        image = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise AIServiceError("AI-провайдер вернул повреждённое изображение.") from exc
    return image, {"model": payload["model"], "size": size, "usage": data.get("usage") or {}}


def transcribe_audio(audio_path: Path) -> dict[str, Any]:
    model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
    form: dict[str, str] = {"model": model}
    if model == "whisper-1":
        form.update({"response_format": "verbose_json", "timestamp_granularities[]": "segment"})
    try:
        with audio_path.open("rb") as source:
            response = httpx.post(
                f"{_base_url()}/audio/transcriptions", headers=_headers(), data=form,
                files={"file": (audio_path.name, source, "audio/mpeg")}, timeout=300,
            )
    except (OSError, httpx.HTTPError) as exc:
        raise AIServiceError("Не удалось отправить аудио на расшифровку.") from exc
    _raise_api_error(response)
    return response.json()


def media_duration(source: Path) -> float:
    result = subprocess.run(
        [resolve_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(source)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise AIServiceError("Не удалось определить длительность исходного видео.") from exc
    if result.returncode != 0 or duration <= 0:
        raise AIServiceError("Не удалось определить длительность исходного видео.")
    return duration


def extract_audio(source: Path, target: Path, *, start: float = 0, duration: float | None = None) -> None:
    timing = ["-ss", f"{start:.3f}"] if start else []
    if duration is not None:
        timing += ["-t", f"{duration:.3f}"]
    result = subprocess.run(
        [resolve_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *timing, "-i", str(source),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(target)],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        raise AIServiceError(f"FFmpeg не смог извлечь аудио: {result.stderr[-500:]}")
    if target.stat().st_size > 24 * 1024 * 1024:
        raise AIServiceError("Аудиодорожка длиннее лимита AI. Уменьшите видео или битрейт.")


def transcribe_media(source: Path, temporary_dir: Path, log: Callable[[str], None]) -> dict[str, Any]:
    """Transcribe arbitrarily long media in API-safe chunks with absolute timestamps."""
    duration = media_duration(source)
    chunk_seconds = max(600, min(int(os.getenv("OPENAI_AUDIO_CHUNK_SECONDS", "2700")), 3300))
    combined_text: list[str] = []
    combined_segments: list[dict[str, Any]] = []
    chunk_count = max(1, int((duration + chunk_seconds - 1) // chunk_seconds))
    for index in range(chunk_count):
        start = index * chunk_seconds
        length = min(chunk_seconds, duration - start)
        audio_path = temporary_dir / f"audio_{index + 1:03d}.mp3"
        log(f"Извлекаю аудио: часть {index + 1} из {chunk_count}")
        extract_audio(source, audio_path, start=start, duration=length)
        log(f"Расшифровываю: часть {index + 1} из {chunk_count}")
        partial = transcribe_audio(audio_path)
        combined_text.append(str(partial.get("text") or ""))
        for segment in partial.get("segments") or []:
            shifted = dict(segment)
            shifted["start"] = float(segment.get("start") or 0) + start
            shifted["end"] = float(segment.get("end") or 0) + start
            combined_segments.append(shifted)
        audio_path.unlink(missing_ok=True)
    return {"text": "\n".join(combined_text).strip(), "segments": combined_segments, "duration": duration}


def select_highlights(transcript: dict[str, Any], count: int, min_seconds: int, max_seconds: int) -> list[dict[str, Any]]:
    segments = transcript.get("segments") or []
    compact = [{"start": x.get("start"), "end": x.get("end"), "text": x.get("text", "")} for x in segments]
    if not compact:
        raise AIServiceError("Расшифровка не содержит временных меток. Используйте модель whisper-1.")
    prompt = (
        f"Выбери {count} самостоятельных ярких фрагментов для вертикальных клипов. "
        f"Длительность каждого от {min_seconds} до {max_seconds} секунд. Не пересекай фрагменты. "
        "Верни только JSON-массив объектов start, end, title, reason. Времена должны опираться на сегменты.\n"
        + json.dumps(compact, ensure_ascii=False)
    )
    response = generate_text(prompt, "Ты опытный редактор коротких видео. Отвечай только валидным JSON.", max_output_tokens=1400)
    raw = response["text"].strip().removeprefix("```json").removesuffix("```").strip()
    try:
        candidates = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIServiceError("AI не смог вернуть корректный план клипов.") from exc
    result: list[dict[str, Any]] = []
    for item in candidates if isinstance(candidates, list) else []:
        try:
            start, end = max(0.0, float(item["start"])), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = end - start
        if min_seconds <= duration <= max_seconds:
            result.append({"start": start, "end": end, "title": str(item.get("title") or "Клип")[:120], "reason": str(item.get("reason") or "")[:500]})
        if len(result) >= count:
            break
    if not result:
        raise AIServiceError("AI не нашёл подходящих фрагментов с заданной длительностью.")
    return result


def render_vertical_clips(source: Path, clips: list[dict[str, Any]], output_dir: Path, log: Callable[[str], None]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    vf = "[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=28[blur];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[front];[blur][front]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
    for index, clip in enumerate(clips, 1):
        target = output_dir / f"clip_{index:02d}.mp4"
        log(f"Рендер клипа {index} из {len(clips)}")
        result = subprocess.run(
            [resolve_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{clip['start']:.3f}", "-t", f"{clip['end'] - clip['start']:.3f}", "-i", str(source),
             "-filter_complex", vf, "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
             "-crf", os.getenv("FFMPEG_CRF", "22"), "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target)],
            capture_output=True, text=True, timeout=3600,
        )
        if result.returncode != 0:
            raise AIServiceError(f"FFmpeg не смог собрать клип: {result.stderr[-500:]}")
        rendered.append(target)
    archive = output_dir / "ai_clips.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("clips.json", json.dumps(clips, ensure_ascii=False, indent=2))
        for path in rendered:
            bundle.write(path, path.name)
    return archive
