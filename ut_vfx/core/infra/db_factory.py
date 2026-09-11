"""
Unified SQLAlchemy 2.0 Database Factory & Session Management for UTCAP VFX.
Supports dynamic switching between PostgreSQL (Studio/Network mode) and SQLite WAL (Local fallback).
Provides thread-safe engine caching and transactional session context management.
"""

from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Generator, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ut_vfx.core.infra.models.base import Base
# Import all models to ensure metadata is populated
import ut_vfx.core.infra.models  # noqa: F401

logger = logging.getLogger(__name__)

_engine_lock = threading.RLock()
_engine_instance: Optional[Engine] = None
_sessionmaker_instance: Optional[sessionmaker] = None


def _get_sqlite_path() -> str:
    """Resolve the SQLite database path identically to SQLiteManager."""
    try:
        from ut_vfx.core.infra.sqlite_manager import SQLiteManager
        return SQLiteManager._resolve_db_path()
    except Exception:
        import os
        local_app = os.getenv("LOCALAPPDATA")
        db_dir = Path(local_app) / "UTVFX" if local_app else Path.home() / ".utvfx"
        db_dir.mkdir(parents=True, exist_ok=True)
        return str(db_dir / "ut_vfx.db")


def get_database_url() -> str:
    """
    Construct the database connection URL based on active configuration.
    Falls back to SQLite if PostgreSQL is unavailable or unconfigured.
    """
    try:
        from ut_vfx.core.infra.database_manager import database_manager
        active_mode = getattr(database_manager, "active_mode", "sqlite")
        fallback_used = getattr(database_manager, "fallback_used", False)
    except Exception:
        active_mode = "sqlite"
        fallback_used = False

    if active_mode == "postgres" and not fallback_used:
        try:
            from ut_vfx.core.infra.global_config import GlobalConfig
            config = GlobalConfig.get("db_config", {}) or {}
            host = config.get("host") or GlobalConfig.get("db_host") or "127.0.0.1"
            port = int(config.get("port") or GlobalConfig.get("db_port") or 5440)
            dbname = config.get("name") or GlobalConfig.get("db_name") or "ut_vfx"
            user = config.get("user") or GlobalConfig.get("db_user") or "postgres"
            
            password = config.get("password")
            if not password:
                try:
                    import keyring
                    password = keyring.get_password("UTVFX", "db_password")
                except Exception:
                    password = None
            if not password:
                password = "postgres"

            escaped_user = quote_plus(str(user))
            escaped_pwd = quote_plus(str(password))
            return f"postgresql+psycopg2://{escaped_user}:{escaped_pwd}@{host}:{port}/{dbname}"
        except Exception as e:
            logger.warning("Failed to construct PostgreSQL URL: %s. Falling back to SQLite.", e)

    sqlite_path = _get_sqlite_path()
    # Normalize Windows paths for sqlite URL (forward slashes)
    sqlite_uri = Path(sqlite_path).as_posix()
    return f"sqlite:///{sqlite_uri}"


def get_engine(force_recreate: bool = False) -> Engine:
    """
    Return the singleton SQLAlchemy Engine, creating or recycling it if needed.
    """
    global _engine_instance, _sessionmaker_instance
    with _engine_lock:
        if _engine_instance is not None and not force_recreate:
            return _engine_instance

        if _engine_instance is not None and force_recreate:
            try:
                _engine_instance.dispose()
            except Exception as e:
                logger.debug("Engine disposal notice: %s", e)
            _engine_instance = None
            _sessionmaker_instance = None

        url = get_database_url()
        is_sqlite = url.startswith("sqlite")

        if is_sqlite:
            engine = create_engine(
                url,
                connect_args={"check_same_thread": False, "timeout": 30},
                pool_pre_ping=True,
            )

            # Enforce WAL mode, normal synchronous, and busy timeout for SQLite concurrency
            @event.listens_for(engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                if isinstance(dbapi_connection, sqlite3.Connection):
                    cursor = dbapi_connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=10000")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()
        else:
            engine = create_engine(
                url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
            )

        _engine_instance = engine
        _sessionmaker_instance = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_engine_instance,
        )

        logger.info("SQLAlchemy Engine initialized with dialect: %s", engine.dialect.name)
        return _engine_instance


def get_session_factory() -> sessionmaker:
    """Return the configured sessionmaker."""
    global _sessionmaker_instance
    if _sessionmaker_instance is None:
        get_engine()
    return _sessionmaker_instance


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager providing a transactional SQLAlchemy Session.
    Automatically commits on normal exit, rolls back on exceptions, and closes.
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Database session rollback due to exception: %s", exc)
        raise
    finally:
        session.close()


def init_schema(engine: Optional[Engine] = None) -> None:
    """
    Idempotently initialize all tables defined across SQLAlchemy models.
    """
    target_engine = engine or get_engine()
    Base.metadata.create_all(bind=target_engine)
    logger.info("SQLAlchemy schema verification completed.")


def reset_engine() -> None:
    """Reset the engine and session factory on config reload or backend switch."""
    get_engine(force_recreate=True)
