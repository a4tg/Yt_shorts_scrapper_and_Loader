import base64
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_generate_image_decodes_base64(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    upstream = response({"data": [{"b64_json": base64.b64encode(b"png-data").decode()}]})
    with patch("ai_service.httpx.post", return_value=upstream):
        image, metadata = ai_service.generate_image("Баннер")
    assert image == b"png-data"
    assert metadata["size"] == "1024x1024"


def test_highlight_selection_validates_duration() -> None:
    transcript = {"segments": [{"start": 0, "end": 70, "text": "Фрагмент"}]}
    answer = {"text": '[{"start": 5, "end": 35, "title": "Момент", "reason": "Сильный заход"}]'}
    with patch("ai_service.generate_text", return_value=answer):
        clips = ai_service.select_highlights(transcript, 1, 20, 60)
    assert clips == [{"start": 5.0, "end": 35.0, "title": "Момент", "reason": "Сильный заход"}]


def test_long_media_transcription_restores_absolute_timestamps(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_AUDIO_CHUNK_SECONDS", "600")
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


def test_ai_text_endpoint_is_project_scoped_and_reserves_credit() -> None:
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


def test_ai_clips_endpoint_accepts_project_video_attachment() -> None:
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
    finally:
        target.unlink(missing_ok=True)


def test_ai_config_never_exposes_api_key(monkeypatch) -> None:
    client = register_user(); monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    payload = client.get("/api/ai/config").json()
    assert payload["enabled"] is True
    assert "super-secret" not in str(payload)
