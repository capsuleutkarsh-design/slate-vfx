import pytest
import tempfile
from pathlib import Path
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from ut_vfx.core.infra.database_manager import DatabaseManager
from ut_vfx.core.infra.config_manager import ConfigManager

@pytest.fixture
def temp_vfx_root(tmp_path):
    """Creates a temporary VFX Project Root structure."""
    root = tmp_path / "vfx_root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "01_Admin").mkdir(exist_ok=True)
    (root / "05_Reels").mkdir(exist_ok=True)
    (root / "Incoming").mkdir(exist_ok=True)
    return root

@pytest.fixture
def mock_db(temp_vfx_root):
    """Creates a localized database manager that doesn't touch the real system DB."""
    from ut_vfx.core.infra.sqlite_manager import SQLiteManager
    import ut_vfx.core.infra.database_manager as db_module
    
    db_path = str(temp_vfx_root / "test_ut_vfx.db")
    SQLiteManager._instance = None
    mgr = db_module.DatabaseManager(db_path=db_path)
    
    with db_module._manager_lock:
        db_module._manager_instance = mgr
        
    yield mgr
    
    if hasattr(mgr, 'close_connection'):
        mgr.close_connection()
    with db_module._manager_lock:
        db_module._manager_instance = None
    SQLiteManager._instance = None

@pytest.fixture
def mock_config(temp_vfx_root):
    """Creates a config manager pointing to temp paths."""
    cm = ConfigManager()
    # Override global paths if necessary
    # cm.global_settings['project_root'] = str(temp_vfx_root)
    yield cm

import os

@pytest.fixture(autouse=True)
def mock_gui_dialogs(monkeypatch):
    """
    Mock blocking GUI components for headless testing.
    This prevents tests from hanging when QMessageBox or QFileDialog is called.
    """
    os.environ["HEADLESS_TESTING"] = "1"
    
    try:
        from PySide6.QtWidgets import QMessageBox, QFileDialog, QProgressDialog, QDialog
        
        # Mock QMessageBox methods
        monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        
        # Mock QFileDialog methods
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("/mock/path/file.txt", ""))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("/mock/path/file.txt", ""))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: "/mock/path/dir")
        
        # Mock QDialog exec (note: QDialog.DialogCode.Accepted is used, but for simplicity we return 1 which is the int value of Accepted)
        monkeypatch.setattr(QDialog, "exec", lambda *args, **kwargs: 1)
        
    except ImportError:
        pass


def pytest_configure(config):
    """
    Stop the suite writing to the developer's own settings file.

    GlobalConfig.set() saves to %LOCALAPPDATA%, and several tests set values -
    some at import time, before any fixture can run - so a test run left the
    real client pointing at a scratch database, or switched it to SQLite. This
    hook runs before collection, which is early enough to catch those too.

    Values still change in memory, so the tests behave exactly as before. Only
    the write to disk is stopped.
    """
    from ut_vfx.core.infra.global_config import GlobalConfig

    if not getattr(GlobalConfig, "_save_disabled_for_tests", False):
        GlobalConfig._real_save = GlobalConfig.save
        GlobalConfig.save = lambda self: None
        GlobalConfig._save_disabled_for_tests = True


def pytest_unconfigure(config):
    from ut_vfx.core.infra.global_config import GlobalConfig

    if getattr(GlobalConfig, "_save_disabled_for_tests", False):
        GlobalConfig.save = GlobalConfig._real_save
        GlobalConfig._save_disabled_for_tests = False


# ---------------------------------------------------------------------------
# Running against a real PostgreSQL
#
# Everything above runs on SQLite. That is fast and it is not what the studio
# runs. Three bugs reached the live server behind a green suite because of it:
# the server's own monitoring lost its password, the client's constructor was
# cut in half, and every dashboard save failed with a TypeError because the
# PostgreSQL wrapper dropped an argument SQLite accepted.
#
# These fixtures put a real PostgreSQL under the same code. They skip
# themselves when no server is reachable, so the suite still runs anywhere.
# ---------------------------------------------------------------------------

POSTGRES_TEST_DB = "ut_vfx_pytest"

_MISSING = object()


def _pg_settings():
    """Where to find a PostgreSQL to test against."""
    import json
    settings = {"host": "127.0.0.1", "port": 5440,
                "user": "postgres", "password": ""}

    root = Path(__file__).resolve().parents[1]
    for candidate in (root / "client_config.json",
                      root / "ut_vfx" / "default_config.json"):
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                settings["port"] = int(data.get("db_port") or settings["port"])
                settings["password"] = data.get("db_password") or settings["password"]
                break
        except Exception:
            continue

    # An explicit environment variable wins, for CI.
    settings["host"] = os.environ.get("UTVFX_TEST_PGHOST", settings["host"])
    settings["port"] = int(os.environ.get("UTVFX_TEST_PGPORT", settings["port"]))
    settings["password"] = os.environ.get("UTVFX_TEST_PGPASSWORD", settings["password"])
    return settings


