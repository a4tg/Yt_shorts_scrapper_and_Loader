import base64
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import ai_service
import server
from auth_service import attempt_limiter
from saas_models import ContentAttachment, ContentItem, Job


PASSWORD = "correct horse battery staple"


def register_user() -> TestClient:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={"email": f"ai-{uuid.uuid4().hex}@example.com", "password": PASSWORD},
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client


def csrf(client: TestClient) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": client.cookies.get("yt_loader_csrf")}


def project_id(client: TestClient) -> str:
    workspace = client.get("/api/workspaces").json()[0]
    return client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]["id"]


def response(payload: dict) -> MagicMock:
    result = MagicMock()
    result.is_success = True
    result.status_code = 200
    result.headers = {}
    result.json.return_value = payload
    return result


def test_generate_text_uses_responses_api_and_extracts_output(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    upstream = response({
        "model": "test-model",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Готовый пост"}]}],
        "usage": {"total_tokens": 42},
    })
    with patch("ai_service.httpx.post", return_value=upstream) as call:
        result = ai_service.generate_text("Тема", "Инструкция")
    assert result["text"] == "Готовый пост"
    assert call.call_args.args[0].endswith("/responses")
    assert call.call_args.kwargs["json"]["store"] is False


def test_generate_text_falls_back_to_chat_completions(monkeypatch) -> None:
    monkeypatch.setenv("AAP_AI_PROVIDER", "aitunnel")
    monkeypatch.setenv("AAP_AI_API_KEY", "tunnel-key")
    monkeypatch.setenv("AAP_AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AAP_AI_API_MODE", "auto")
    unavailable = MagicMock()
    unavailable.is_success = False
    unavailable.status_code = 404
    unavailable.json.return_value = {
        "error": {"message": "Responses API is unavailable"}
    }
    chat = response({
        "model": "compatible-model",
        "choices": [{"message": {"content": "Готовый ответ"}}],
        "usage": {"total_tokens": 21},
    })
    with patch("ai_service.httpx.post", side_effect=[unavailable, chat]) as call:
        result = ai_service.generate_text("Тема", "Инструкция")
    assert result["text"] == "Готовый ответ"
    assert result["provider"] == "aitunnel"
    assert result["api_mode"] == "chat_completions"
    assert call.call_args_list[1].args[0].endswith("/chat/completions")


def test_generate_text_can_select_premium_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AAP_AI_API_MODE", "chat_completions")
    monkeypatch.setenv("AAP_AI_TEXT_MODEL", "cheap-model")
    monkeypatch.setenv("AAP_AI_PREMIUM_TEXT_MODEL", "premium-model")
    upstream = response({
        "choices": [{"message": {"content": "Premium result"}}],
        "usage": {"cost_rub": 0.5},
    })
    with patch("ai_service.httpx.post", return_value=upstream) as call:
        result = ai_service.generate_text("Topic", "Instruction", premium=True)
    assert call.call_args.kwargs["json"]["model"] == "premium-model"
    assert result["model_tier"] == "premium"


def test_generate_text_retries_explicit_temporary_provider_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AAP_AI_RETRY_BASE_SECONDS", "0")
    busy = MagicMock()
    busy.is_success = False
    busy.status_code = 429
    busy.headers = {"retry-after": "0"}
    busy.json.return_value = {"error": {"message": "rate limited"}}
    ready = response({
        "model": "test-model",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Готово"}]}],
    })
    with patch("ai_service.httpx.post", side_effect=[busy, ready]) as call:
        result = ai_service.generate_text("Тема", "Инструкция")
    assert result["text"] == "Готово"
    assert call.call_count == 2


def test_ai_request_does_not_retry_ambiguous_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch(
        "ai_service.httpx.post",
        side_effect=httpx.ReadTimeout("provider response timed out"),
    ) as call:
        try:
            ai_service.generate_text("Тема", "Инструкция")
        except ai_service.AIServiceError:
            pass
        else:
            raise AssertionError("Timeout must become a controlled AIServiceError")
    assert call.call_count == 1


def test_generate_image_decodes_base64(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    upstream = response({"data": [{"b64_json": base64.b64encode(b"png-data").decode()}]})
    with patch("ai_service.httpx.post", return_value=upstream):
        image, metadata = ai_service.generate_image("Баннер")
    assert image == b"png-data"
    assert metadata["size"] == "1024x1024"


def test_generate_image_sends_explicit_cost_controlling_quality(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    upstream = response({
        "data": [{"b64_json": base64.b64encode(b"png-data").decode()}],
        "usage": {"cost_rub": 1.87},
    })
    with patch("ai_service.httpx.post", return_value=upstream) as call:
        _image, metadata = ai_service.generate_image("Banner", quality="low")
    assert call.call_args.kwargs["json"]["quality"] == "low"
    assert metadata["quality"] == "low"
    assert metadata["usage"]["cost_rub"] == 1.87


def test_usage_cost_rub_accepts_numeric_strings_and_rejects_invalid_values() -> None:
    assert ai_service.usage_cost_rub({"cost_rub": "1.87"}) == 1.87
    assert ai_service.usage_cost_rub({"cost_rub": "invalid"}) == 0
    assert ai_service.usage_cost_rub(None) == 0


def test_highlight_selection_validates_duration() -> None:
    transcript = {
        "duration": 70,
        "segments": [{"start": 0, "end": 70, "text": "Фрагмент"}],
    }
    answer = {"text": '[{"start": 5, "end": 35, "title": "Момент", "reason": "Сильный заход"}]'}
    with patch("ai_service.generate_text", return_value=answer):
        clips = ai_service.select_highlights(transcript, 1, 20, 60)
    assert clips == [{
        "start": 5.0, "end": 35.0, "title": "Момент",
        "reason": "Сильный заход", "score": 50,
    }]


def test_highlight_selection_rejects_overlap_and_out_of_bounds() -> None:
    transcript = {
        "duration": 120,
        "segments": [
            {"start": 0, "end": 60, "text": "Первый смысловой фрагмент"},
            {"start": 60, "end": 120, "text": "Второй смысловой фрагмент"},
        ],
    }
    answer = {"text": """[
      {"start": 10, "end": 50, "title": "Первый", "score": 90},
      {"start": 20, "end": 55, "title": "Пересечение", "score": 80},
      {"start": 90, "end": 140, "title": "Обрезается", "score": 70}
    ]"""}
    with patch("ai_service.generate_text", return_value=answer):
        clips = ai_service.select_highlights(transcript, 3, 20, 60)
    assert [item["title"] for item in clips] == ["Первый", "Обрезается"]
    assert clips[-1]["end"] == 120


def test_long_media_transcription_restores_absolute_timestamps(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_AUDIO_CHUNK_SECONDS", "600")
    monkeypatch.setenv("AAP_AI_TRANSCRIPTION_TIMESTAMP_MODE", "provider")
    with (
        patch("ai_service.media_duration", return_value=1200),
        patch("ai_service.extract_audio") as extract,
        patch("ai_service.transcribe_audio", side_effect=[
            {"text": "Первая", "segments": [{"start": 10, "end": 20, "text": "Первая"}]},
            {"text": "Вторая", "segments": [{"start": 5, "end": 15, "text": "Вторая"}]},
        ]),
    ):
        result = ai_service.transcribe_media(Path("source.mp4"), tmp_path, lambda _message: None)
    assert extract.call_count == 2
    assert result["segments"][0]["start"] == 10
    assert result["segments"][1]["start"] == 605


def test_text_only_transcription_builds_estimated_timestamps_and_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AAP_AI_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setenv("AAP_AI_TRANSCRIPTION_TIMESTAMP_MODE", "auto")
    monkeypatch.setenv("AAP_AI_TIMESTAMP_FALLBACK_CHUNK_SECONDS", "300")
    with (
        patch("ai_service.media_duration", return_value=600),
        patch("ai_service.extract_audio") as extract,
        patch("ai_service.transcribe_audio", side_effect=[
            {"text": "First sentence. Second sentence.", "usage": {"seconds": 300, "cost_rub": 0.65}},
            {"text": "Third sentence. Fourth sentence.", "usage": {"seconds": 300, "cost_rub": 0.65}},
        ]) as transcribe,
    ):
        result = ai_service.transcribe_media(Path("source.mp4"), tmp_path, lambda _message: None)
    assert extract.call_count == 2
    assert transcribe.call_args_list[0].kwargs["request_timestamps"] is True
    assert transcribe.call_args_list[1].kwargs["request_timestamps"] is False
    assert result["timestamps"] == "estimated"
    assert result["segments"][0]["timestamp_source"] == "estimated"
    assert result["segments"][-1]["end"] == 600
    assert result["usage"]["seconds"] == 600
    assert result["usage"]["cost_rub"] == 1.3


def test_transcription_retries_only_rejected_timestamp_parameters(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AAP_AI_API_KEY", "test-key")
    monkeypatch.setenv("AAP_AI_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setenv("AAP_AI_TRANSCRIPTION_TIMESTAMP_MODE", "auto")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    rejected = MagicMock()
    rejected.is_success = False
    rejected.status_code = 422
    rejected.headers = {}
    rejected.json.return_value = {"error": {"message": "verbose_json unsupported"}}
    accepted = response({"text": "Transcript", "usage": {"cost_rub": 0.13}})
    with patch("ai_service.httpx.post", side_effect=[rejected, accepted]) as call:
        result = ai_service.transcribe_audio(audio)
    assert call.call_count == 2
    assert call.call_args_list[0].kwargs["data"]["response_format"] == "verbose_json"
    assert call.call_args_list[1].kwargs["data"]["response_format"] == "json"
    assert result["text"] == "Transcript"


def test_highlight_selection_retries_invalid_json_with_premium_model(monkeypatch) -> None:
    monkeypatch.setenv("AAP_AI_TEXT_MODEL", "cheap-model")
    monkeypatch.setenv("AAP_AI_PREMIUM_TEXT_MODEL", "premium-model")
    transcript = {
        "duration": 70,
        "segments": [{"start": 0, "end": 70, "text": "A complete fragment"}],
    }
    usage: dict[str, object] = {}
    with patch("ai_service.generate_text", side_effect=[
        {"text": "not-json", "usage": {"cost_rub": 0.01}},
        {"text": '[{"start": 5, "end": 35, "title": "Moment"}]', "usage": {"cost_rub": 0.2}},
    ]) as generate:
        clips = ai_service.select_highlights(
            transcript, 1, 20, 60, usage_sink=usage
        )
    assert clips[0]["title"] == "Moment"
    assert generate.call_args_list[1].kwargs["premium"] is True
    assert usage["cost_rub"] == 0.21


def test_ai_text_endpoint_is_project_scoped_and_reserves_credit(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = register_user(); project = project_id(client)
    result = client.post(
        "/api/ai/text", headers=csrf(client),
        json={"project_id": project, "action": "post", "prompt": "Напиши пост о запуске"},
    )
    assert result.status_code == 202, result.text
    with server.SessionLocal() as db:
        job = db.get(Job, result.json()["id"])
        assert job.project_id == project
        assert job.kind == "ai_text"
        assert job.credits_reserved == 1


def test_ai_image_endpoint_preserves_requested_quality(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = register_user(); project = project_id(client)
    result = client.post(
        "/api/ai/images",
        headers=csrf(client),
        json={
            "project_id": project,
            "prompt": "Campaign banner",
            "size": "1024x1024",
            "quality": "low",
        },
    )
    assert result.status_code == 202, result.text
    with server.SessionLocal() as db:
        job = db.get(Job, result.json()["id"])
        assert job.request_payload["quality"] == "low"


def test_ai_clips_endpoint_accepts_project_video_attachment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(server, "media_duration", lambda _path: 600.0)
    client = register_user(); project = project_id(client)
    created = client.post(
        f"/api/projects/{project}/content", headers=csrf(client),
        json={"title": "Исходник", "item_type": "video"},
    )
    assert created.status_code == 201, created.text
    target = server.CONTENT_DIR / project / created.json()["id"] / f"{uuid.uuid4().hex}_source.mp4"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"video")
    try:
        with server.SessionLocal() as db:
            attachment = ContentAttachment(
                project_id=project, content_item_id=created.json()["id"],
                uploaded_by_user_id=client.get("/api/auth/me").json()["id"],
                original_name="source.mp4", storage_path=str(target.resolve()), mime_type="video/mp4",
                source_type="upload", size_bytes=5,
            )
            db.add(attachment); db.commit(); attachment_id = attachment.id
        result = client.post(
            "/api/ai/clips", headers=csrf(client),
            json={"project_id": project, "attachment_id": attachment_id, "count": 1, "min_seconds": 20, "max_seconds": 60},
        )
        assert result.status_code == 202, result.text
        assert result.json()["kind"] == "ai_clips"
        assert result.json()["credits_reserved"] == 5
        with server.SessionLocal() as db:
            job = db.get(Job, result.json()["id"])
            assert job.request_payload["source_duration_seconds"] == 600.0
    finally:
        target.unlink(missing_ok=True)


def test_ai_config_never_exposes_api_key(monkeypatch) -> None:
    client = register_user(); monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    payload = client.get("/api/ai/config").json()
    assert payload["enabled"] is True
    assert "super-secret" not in str(payload)


def test_ai_config_supports_provider_capabilities(monkeypatch) -> None:
    client = register_user()
    monkeypatch.setenv("AAP_AI_API_KEY", "provider-secret")
    monkeypatch.setenv("AAP_AI_PROVIDER", "aitunnel")
    monkeypatch.setenv("AAP_AI_API_MODE", "chat_completions")
    monkeypatch.setenv("AAP_AI_FEATURES", "text,transcription,clips")
    payload = client.get("/api/ai/config").json()
    assert payload["provider"] == "aitunnel"
    assert payload["api_mode"] == "chat_completions"
    assert payload["features"] == ["text", "transcription", "clips"]
    assert "provider-secret" not in str(payload)


def test_disabled_ai_feature_is_rejected_before_job_creation(monkeypatch) -> None:
    client = register_user()
    project = project_id(client)
    monkeypatch.setenv("AAP_AI_API_KEY", "text-only-secret")
    monkeypatch.setenv("AAP_AI_FEATURES", "text")
    response = client.post(
        "/api/ai/images",
        headers=csrf(client),
        json={"project_id": project, "prompt": "Баннер", "size": "1024x1024"},
    )
    assert response.status_code == 503
