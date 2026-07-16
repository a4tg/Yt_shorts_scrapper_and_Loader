from unittest.mock import patch

from fastapi.testclient import TestClient

import server


def test_liveness_is_public_and_request_id_is_returned() -> None:
    client = TestClient(server.app)
    response = client.get("/api/health/live", headers={"X-Request-ID": "probe-123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "probe-123"


def test_invalid_request_id_is_replaced() -> None:
    response = TestClient(server.app).get(
        "/api/health/live", headers={"X-Request-ID": "bad id with spaces"}
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad id with spaces"
    assert len(response.headers["X-Request-ID"]) == 32


def test_readiness_reports_database_workers_disk_and_queue() -> None:
    with patch("server.check_database", return_value=True):
        response = TestClient(server.app).get("/api/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["workers"] == "ok"
    assert payload["disk"] == "ok"
    assert isinstance(payload["queue"], dict)


def test_metrics_require_separate_bearer_secret(monkeypatch) -> None:
    client = TestClient(server.app)
    monkeypatch.delenv("YT_LOADER_METRICS_TOKEN", raising=False)
    assert client.get("/api/metrics").status_code == 404
    monkeypatch.setenv("YT_LOADER_METRICS_TOKEN", "metrics-secret")
    assert client.get("/api/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 404
    response = client.get(
        "/api/metrics", headers={"Authorization": "Bearer metrics-secret"}
    )
    assert response.status_code == 200
    assert "yt_loader_http_requests_total" in response.text
    assert "metrics-secret" not in response.text


def test_backup_script_covers_database_files_and_retention() -> None:
    script = (server.BASE_DIR / "deploy" / "backup-data.sh").read_text(encoding="utf-8")
    assert "pg_dump" in script
    assert "server_data" in script
    assert "YT_LOADER_BACKUP_RETENTION_DAYS" in script
