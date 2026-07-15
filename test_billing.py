import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select

import server
from auth_service import attempt_limiter
from billing_service import (
    InsufficientCreditsError,
    credit_snapshot,
    grant_credits,
)
from job_queue import ProcessedJob
from saas_models import CreditLedger, Job, User


def registered_client() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"billing-{uuid.uuid4().hex}@example.com",
            "password": "correct horse battery staple",
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    client.headers.update(
        {
            "Origin": "http://testserver",
            "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
        }
    )
    return client, response.json()


def mark_running(job_id: str) -> None:
    with server.SessionLocal() as db:
        record = db.get(Job, job_id)
        record.status = "running"
        record.worker_id = server.manager.worker_id
        db.commit()


def test_registration_receives_starter_credits_and_plan_catalog() -> None:
    client, user = registered_client()
    assert user["credit_balance"] == 5
    assert user["credit_total"] == 5
    assert user["credits_reserved"] == 0

    summary = client.get("/api/billing/summary").json()
    assert summary["available"] == 5
    assert summary["plan"]["id"] == "free"
    plans = client.get("/api/billing/plans").json()
    assert [plan["id"] for plan in plans] == ["free", "creator", "studio"]


def test_job_reserves_then_charges_credit_on_success() -> None:
    client, user = registered_client()
    job = server.manager.create("import", {"limit": 1}, str(user["id"]))
    reserved = client.get("/api/billing/summary").json()
    assert (reserved["balance"], reserved["reserved"], reserved["available"]) == (5, 1, 4)
    assert job["credits_reserved"] == 1

    mark_running(str(job["id"]))
    server.manager._finish_success(
        str(job["id"]), ProcessedJob(result={"count": 0})
    )

    charged = client.get("/api/billing/summary").json()
    assert (charged["balance"], charged["reserved"], charged["available"]) == (4, 0, 4)
    finished = server.manager.get(str(job["id"]))
    assert finished["credits_reserved"] == 0
    assert finished["credits_spent"] == 1
    ledger = client.get("/api/billing/ledger").json()
    assert any(entry["job_id"] == job["id"] and entry["amount"] == -1 for entry in ledger)


def test_failed_job_releases_reservation_without_charge() -> None:
    client, user = registered_client()
    job = server.manager.create("download", {"overlays": []}, str(user["id"]))
    mark_running(str(job["id"]))
    server.manager._finish_error(str(job["id"]), RuntimeError("download failed"))

    summary = client.get("/api/billing/summary").json()
    assert (summary["balance"], summary["reserved"], summary["available"]) == (5, 0, 5)
    assert server.manager.get(str(job["id"]))["credits_spent"] == 0


def test_multiple_overlay_variants_reserve_multiple_credits() -> None:
    client, user = registered_client()
    job = server.manager.create(
        "download",
        {"overlays": [{"path": "1"}, {"path": "2"}, {"path": "3"}]},
        str(user["id"]),
    )
    assert job["credits_reserved"] == 3
    assert client.get("/api/billing/summary").json()["available"] == 2


def test_import_releases_unused_estimate_after_actual_count_is_known() -> None:
    client, user = registered_client()
    job = server.manager.create("import", {"limit": 250}, str(user["id"]))
    assert job["credits_reserved"] == 3
    assert client.get("/api/billing/summary").json()["available"] == 2

    mark_running(str(job["id"]))
    server.manager._finish_success(
        str(job["id"]), ProcessedJob(result={"count": 101})
    )
    summary = client.get("/api/billing/summary").json()
    assert (summary["balance"], summary["reserved"], summary["available"]) == (3, 0, 3)
    assert server.manager.get(str(job["id"]))["credits_spent"] == 2


def test_api_rejects_job_when_balance_is_exhausted() -> None:
    client, _user = registered_client()
    for _ in range(5):
        response = client.post(
            "/api/channels/import",
            json={
                "channel_url": "https://youtube.com/@example/shorts",
                "limit": 1,
            },
        )
        assert response.status_code == 202
    rejected = client.post(
        "/api/channels/import",
        json={
            "channel_url": "https://youtube.com/@example/shorts",
            "limit": 1,
        },
    )
    assert rejected.status_code == 402
    assert "Недостаточно кредитов" in rejected.json()["detail"]


def test_conditional_reservation_prevents_double_spend() -> None:
    user = User(
        email=f"concurrent-{uuid.uuid4().hex}@example.com",
        password_hash="test-only",
        credit_balance=1,
    )
    with server.SessionLocal() as db:
        db.add(user)
        db.commit()
        user_id = user.id

    def reserve_once(index: int) -> str:
        try:
            server.manager.create(
                "import", {"limit": 1}, user_id, job_id=f"concurrent-{index}-{uuid.uuid4().hex[:12]}"
            )
            return "reserved"
        except InsufficientCreditsError:
            return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve_once, range(2)))
    assert sorted(results) == ["insufficient", "reserved"]
    with server.SessionLocal() as db:
        snapshot = credit_snapshot(db, user_id)
        assert (snapshot.balance, snapshot.reserved, snapshot.available) == (1, 1, 0)


def test_credit_grant_is_idempotent() -> None:
    user = User(
        email=f"grant-{uuid.uuid4().hex}@example.com",
        password_hash="test-only",
    )
    with server.SessionLocal() as db, db.begin():
        db.add(user)
        db.flush()
        key = f"test-grant:{uuid.uuid4()}"
        assert grant_credits(
            db, user.id, 10,
            operation_type="test_grant",
            description="Test",
            idempotency_key=key,
        )
        assert not grant_credits(
            db, user.id, 10,
            operation_type="test_grant",
            description="Test duplicate",
            idempotency_key=key,
        )
    with server.SessionLocal() as db:
        assert credit_snapshot(db, user.id).balance == 10
        entries = db.scalars(
            select(CreditLedger).where(CreditLedger.user_id == user.id)
        ).all()
        assert len(entries) == 1
