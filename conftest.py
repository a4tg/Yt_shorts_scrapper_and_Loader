import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

import database
import server
from database import build_engine, get_db
from saas_models import (
    AccountToken,
    CreditLedger,
    Job,
    JobFile,
    Overlay,
    Payment,
    Subscription,
    User,
    UserSession,
    WebhookEvent,
)


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    """Keep authentication tests independent from the developer's local database."""
    temp_dir = tempfile.TemporaryDirectory()
    database_path = Path(temp_dir.name) / "test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    engine = build_engine(database_url)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    server.manager.stop()
    server.manager._legacy_imported = True
    original_database_sessions = database.SessionLocal
    original_server_sessions = server.SessionLocal
    database.SessionLocal = test_sessions
    server.SessionLocal = test_sessions

    def test_db_dependency():
        with test_sessions() as session:
            yield session

    server.app.dependency_overrides[get_db] = test_db_dependency
    try:
        yield
    finally:
        server.app.dependency_overrides.pop(get_db, None)
        database.SessionLocal = original_database_sessions
        server.SessionLocal = original_server_sessions
        engine.dispose()
        temp_dir.cleanup()


@pytest.fixture(autouse=True)
def clear_saas_state_between_tests(isolated_test_database):
    yield
    with server.SessionLocal() as db:
        # Delete dependants first so every test starts with an empty tenant and
        # billing state even when SQLite foreign-key enforcement is enabled.
        db.execute(delete(AccountToken))
        db.execute(delete(CreditLedger))
        db.execute(delete(WebhookEvent))
        db.execute(delete(JobFile))
        db.execute(delete(Overlay))
        db.execute(delete(Job))
        db.execute(delete(Payment))
        db.execute(delete(Subscription))
        db.execute(delete(UserSession))
        db.execute(delete(User))
        db.commit()