def _pg_connect(dbname):
    import psycopg2
    s = _pg_settings()
    return psycopg2.connect(host=s["host"], port=s["port"], user=s["user"],
                            password=s["password"], dbname=dbname,
                            connect_timeout=3,
                            application_name="UT_VFX pytest")


@pytest.fixture(scope="session")
def postgres_available():
    """True when a PostgreSQL we may create a scratch database on is reachable."""
    try:
        conn = _pg_connect("postgres")
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres_scratch_db(postgres_available):
    """
    A throwaway database, created for the session and dropped afterwards.

    Never the studio's own database: these tests write and delete freely.
    """
    if not postgres_available:
        pytest.skip("no PostgreSQL reachable - start the server to run these")

    import psycopg2

    admin = _pg_connect("postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{POSTGRES_TEST_DB}"')
            cur.execute(f'CREATE DATABASE "{POSTGRES_TEST_DB}"')
    finally:
        admin.close()

    yield POSTGRES_TEST_DB

    admin = _pg_connect("postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            # Anything still holding it open would block the drop.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (POSTGRES_TEST_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{POSTGRES_TEST_DB}"')
    except Exception:
        pass
    finally:
        admin.close()


from contextlib import contextmanager


@contextmanager
def _postgres_settings_applied(dbname):
    """
    Point the software at the throwaway database, and put it back afterwards.

    Two rules here, both learned the hard way. Settings are changed in memory
    only, and writing them out is blocked while they are in place:
    GlobalConfig.save() dumps the whole dictionary, so one unrelated
    GlobalConfig.set() anywhere would persist this test database into the
    developer's real configuration - which broke their client. And the change
    is undone the moment a PostgreSQL test finishes, so the SQLite tests that
    run afterwards do not believe they are in offline fallback.
    """
    from ut_vfx.core.infra.global_config import GlobalConfig

    settings = _pg_settings()
    overrides = {
        "db_mode": "postgres",
        "db_host": settings["host"],
        "db_port": settings["port"],
        "db_name": dbname,
        "db_user": settings["user"],
        "db_password": settings["password"],
        # Straight to the database: the pooler is not what is under test here.
        "db_pooler_port": 0,
        # Only this machine, so an absent second host cannot add minutes.
        "db_host_candidates": [settings["host"]],
    }

    if GlobalConfig._instance is None:
        GlobalConfig._instance = GlobalConfig()
    live = GlobalConfig._instance.data
    previous = {k: live.get(k, _MISSING) for k in overrides}
    real_save = GlobalConfig.save

    live.update(overrides)
    GlobalConfig.save = lambda self: None
    try:
        yield
    finally:
        GlobalConfig.save = real_save
        for key, value in previous.items():
            if value is _MISSING:
                live.pop(key, None)
            else:
                live[key] = value


@pytest.fixture(scope="session")
def _pg_manager(postgres_scratch_db):
    """
    One DatabaseManager for the whole session, with the schema built once.

    Building it per test cost about eighty seconds each - the migrations run on
    every construction - which made these tests unusable and so useless.
    """
    import ut_vfx.core.infra.database_manager as db_module
    from ut_vfx.core.infra.postgres_manager import PostgresManager

    with _postgres_settings_applied(postgres_scratch_db):
        PostgresManager._instance = None
        manager = db_module.DatabaseManager()
        ready = manager.get_runtime_status().get("active_mode") == "postgres"

    if not ready:
        PostgresManager._instance = None
        pytest.skip("could not reach PostgreSQL for these tests")

    yield manager

    PostgresManager._instance = None


@pytest.fixture
def pg_db(postgres_scratch_db, _pg_manager):
    """
    A DatabaseManager talking to a real PostgreSQL, emptied before each test.

    Use this anywhere the behaviour could differ between the two backends -
    which is anywhere the database layer is involved at all.
    """
    import ut_vfx.core.infra.database_manager as db_module

    with _postgres_settings_applied(postgres_scratch_db):
        try:
            rows = _pg_manager.execute_query(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'") or []
            names = [r["tablename"] for r in rows if r.get("tablename")]
            if names:
                _pg_manager.execute_update(
                    "TRUNCATE TABLE " + ", ".join(f'"{n}"' for n in names)
                    + " RESTART IDENTITY CASCADE")
        except Exception:
            pass

        with db_module._manager_lock:
            db_module._manager_instance = _pg_manager

        yield _pg_manager

        with db_module._manager_lock:
            db_module._manager_instance = None
