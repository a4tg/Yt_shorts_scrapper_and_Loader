import base64
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

from server_core import resolve_tool


class AIServiceError(RuntimeError):
    pass


RETRYABLE_AI_STATUS_CODES = {429, 502, 503, 504}


def _setting(name: str, legacy_name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or os.getenv(legacy_name, "").strip() or default


def ai_provider() -> str:
    return _setting("AAP_AI_PROVIDER", "OPENAI_PROVIDER", "aitunnel").lower()


def ai_enabled() -> bool:
    return bool(_setting("AAP_AI_API_KEY", "OPENAI_API_KEY"))


def ai_feature_enabled(feature: str) -> bool:
    return ai_enabled() and feature in ai_public_config()["features"]


def ai_public_config() -> dict[str, object]:
    configured = ai_enabled()
    features = [
        item.strip()
        for item in _setting(
            "AAP_AI_FEATURES",
            "OPENAI_FEATURES",
            "text,image,transcription,clips",
        ).split(",")
        if item.strip() in {"text", "image", "transcription", "clips"}
    ]
    return {
        "enabled": configured,
        "provider": ai_provider(),
        "api_mode": _setting("AAP_AI_API_MODE", "OPENAI_API_MODE", "auto"),
        "features": features if configured else [],
        "text_model": _setting("AAP_AI_TEXT_MODEL", "OPENAI_TEXT_MODEL", "deepseek-v4-flash"),
        "premium_text_model": _setting(
            "AAP_AI_PREMIUM_TEXT_MODEL", "OPENAI_PREMIUM_TEXT_MODEL", "gpt-5.4-mini"
        ),
        "image_model": _setting("AAP_AI_IMAGE_MODEL", "OPENAI_IMAGE_MODEL", "gpt-image-2"),
        "image_quality": _image_quality(),
        "transcription_model": _setting(
            "AAP_AI_TRANSCRIPTION_MODEL", "OPENAI_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"
        ),
        "transcription_timestamp_mode": _transcription_timestamp_mode(),
    }


def _image_quality(value: str | None = None) -> str:
    quality = (value or _setting(
        "AAP_AI_IMAGE_QUALITY", "OPENAI_IMAGE_QUALITY", "low"
    )).strip().lower()
    if quality not in {"low", "medium", "high", "auto"}:
        raise AIServiceError(
            "AAP_AI_IMAGE_QUALITY должен быть low, medium, high или auto."
        )
    return quality


def _transcription_timestamp_mode() -> str:
    mode = _setting(
        "AAP_AI_TRANSCRIPTION_TIMESTAMP_MODE",
        "OPENAI_TRANSCRIPTION_TIMESTAMP_MODE",
        "auto",
    ).lower()
    if mode not in {"auto", "provider", "synthetic"}:
        raise AIServiceError(
            "AAP_AI_TRANSCRIPTION_TIMESTAMP_MODE должен быть auto, provider или synthetic."
        )
    return mode


def _merge_usage(target: dict[str, Any], usage: object) -> None:
    """Accumulate numeric provider usage fields without assuming one vendor schema."""
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            target[key] = round(float(target.get(key) or 0) + float(value), 6)


def _usage_cost_rub(usage: object) -> float:
    if not isinstance(usage, dict):
        return 0.0
    try:
        return max(0.0, float(usage.get("cost_rub") or 0))
    except (TypeError, ValueError):
        return 0.0


def usage_cost_rub(usage: object) -> float:
    """Return a provider-reported RUB cost without trusting its JSON type."""
    return _usage_cost_rub(usage)


def _headers() -> dict[str, str]:
    key = _setting("AAP_AI_API_KEY", "OPENAI_API_KEY")
    if not key:
        raise AIServiceError(
            "AI-функции не настроены: добавьте AAP_AI_API_KEY (или OPENAI_API_KEY) на сервере."
        )
    return {"Authorization": f"Bearer {key}"}


def _base_url() -> str:
    return _setting(
        "AAP_AI_BASE_URL", "OPENAI_BASE_URL", "https://api.aitunnel.ru/v1"
    ).rstrip("/")


def check_ai_connection() -> dict[str, object]:
    """Run an explicit, low-cost text probe and return only non-secret diagnostics."""
    if not ai_enabled():
        raise AIServiceError("AI API-ключ не настроен.")
    started = time.perf_counter()
    result = generate_text(
        "Ответь только словом OK.",
        "Это техническая проверка подключения API.",
        max_output_tokens=8,
    )
    return {
        "status": "ok",
        "provider": ai_provider(),
        "model": str(result.get("model") or ai_public_config()["text_model"]),
        "api_mode": str(result.get("api_mode") or ai_public_config()["api_mode"]),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def _retry_settings() -> tuple[int, float]:
    try:
        attempts = int(_setting("AAP_AI_RETRY_ATTEMPTS", "OPENAI_RETRY_ATTEMPTS", "3"))
    except ValueError:
        attempts = 3
    try:
        base_delay = float(_setting("AAP_AI_RETRY_BASE_SECONDS", "OPENAI_RETRY_BASE_SECONDS", "0.75"))
    except ValueError:
        base_delay = 0.75
    return max(1, min(attempts, 5)), max(0.0, min(base_delay, 10.0))


def _retry_delay(response: httpx.Response | None, attempt: int, base_delay: float) -> float:
    if response is not None:
        raw = response.headers.get("retry-after", "").strip()
        try:
            return max(0.0, min(float(raw), 30.0))
        except ValueError:
            pass
    return min(base_delay * (2 ** max(0, attempt - 1)), 30.0)


def _post_with_retry(url: str, **kwargs: Any) -> httpx.Response:
    """Retry explicit temporary failures without duplicating uncertain timed-out jobs."""
    attempts, base_delay = _retry_settings()
    for attempt in range(1, attempts + 1):
        response: httpx.Response | None = None
        try:
            response = httpx.post(url, **kwargs)
        except httpx.ConnectError:
            if attempt >= attempts:
                raise
        except httpx.HTTPError:
            # A read/write timeout may happen after the provider accepted a paid
            # generation. Retrying it could create a duplicate charge.
            raise
        else:
            if response.status_code not in RETRYABLE_AI_STATUS_CODES or attempt >= attempts:
                return response
        time.sleep(_retry_delay(response, attempt, base_delay))
    raise RuntimeError("unreachable")


def _raise_api_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("error", {}).get("message")
    except (ValueError, AttributeError):
        detail = None
    raise AIServiceError(f"AI-провайдер вернул ошибку {response.status_code}: {detail or 'без описания'}")


def _responses_text(
    prompt: str, instructions: str, model: str, max_output_tokens: int
) -> dict[str, Any]:
    payload = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    response = _post_with_retry(
        f"{_base_url()}/responses",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
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
    return {
        "text": result, "model": data.get("model") or model,
        "usage": data.get("usage") or {}, "api_mode": "responses",
    }


def _chat_completions_text(
    prompt: str, instructions: str, model: str, max_output_tokens: int
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_output_tokens,
    }
    response = _post_with_retry(
        f"{_base_url()}/chat/completions",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    _raise_api_error(response)
    data = response.json()
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if isinstance(content, list):
        content = "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        )
    result = str(content or "").strip()
    if not result:
        raise AIServiceError("AI-провайдер не вернул текст.")
    return {
        "text": result, "model": data.get("model") or model,
        "usage": data.get("usage") or {}, "api_mode": "chat_completions",
    }


def generate_text(
    prompt: str,
    instructions: str,
    *,
    max_output_tokens: int = 1800,
    premium: bool = False,
) -> dict[str, Any]:
    primary_model = _setting(
        "AAP_AI_TEXT_MODEL", "OPENAI_TEXT_MODEL", "deepseek-v4-flash"
    )
    premium_model = _setting(
        "AAP_AI_PREMIUM_TEXT_MODEL", "OPENAI_PREMIUM_TEXT_MODEL", "gpt-5.4-mini"
    )
    model = premium_model if premium and premium_model else primary_model
    mode = _setting("AAP_AI_API_MODE", "OPENAI_API_MODE", "auto").lower()
    if mode not in {"auto", "responses", "chat_completions"}:
        raise AIServiceError("AAP_AI_API_MODE должен быть auto, responses или chat_completions.")
    try:
        if mode == "chat_completions":
            result = _chat_completions_text(prompt, instructions, model, max_output_tokens)
        elif mode == "responses":
            result = _responses_text(prompt, instructions, model, max_output_tokens)
        else:
            try:
                result = _responses_text(prompt, instructions, model, max_output_tokens)
            except AIServiceError as exc:
                message = str(exc)
                if not any(code in message for code in (" 404:", " 405:", " 422:")):
                    raise
                result = _chat_completions_text(
                    prompt, instructions, model, max_output_tokens
                )
    except httpx.HTTPError as exc:
        raise AIServiceError("Не удалось связаться с AI-провайдером.") from exc
    result["provider"] = ai_provider()
    result["model_tier"] = "premium" if premium and premium_model else "standard"
    return result


def generate_image(
    prompt: str,
    *,
    size: str = "1024x1024",
    quality: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    payload = {
        "model": _setting("AAP_AI_IMAGE_MODEL", "OPENAI_IMAGE_MODEL", "gpt-image-2"),
        "prompt": prompt,
        "size": size,
        "quality": _image_quality(quality),
        "output_format": "png",
    }
    try:
        response = _post_with_retry(
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
    return image, {
        "model": payload["model"], "size": size, "quality": payload["quality"],
        "usage": data.get("usage") or {},
        "provider": ai_provider(),
    }


def transcribe_audio(
    audio_path: Path,
    *,
    request_timestamps: bool | None = None,
) -> dict[str, Any]:
    model = _setting(
        "AAP_AI_TRANSCRIPTION_MODEL", "OPENAI_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"
    )
    timestamp_mode = _transcription_timestamp_mode()
    if request_timestamps is None:
        request_timestamps = timestamp_mode != "synthetic"
    form: dict[str, str] = {"model": model}
    if request_timestamps:
        form.update({"response_format": "verbose_json", "timestamp_granularities[]": "segment"})
    try:
        with audio_path.open("rb") as source:
            response = _post_with_retry(
                f"{_base_url()}/audio/transcriptions", headers=_headers(), data=form,
                files={"file": (audio_path.name, source, "audio/mpeg")}, timeout=300,
            )
    except (OSError, httpx.HTTPError) as exc:
        raise AIServiceError("Не удалось отправить аудио на расшифровку.") from exc

    # Some OpenAI-compatible gateways accept the transcription model but not
    # verbose timestamp parameters. A rejected request is safe to retry because
    # the provider did not process it; an ambiguous timeout is never retried.
    if (
        request_timestamps
        and timestamp_mode == "auto"
        and model != "whisper-1"
        and response.status_code in {400, 415, 422}
    ):
        fallback_form = {"model": model, "response_format": "json"}
        try:
            with audio_path.open("rb") as source:
                response = _post_with_retry(
                    f"{_base_url()}/audio/transcriptions",
                    headers=_headers(),
                    data=fallback_form,
                    files={"file": (audio_path.name, source, "audio/mpeg")},
                    timeout=300,
                )
        except (OSError, httpx.HTTPError) as exc:
            raise AIServiceError("Не удалось отправить аудио на расшифровку.") from exc
    _raise_api_error(response)
    result = response.json()
    if not isinstance(result, dict):
        raise AIServiceError("AI-провайдер вернул некорректную расшифровку.")
    result.setdefault("provider", ai_provider())
    result.setdefault("model", model)
    return result


def _synthetic_segments(text: str, duration: float) -> list[dict[str, Any]]:
    """Build approximate local timestamps when an STT model returns text only."""
    normalized = " ".join(str(text or "").split())
    if not normalized or duration <= 0:
        return []
    pieces = [
        piece.strip()
        for piece in re.split(r"(?<=[.!?…])\s+|\n+", normalized)
        if piece.strip()
    ]
    if len(pieces) == 1 and len(normalized.split()) > 24:
        words = normalized.split()
        pieces = [" ".join(words[index:index + 18]) for index in range(0, len(words), 18)]
    weights = [max(1, len(piece.split())) for piece in pieces]
    total_weight = sum(weights)
    elapsed = 0.0
    result: list[dict[str, Any]] = []
    for index, (piece, weight) in enumerate(zip(pieces, weights)):
        start = elapsed
        elapsed = duration if index == len(pieces) - 1 else elapsed + duration * weight / total_weight
        result.append({
            "start": round(start, 3),
            "end": round(max(start, elapsed), 3),
            "text": piece,
            "timestamp_source": "estimated",
        })
    return result


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
    chunk_seconds = max(
        600,
        min(
            int(_setting(
                "AAP_AI_AUDIO_CHUNK_SECONDS", "OPENAI_AUDIO_CHUNK_SECONDS", "2700"
            )),
            3300,
        ),
    )
    try:
        fallback_chunk_seconds = int(_setting(
            "AAP_AI_TIMESTAMP_FALLBACK_CHUNK_SECONDS",
            "OPENAI_TIMESTAMP_FALLBACK_CHUNK_SECONDS",
            "300",
        ))
    except ValueError:
        fallback_chunk_seconds = 300
    fallback_chunk_seconds = max(60, min(fallback_chunk_seconds, 600))
    timestamp_mode = _transcription_timestamp_mode()
    model = _setting(
        "AAP_AI_TRANSCRIPTION_MODEL", "OPENAI_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"
    )
    native_timestamps: bool | None
    if timestamp_mode == "provider" or model == "whisper-1":
        native_timestamps = True
    elif timestamp_mode == "synthetic":
        native_timestamps = False
    else:
        native_timestamps = None

    combined_text: list[str] = []
    combined_segments: list[dict[str, Any]] = []
    combined_usage: dict[str, Any] = {}
    start = 0.0
    index = 0
    while start < duration:
        active_chunk_seconds = chunk_seconds if native_timestamps is True else fallback_chunk_seconds
        length = min(active_chunk_seconds, duration - start)
        index += 1
        audio_path = temporary_dir / f"audio_{index:03d}.mp3"
        log(f"Извлекаю аудио: фрагмент с {int(start)} сек.")
        extract_audio(source, audio_path, start=start, duration=length)
        log(f"Расшифровываю: фрагмент {index}")
        partial = transcribe_audio(
            audio_path,
            request_timestamps=native_timestamps is not False,
        )
        partial_text = str(partial.get("text") or "").strip()
        combined_text.append(partial_text)
        partial_segments = partial.get("segments") or []
        if native_timestamps is None:
            native_timestamps = bool(partial_segments)
            if not native_timestamps:
                log("Провайдер не вернул таймкоды; использую локальную оценку по фрагментам")
        if not partial_segments:
            partial_segments = _synthetic_segments(partial_text, length)
        for segment in partial_segments:
            shifted = dict(segment)
            shifted["start"] = float(segment.get("start") or 0) + start
            shifted["end"] = float(segment.get("end") or 0) + start
            combined_segments.append(shifted)
        _merge_usage(combined_usage, partial.get("usage"))
        audio_path.unlink(missing_ok=True)
        start += length
    return {
        "text": "\n".join(combined_text).strip(),
        "segments": combined_segments,
        "duration": duration,
        "usage": combined_usage,
        "provider": ai_provider(),
        "model": model,
        "timestamps": "provider" if native_timestamps else "estimated",
    }


def _compact_transcript(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge tiny Whisper segments so long recordings stay within provider limits."""
    compact: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in segments:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if end <= start or not text:
            continue
        if current is None:
            current = {"start": start, "end": end, "text": text}
            continue
        combined = f"{current['text']} {text}".strip()
        if start - float(current["end"]) <= 2 and (
            float(current["end"]) - float(current["start"]) < 18
            or len(combined) <= 420
        ):
            current["end"] = end
            current["text"] = combined[:700]
        else:
            compact.append(current)
            current = {"start": start, "end": end, "text": text}
    if current is not None:
        compact.append(current)
    return compact


def _validate_clip_candidates(
    candidates: object,
    *,
    duration: float,
    count: int,
    min_seconds: int,
    max_seconds: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in candidates if isinstance(candidates, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start, end = max(0.0, float(item["start"])), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        end = min(duration, end)
        clip_duration = end - start
        overlaps = any(start < saved["end"] and end > saved["start"] for saved in result)
        if min_seconds <= clip_duration <= max_seconds and not overlaps:
            try:
                score = max(0, min(100, int(item.get("score") or 50)))
            except (TypeError, ValueError):
                score = 50
            result.append({
                "start": round(start, 3), "end": round(end, 3),
                "title": str(item.get("title") or "Клип")[:120],
                "reason": str(item.get("reason") or "")[:500],
                "score": score,
            })
        if len(result) >= count:
            break
    return result


def select_highlights(
    transcript: dict[str, Any],
    count: int,
    min_seconds: int,
    max_seconds: int,
    *,
    usage_sink: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    segments = transcript.get("segments") or []
    compact = _compact_transcript(segments)
    if not compact:
        raise AIServiceError("Расшифровка не содержит текста с временными метками.")
    media_length = float(transcript.get("duration") or compact[-1]["end"])
    batch_size = max(40, min(int(os.getenv("AAP_AI_CLIP_SEGMENTS_PER_BATCH", "160")), 240))
    requested_per_batch = min(8, max(3, count * 2))
    candidates: list[dict[str, Any]] = []
    for offset in range(0, len(compact), batch_size):
        batch = compact[offset:offset + batch_size]
        prompt = (
            f"Выбери до {requested_per_batch} самостоятельных ярких фрагментов для вертикальных клипов. "
            f"Длительность каждого от {min_seconds} до {max_seconds} секунд. "
            "Оцени удержание внимания, понятность без внешнего контекста, сильный заход и завершённость мысли. "
            "Не пересекай фрагменты. Верни только JSON-массив объектов "
            "start, end, title, reason, score (0..100). Времена должны опираться на сегменты.\n"
            + json.dumps(batch, ensure_ascii=False)
        )
        response = generate_text(
            prompt,
            "Ты опытный редактор коротких видео. Не выдумывай содержание и отвечай только валидным JSON.",
            max_output_tokens=1800,
        )
        if usage_sink is not None:
            _merge_usage(usage_sink, response.get("usage"))
        raw = response["text"].strip().removeprefix("```json").removesuffix("```").strip()
        try:
            batch_candidates = json.loads(raw)
        except json.JSONDecodeError as exc:
            premium_model = _setting(
                "AAP_AI_PREMIUM_TEXT_MODEL", "OPENAI_PREMIUM_TEXT_MODEL"
            )
            primary_model = _setting(
                "AAP_AI_TEXT_MODEL", "OPENAI_TEXT_MODEL", "deepseek-v4-flash"
            )
            if not premium_model or premium_model == primary_model:
                raise AIServiceError("AI не смог вернуть корректный план клипов.") from exc
            response = generate_text(
                prompt,
                "Ты опытный редактор коротких видео. Не выдумывай содержание и отвечай только валидным JSON.",
                max_output_tokens=1800,
                premium=True,
            )
            if usage_sink is not None:
                _merge_usage(usage_sink, response.get("usage"))
            raw = response["text"].strip().removeprefix("```json").removesuffix("```").strip()
            try:
                batch_candidates = json.loads(raw)
            except json.JSONDecodeError as premium_exc:
                raise AIServiceError("AI не смог вернуть корректный план клипов.") from premium_exc
        if isinstance(batch_candidates, list):
            candidates.extend(item for item in batch_candidates if isinstance(item, dict))
    def candidate_score(item: dict[str, Any]) -> float:
        try:
            return float(item.get("score") or 0)
        except (TypeError, ValueError):
            return 0

    candidates.sort(key=candidate_score, reverse=True)
    result = _validate_clip_candidates(
        candidates, duration=media_length, count=count,
        min_seconds=min_seconds, max_seconds=max_seconds,
    )
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
