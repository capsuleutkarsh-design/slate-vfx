"""
Slate Database Manager — unified proxy that selects SQLite or PostgreSQL backend.

Default: SQLite (standalone, zero-config).
Set "db_mode": "postgres" in client_config.json for network/studio use.
"""

import logging
from threading import RLock
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Proxy wrapper that delegates all database calls to the active backend.

    Backend selection:
      - "sqlite"   → SQLiteManager  (default, standalone)
      - "postgres" → PostgresManager (network/studio)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.requested_mode = self._detect_mode()
        self.allow_fallback = self._allow_fallback()
        self.active_mode = self.requested_mode
        self.fallback_used = False
        self.bootstrap_error: str = ""

        logger.info(
            "DatabaseManager initializing (requested_mode=%s, allow_fallback=%s)",
            self.requested_mode,
            self.allow_fallback,
        )

        self.backend, self.active_mode, self.fallback_used = self._bootstrap_backend(db_path=db_path)

        # Trigger Phase 1 Auto-Migrations (Postgres only)
        # Alembic is deliberately not run here any more, and the schema has one
        # owner instead of two.
        #
        # It could never work from an installed build: it shells out to an
        # alembic program and needs alembic.ini and a versions folder, none of
        # which are bundled or present on a workstation. And on a machine where
        # it could run, it failed on the first statement every time - the
        # revisions add columns that _ensure_db has already created, so it
        # aborted before stamping a version and could never make progress. No
        # PostgreSQL database in this studio carries an alembic_version row.
        #
        # Everything the application needs is now created by _ensure_db and by
        # this module, both of which run on both backends and inside an
        # installed build. Alembic remains for development; it is no longer in
        # the path of a studio starting up, where it only ever produced a
        # traceback in the log.

        # Every schema step, in order, from one list - see
        # migrations/registry.py. Each step used to be its own try/except
        # block here; the areas now add theirs to the registry with one line
        # and never edit this constructor. A failing step is logged and the
        # rest still run.
        try:
            from .migrations.registry import run_migrations
            self.migration_report = run_migrations(self.backend)
        except Exception as e:
            self.migration_report = {}
            logger.error(f"Schema migrations could not run: {e}")

        if self.fallback_used:
            logger.warning(
                "Database fallback active: requested=%s -> active=%s. "
                "Running in local fallback mode (not centrally synced).",
                self.requested_mode,
                self.active_mode,
            )
        else:
            logger.info("Database backend active: %s", self.active_mode)

    @staticmethod
    def _detect_mode() -> str:
        """Read db_mode from GlobalConfig; default to 'sqlite'."""
        try:
            from .global_config import GlobalConfig
            mode = GlobalConfig.get_db_mode()
            if mode == "postgres":
                return mode
        except Exception:
            pass
        return "sqlite"

    @staticmethod
    def _allow_fallback() -> bool:
        """Read allow_db_fallback from GlobalConfig; default True for resilience."""
        try:
            from .global_config import GlobalConfig
            return bool(GlobalConfig.allow_db_fallback())
        except Exception:
            pass
        return True

    def _bootstrap_backend(self, db_path: Optional[str] = None) -> Tuple[object, str, bool]:
        """Create the requested backend and optionally fallback to sqlite."""
        mode = self.requested_mode

        # An explicit path is an explicit instruction: open that SQLite file.
        # Every caller that passes one is a test or a tool wanting a database
        # of its own. This used to be ignored whenever the settings said
        # PostgreSQL, so on any machine that could reach the studio server the
        # "mock" database in the test suite was the studio's real one - tests
        # rewrote every account's sync stamp and left projects and leave rows
        # behind in it.
        if db_path:
            from .sqlite_manager import SQLiteManager
            self.requested_mode = "sqlite"
            return SQLiteManager(db_path=db_path), "sqlite", False

        if mode == "postgres":
            try:
                from .postgres_manager import PostgresManager

                backend = PostgresManager()
                # One attempt per address, so the decision (studio database,
                # local copy, or "unavailable") takes seconds. The retry ladder
                # made a refused port cost about 28 s here, every start-up.
                backend._init_pool(retry=False)
                return backend, "postgres", False
            except Exception as exc:
                self.bootstrap_error = str(exc)
                logger.error("Postgres bootstrap failed: %s", exc)
                if not self.allow_fallback:
                    # The error every screen already knows means "no
                    # database"; a RuntimeError went past the sign-in window's
                    # handler and ended Slate in the crash handler.
                    from .db_results import DatabaseUnavailableError
                    raise DatabaseUnavailableError(
                        "Postgres initialization failed and fallback is disabled. "
                        f"Reason: {exc}"
                    ) from exc
                logger.warning("Falling back to SQLite backend.")
                from .sqlite_manager import SQLiteManager

                return SQLiteManager(db_path=db_path), "sqlite", True

        from .sqlite_manager import SQLiteManager
        return SQLiteManager(db_path=db_path), "sqlite", False

    def get_runtime_status(self) -> dict:
        """Expose DB runtime mode for UI/status surfaces."""
        return {
            "requested_mode": self.requested_mode,
            "active_mode": self.active_mode,
            "fallback_used": self.fallback_used,
            "allow_fallback": self.allow_fallback,
            "bootstrap_error": self.bootstrap_error,
        }

    def is_local_mode(self) -> bool:
        """True when app is running in sqlite fallback from requested postgres."""
        return str(self.active_mode).lower() == "sqlite" and bool(self.fallback_used)

    def runtime_context_summary(self) -> str:
        """Compact human-readable DB runtime context for logs/UI errors."""
        if self.is_local_mode():
            return "LOCAL MODE (SQLite fallback; central sync limited)"
        return f"{str(self.active_mode).upper()} MODE"

    def reload_from_config(self, db_path: Optional[str] = None) -> None:
        """Rebuild backend from latest GlobalConfig values."""
        try:
            self.force_shutdown()
        except Exception as exc:
            logger.debug("DatabaseManager reload: backend shutdown skipped: %s", exc)

        self.requested_mode = self._detect_mode()
        self.allow_fallback = self._allow_fallback()
        self.active_mode = self.requested_mode
        self.fallback_used = False
        self.bootstrap_error = ""
        self.backend, self.active_mode, self.fallback_used = self._bootstrap_backend(db_path=db_path)

    def __getattr__(self, name):
        """Delegate all unknown method calls to the backend."""
        return getattr(self.backend, name)

    def force_shutdown(self):
        """Explicitly shut down the backend."""
        if hasattr(self.backend, 'shutdown_system'):
            self.backend.shutdown_system()
        elif hasattr(self.backend, 'force_shutdown'):
            self.backend.force_shutdown()


