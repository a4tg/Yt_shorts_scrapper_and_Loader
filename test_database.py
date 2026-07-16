import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import server
from database import build_engine, check_database
from saas_models import CreditLedger, User


EXPECTED_TABLES = {
    "users",
    "user_sessions",
    "plans",
    "subscriptions",
    "payments",
    "jobs",
    "job_files",
    "overlays",
    "credit_ledger",
    "webhook_events",
    "account_tokens",
    "workspaces",
    "workspace_members",
    "projects",
    "approval_workflows",
        "approval_stages",
        "content_items",
        "content_revisions",
        "content_attachments",
}


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        self.config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
        self.config.attributes["database_url"] = self.database_url
        self.engines = []

    def tearDown(self) -> None:
        for engine in self.engines:
            engine.dispose()
        self.temp_dir.cleanup()

    def make_engine(self):
        engine = build_engine(self.database_url)
        self.engines.append(engine)
        return engine

    def test_initial_migration_upgrades_and_downgrades_cleanly(self) -> None:
        command.upgrade(self.config, "head")
        engine = self.make_engine()
        self.assertTrue(EXPECTED_TABLES.issubset(set(inspect(engine).get_table_names())))
        self.assertIn("is_admin", {column["name"] for column in inspect(engine).get_columns("users")})
        job_columns = {column["name"]: column for column in inspect(engine).get_columns("jobs")}
        self.assertIn("lease_expires_at", job_columns)
        self.assertIn("worker_id", job_columns)
        self.assertIn("attempts", job_columns)
        self.assertTrue(job_columns["user_id"]["nullable"])
        user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
        self.assertTrue({"credit_balance", "reserved_credits"}.issubset(user_columns))
        ledger_columns = {column["name"] for column in inspect(engine).get_columns("credit_ledger")}
        self.assertIn("idempotency_key", ledger_columns)
        payment_columns = {column["name"] for column in inspect(engine).get_columns("payments")}
        self.assertTrue(
            {"plan_id", "subscription_id", "credits", "billing_period_key"}.issubset(
                payment_columns
            )
        )
        self.assertTrue(check_database(engine))

        command.downgrade(self.config, "base")
        self.assertTrue(EXPECTED_TABLES.isdisjoint(set(inspect(engine).get_table_names())))
        self.assertFalse(check_database(engine))

    def test_existing_user_receives_starter_credits_during_upgrade(self) -> None:
        command.upgrade(self.config, "3c21790ec1ae")
        engine = self.make_engine()
        user_id = "existing-user-before-billing"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, status, is_admin) "
                    "VALUES (:id, :email, 'active', 0)"
                ),
                {"id": user_id, "email": "existing-before-billing@example.com"},
            )
        command.upgrade(self.config, "head")
        with engine.connect() as connection:
            balance = connection.scalar(
                text("SELECT credit_balance FROM users WHERE id = :id"), {"id": user_id}
            )
            grant = connection.scalar(
                text(
                    "SELECT amount FROM credit_ledger "
                    "WHERE user_id = :id AND operation_type = 'signup_grant'"
                ),
                {"id": user_id},
            )
            verified_at = connection.scalar(
                text("SELECT email_verified_at FROM users WHERE id = :id"), {"id": user_id}
            )
        self.assertEqual(balance, 5)
        self.assertEqual(grant, 5)
        self.assertIsNotNone(verified_at)
        with engine.connect() as connection:
            workspace_role = connection.scalar(
                text(
                    "SELECT wm.role FROM workspace_members wm "
                    "JOIN workspaces w ON w.id = wm.workspace_id "
                    "WHERE wm.user_id = :id"
                ),
                {"id": user_id},
            )
            project_count = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM projects p JOIN workspaces w ON w.id = p.workspace_id "
                    "WHERE w.owner_user_id = :id"
                ),
                {"id": user_id},
            )
        self.assertEqual(workspace_role, "owner")
        self.assertEqual(project_count, 1)

    def test_models_enforce_unique_users_and_keep_credit_history(self) -> None:
        command.upgrade(self.config, "head")
        engine = self.make_engine()
        with Session(engine) as session:
            user = User(email="creator@example.com", password_hash="future-argon2-hash")
            session.add(user)
            session.flush()
            session.add_all(
                [
                    CreditLedger(user_id=user.id, amount=10, operation_type="manual_grant"),
                    CreditLedger(user_id=user.id, amount=-3, operation_type="job_charge"),
                ]
            )
            session.commit()
            self.assertEqual(
                sum(
                    session.scalars(
                        select(CreditLedger.amount).where(CreditLedger.user_id == user.id)
                    ).all()
                ),
                7,
            )

            session.add(User(email="creator@example.com"))
            with self.assertRaises(IntegrityError):
                session.commit()


class DatabaseHealthTests(unittest.TestCase):
    def test_health_reports_database_state(self) -> None:
        client = TestClient(server.app)
        with patch("server.check_database", return_value=True):
            healthy = client.get("/api/health")
            self.assertEqual(healthy.status_code, 200)
            self.assertEqual(
                healthy.json(), {"status": "ok", "database": "ok", "workers": "ok"}
            )
        with patch("server.check_database", return_value=False):
            degraded = client.get("/api/health")
            self.assertEqual(degraded.status_code, 503)
            self.assertEqual(
                degraded.json(),
                {"status": "degraded", "database": "error", "workers": "ok"},
            )
        with patch("server.check_database", return_value=True), patch.object(
            server.manager, "healthy", return_value=False
        ):
            degraded = client.get("/api/health")
            self.assertEqual(degraded.status_code, 503)
            self.assertEqual(degraded.json()["workers"], "error")


if __name__ == "__main__":
    unittest.main()
