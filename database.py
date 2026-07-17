import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = (BASE_DIR / "server_data" / "yt_loader.db").resolve()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}")
EXPECTED_DATABASE_REVISION = "m8b9c0d1e2f3"


def build_engine(database_url: str = DATABASE_URL) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    connection_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if database_url.startswith("sqlite"):
        @event.listens_for(connection_engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return connection_engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def check_database(connection_engine: Engine = engine) -> bool:
    try:
        with connection_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            migration_version = connection.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
        return migration_version == EXPECTED_DATABASE_REVISION
    except Exception:
        return False