_manager_lock = RLock()
_manager_instance: Optional[DatabaseManager] = None


_manager_building = False
# Why the last build failed. Kept so every later caller gets the same answer at
# once: each one used to rebuild (and wait for the network) again, which made
# the sign-in window take 214 s to appear with the database down.
# ponytail: kept until reload_from_config() on the proxy, which the sign-in
# window's Try again calls; a tool that wants to retry calls it too.
_manager_failure: Optional[BaseException] = None


def _get_manager() -> DatabaseManager:
    global _manager_instance, _manager_building, _manager_failure
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                if _manager_failure is not None:
                    raise _manager_failure
                if _manager_building:
                    # Something reached for the global manager while it was
                    # still being built. Building a second one here is how a
                    # start-up turned into a hundred and sixty of them.
                    raise RuntimeError(
                        "The database manager was asked for while it was still "
                        "starting up. Pass the backend in rather than reaching "
                        "for the global one."
                    )
                _manager_building = True
                try:
                    _manager_instance = DatabaseManager()
                except Exception as exc:
                    _manager_failure = exc
                    raise
                finally:
                    _manager_building = False
    return _manager_instance


def is_connected() -> bool:
    """Whether the shared manager has been built (asking never connects)."""
    return _manager_instance is not None


class _DatabaseManagerProxy:
    """Lazy proxy to avoid eager DB bootstrap at import time."""

    def __getattr__(self, name):
        return getattr(_get_manager(), name)

    def reload_from_config(self, db_path: Optional[str] = None) -> None:
        """
        Use the settings as they are now. Built: rebuild. Not built yet (or the
        last build failed): forget the failure; the next use builds - so this
        never blocks on the network when nothing was connected.
        """
        global _manager_failure
        with _manager_lock:
            _manager_failure = None
            manager = _manager_instance
        if manager is not None:
            manager.reload_from_config(db_path)

    def __repr__(self):
        mgr = _manager_instance
        if mgr is None:
            return "<DatabaseManagerProxy(uninitialized)>"
        return f"<DatabaseManagerProxy(active_mode={mgr.active_mode}, fallback_used={mgr.fallback_used})>"


database_manager = _DatabaseManagerProxy()