"""
SQLite Database Manager for Slate — standalone/local backend.

Provides the same public API as PostgresManager so that DatabaseManager
can switch between backends transparently.

Features:
 - Auto-creates DB file + schema on first run
 - WAL journal mode for better concurrency
 - Thread-safe via threading.Lock
 - Dict-like row access via sqlite3.Row
 - No external dependencies (stdlib only)
"""

import sqlite3
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .db_results import (
    DatabaseUnavailableError, SqlResult, WriteResult, classify_error, error_text,
    is_legacy_write_fetch, is_write_statement,
)
from .transaction import AtomicUnit

logger = logging.getLogger(__name__)

# "INSERT ... RETURNING id": SQLite builds older than 3.35 do not know
# RETURNING, so it is taken off and the id comes from lastrowid instead.
_RETURNING_RE = re.compile(r'\s+RETURNING\s+\w+(?:\s*,\s*\w+)*', re.IGNORECASE)


def _sqlite_unavailable(exc: BaseException) -> bool:
    """
    Whether an sqlite3 error means the database itself is out of reach -
    locked by another process past the timeout, missing, or unreadable - as
    opposed to this statement being wrong. sqlite3 raises OperationalError
    for both ("no such column" too), so it is told apart by its message.
    """
    if isinstance(exc, DatabaseUnavailableError):
        return True
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in (
        "database is locked", "unable to open", "disk i/o", "database is busy",
        "readonly database", "database or disk is full", "malformed"))


def _reel_of(data_json) -> str:
    """
    A shot's reel, read out of its stored JSON.

    The reel is part of a shot's identity, and it already travels inside the
    shot payload - so the database layer lifts it out rather than every caller
    having to pass it separately.
    """
    try:
        data = json.loads(data_json) if isinstance(data_json, str) else (data_json or {})
        return str(data.get("reel_episode") or "").strip()
    except Exception:
        return ""




# ── Schema ──────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    template_used TEXT DEFAULT '',
    target_directory TEXT DEFAULT '',
    total_folders INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    operation_type TEXT DEFAULT '',
    start_time TEXT,
    end_time TEXT,
    duration REAL DEFAULT 0,
    items_processed INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER,
    item_name TEXT DEFAULT '',
    source_path TEXT DEFAULT '',
    dest_path TEXT DEFAULT '',
    file_size BIGINT DEFAULT 0,
    duration REAL DEFAULT 0,
    status TEXT DEFAULT '',
    error_msg TEXT DEFAULT '',
    timestamp TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE,
    file_name TEXT DEFAULT '',
    file_size BIGINT DEFAULT 0,
    file_type TEXT DEFAULT '',
    thumb_path TEXT DEFAULT '',
    proxy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    embedding TEXT,
    ingest_date TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tracking_projects (
    code TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}',
    last_updated TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tracking_shots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL,
    reel TEXT DEFAULT '',
    shot_name TEXT NOT NULL,
    status TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    data_json TEXT DEFAULT '{}',
    last_updated TEXT,
    version INTEGER DEFAULT 0,
    -- The reel is part of a shot's identity: SH010 can exist in ReelA and
    -- ReelB, and they are different shots.
    UNIQUE(project_code, reel, shot_name)
);

CREATE TABLE IF NOT EXISTS tracking_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shot_id INTEGER,
    project_code TEXT,
    department TEXT DEFAULT '',
    status TEXT DEFAULT '',
    artist_name TEXT DEFAULT '',
    artist_id INTEGER,
    bid_days REAL DEFAULT 0,
    target_date TEXT DEFAULT '',
    UNIQUE(project_code, shot_id, department)
);

CREATE TABLE IF NOT EXISTS tracking_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL,
    reel TEXT DEFAULT '',
    shot_name TEXT NOT NULL,
    version_name TEXT NOT NULL,
    department TEXT DEFAULT '',
    artist TEXT DEFAULT '',
    status TEXT DEFAULT '',
    sent_to TEXT DEFAULT '',
    sent_date TEXT DEFAULT '',
    media_path TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_code, reel, shot_name, version_name)
);

CREATE TABLE IF NOT EXISTS tracking_version_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    source TEXT DEFAULT '',
    author TEXT DEFAULT '',
    note_date TEXT DEFAULT '',
    text TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ut_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    job_title TEXT DEFAULT '',
    roles TEXT DEFAULT '[]',
    profile_pic_path TEXT DEFAULT '',
    last_synced TEXT
);

CREATE TABLE IF NOT EXISTS ut_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name TEXT UNIQUE NOT NULL,
    permissions TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS change_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT DEFAULT '',
    entity_type TEXT DEFAULT '',
    entity_id TEXT DEFAULT '',
    user_id INTEGER,
    action_type TEXT DEFAULT '',
    field_changed TEXT DEFAULT '',
    old_value TEXT DEFAULT '',
    new_value TEXT DEFAULT '',
    timestamp TEXT DEFAULT (datetime('now')),
    shot_id INTEGER,
    shot_name TEXT,
    reel TEXT,
    department TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    user_id INTEGER,
    action TEXT DEFAULT '',
    target TEXT DEFAULT '',
    details TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS attendance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    day_date TEXT NOT NULL,
    punch_in TEXT,
    punch_out TEXT,
    pc_name TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    UNIQUE(user_id, day_date)
);

CREATE TABLE IF NOT EXISTS slate_json_write_locks (
    lock_name TEXT PRIMARY KEY,
    holder TEXT DEFAULT '',
    acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS it_deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_name TEXT NOT NULL,
    target_machine TEXT NOT NULL,
    deployed_by TEXT,
    status TEXT DEFAULT 'Pending',
    deployed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hardware_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_name TEXT UNIQUE NOT NULL,
    assigned_to TEXT,
    gpu TEXT,
    ram TEXT,
    cpu TEXT,
    storage TEXT,
    type TEXT,
    status TEXT DEFAULT 'Active',
    location TEXT DEFAULT '',
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS it_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_by TEXT,
    category TEXT,
    description TEXT,
    status TEXT DEFAULT 'Open',
    priority TEXT DEFAULT 'Medium',
    assigned_to TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS ticket_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER,
    user_id TEXT,
    comment TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS it_licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    software_name TEXT NOT NULL,
    license_key TEXT,
    seats_total INTEGER DEFAULT 0,
    seats_used INTEGER DEFAULT 0,
    expiry_date TEXT
);

CREATE TABLE IF NOT EXISTS leave_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    half_day BOOLEAN DEFAULT 0,
    reason TEXT,
    status TEXT DEFAULT 'Pending',
    approved_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leave_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE NOT NULL,
    cl_balance REAL DEFAULT 10.0,
    sl_balance REAL DEFAULT 5.0,
    el_balance REAL DEFAULT 5.0,
    lwp_balance REAL DEFAULT 0.0,
    last_updated TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS onboarding_workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    department TEXT,
    is_completed BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prod_scheduling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT,
    milestone TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT,
    depends_on_id INTEGER
);

CREATE TABLE IF NOT EXISTS prod_bidding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT,
    shot_count INTEGER DEFAULT 0,
    cost_per_shot REAL DEFAULT 0,
    estimated_budget REAL,
    status TEXT DEFAULT 'Draft'
);

CREATE INDEX IF NOT EXISTS idx_change_history_project ON change_history(project_code);
CREATE INDEX IF NOT EXISTS idx_tracking_tasks_project ON tracking_tasks(project_code);
CREATE INDEX IF NOT EXISTS idx_tracking_shots_project ON tracking_shots(project_code);
CREATE INDEX IF NOT EXISTS idx_attendance_user ON attendance_log(user_id);
"""


def _dict_factory(cursor, row):
    """Row factory that returns dict-like objects (matches RealDictCursor behavior)."""
    fields = [column[0] for column in cursor.description]
    return dict(zip(fields, row))


class SQLiteManager:
    """
    SQLite backend for Slate — drop-in replacement for PostgresManager.

    DB file location (auto-detected):
      1. GlobalConfig 'db_path' setting
      2. %LOCALAPPDATA%/Slate/slate.db
      3. ./slate.db (fallback)
    """

    _instance = None
    _lock = threading.Lock()
    _is_shutting_down = False

    @classmethod
    def shutdown_system(cls):
        """Shut down the SQLite connection pool."""
        cls._is_shutting_down = True
        try:
            if cls._instance:
                cls._instance._initialized = False
        finally:
            cls._is_shutting_down = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        if hasattr(self, '_initialized') and self._initialized:
            return

        self._db_path = self._resolve_db_path(db_path)
        self._local = threading.local()
        self._embedding_cache = None
        self._vector_cache_lock = threading.RLock()

        # Lazy-import repos to match PostgresManager interface
        from .project_repository import ProjectRepository
        from .stock_repository import StockRepository
        self.project_repo = ProjectRepository(self)
        self.stock_repo = StockRepository(self)

        self._ensure_db()
        self._initialized = True
        logger.info(f"SQLiteManager initialized (db: {self._db_path})")

    # ── Path Resolution ─────────────────────────────────────────────────────

    @staticmethod
    def _resolve_db_path(explicit: Optional[str] = None) -> str:
        if explicit:
            return str(Path(explicit).resolve())

        local_app = os.getenv("LOCALAPPDATA", "")

        try:
            from .global_config import GlobalConfig
            cfg_path = GlobalConfig.get("db_path", "")
            if cfg_path and str(cfg_path).strip():
                return str(Path(cfg_path).resolve())

            # Local-first default for sqlite stability.
            if local_app:
                db_dir = Path(local_app) / "Slate"
            else:
                db_dir = Path.home() / ".slate"
            db_dir.mkdir(parents=True, exist_ok=True)
            return str((db_dir / "slate.db").resolve())
        except Exception as e:
            logger.warning(f"Could not resolve sqlite db path from config: {e}. Falling back to Local AppData.")

        if local_app:
            db_dir = Path(local_app) / "Slate"
        else:
            db_dir = Path.home() / ".slate"
        db_dir.mkdir(parents=True, exist_ok=True)
        return str(db_dir / "slate.db")

    # ── Connection ──────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return the thread-local connection, creating it if needed."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.total_changes
            except sqlite3.ProgrammingError:
                conn = None
                self._local.conn = None

        if conn is None:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30,
            )
            conn.row_factory = _dict_factory
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def close_connection(self):
        """Close the thread-local connection cleanly."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _ensure_db(self):
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(prod_scheduling)")
            cols = [row['name'] if isinstance(row, dict) else row[1] for row in cur.fetchall()]
            if "depends_on_id" not in cols:
                conn.execute("ALTER TABLE prod_scheduling ADD COLUMN depends_on_id INTEGER")
                conn.commit()
        except Exception:
            pass
        conn.commit()

    def _initialize_database(self):
        """Backward-compatibility alias for _ensure_db."""
        self._ensure_db()

    @contextmanager
    def get_connection(self):
        """Context manager matching PostgresManager.get_connection()."""
        if self._is_shutting_down:
            raise ConnectionError("SQLiteManager is shutting down.")
        conn = self._get_conn()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def transaction(self):
        """Explicit transaction block — commits on success, rolls back on error."""
        if self._is_shutting_down:
            raise ConnectionError("SQLiteManager is shutting down.")
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def atomic(self):
        """
        Several statements that happen together or not at all - the same
        block, the same %s parameters and the same errors as
        PostgresManager.atomic(). See transaction.py.
        """
        if self._is_shutting_down:
            raise DatabaseUnavailableError("The local database is closing, so nothing was saved.")
        conn = self._get_conn()
        # Anything a previous statement left open is committed first, so the
        # unit starts clean and its rollback cannot take someone else's work.
        if conn.in_transaction:
            conn.commit()
        unit = AtomicUnit(conn, translate=self._translate_sql,
                          is_unavailable=_sqlite_unavailable, strip_returning=True)
        try:
            yield unit
            conn.commit()
        except BaseException as e:
            try:
                conn.rollback()
            except Exception as exc:
                logger.debug("Rollback after a failed atomic block skipped: %s", exc)
            if _sqlite_unavailable(e):
                raise DatabaseUnavailableError(
                    "The local database could not be written, so nothing was saved.") from e
            raise

    # ── Query Execution ─────────────────────────────────────────────────────
    #
    # The same contract as PostgresManager (see db_results.py): reads return
    # rows, or None when the statement was refused; writes return a
    # WriteResult; a database that cannot be opened or stays locked raises
    # DatabaseUnavailableError. This backend used to raise RuntimeError for
    # every refused statement while PostgreSQL returned None, so code tested
    # on one behaved differently on the other.

    _last_error_local = threading.local()

    def last_error(self) -> str:
        """Why the most recent statement on this thread was refused, or ''."""
        return getattr(self._last_error_local, "text", "") or ""

    def _remember_error(self, text: str = "") -> None:
        self._last_error_local.text = text or ""

    def _prepare(self, query: str):
        q = self._translate_sql(query)
        has_returning = bool(_RETURNING_RE.search(query))
        if has_returning:
            # Remove the RETURNING clause; the new id comes from lastrowid.
            q = _RETURNING_RE.sub('', q)
        return q, has_returning

    def execute_query(self, query: str, params: tuple = None, fetch: str = "all") -> Any:
        """
        Execute a query, translating PostgreSQL syntax on-the-fly.

        Supports: %s → ?, ILIKE → LIKE, RETURNING id (via lastrowid).
        fetch=False / None / "none" is a write and returns a WriteResult.
        """
        if is_legacy_write_fetch(fetch):
            return self.write(query, params)
        try:
            q, has_returning = self._prepare(query)
            conn = self._get_conn()
            cur = conn.execute(q, params or ())

            if is_write_statement(q):
                conn.commit()

            self._remember_error("")
            if has_returning and fetch == "lastrowid":
                return cur.lastrowid

            if fetch == "all":
                return cur.fetchall() if cur.description is not None else []
            elif fetch == "one":
                return cur.fetchone() if cur.description is not None else None
            elif fetch == "rowcount":
                return cur.rowcount
            elif fetch == "lastrowid":
                return cur.lastrowid
            raise ValueError(f"Unknown fetch mode {fetch!r}")
        except Exception as e:
            self._rollback_quietly()
            if _sqlite_unavailable(e):
                logger.error(f"SQLite database unavailable: {e} | Query: {query[:120]}")
                raise DatabaseUnavailableError(
                    "The local database could not be read or written. "
                    "Your work has not been lost - try again in a moment.") from e
            self._remember_error(error_text(e))
            logger.error(f"SQLite query error: {e} | Query: {query[:120]}")
            return None

    def write(self, query: str, params: tuple = None, *, strict: bool = False) -> WriteResult:
        """One write, reported as a WriteResult - see PostgresManager.write()."""
        try:
            q, has_returning = self._prepare(query)
            conn = self._get_conn()
            cur = conn.execute(q, params or ())
            rows = cur.rowcount
            last_id = None
            if cur.description is not None:
                first = cur.fetchone()
                if first:
                    last_id = list(first.values())[0] if hasattr(first, "values") else first[0]
            elif has_returning or q.lstrip().upper().startswith("INSERT"):
                last_id = cur.lastrowid if has_returning or rows > 0 else None
            conn.commit()
            result = WriteResult(True, rows=rows, last_id=last_id)
        except Exception as e:
            self._rollback_quietly()
            if _sqlite_unavailable(e):
                logger.error(f"SQLite database unavailable: {e} | Query: {query[:120]}")
                raise DatabaseUnavailableError(
                    "The local database could not be written, so nothing was saved. "
                    "Your work has not been lost - try again in a moment.") from e
            result = WriteResult.failed(e)
            logger.error("Write refused: %s | %s", result.error, " ".join(query.split())[:160])
        self._remember_error(result.error)
        if strict:
            result.raise_for_error()
        return result

    def execute_update(self, query: str, params: tuple = None) -> WriteResult:
        """The same as write(): truthy when accepted, .rows for what changed."""
        return self.write(query, params)

    def execute_sql(self, query: str, params: tuple = None, max_rows: Optional[int] = None) -> SqlResult:
        """Run a typed statement and report everything - see PostgresManager.execute_sql()."""
        try:
            conn = self._get_conn()
            cur = conn.execute(self._translate_sql(query), params or ())
            if cur.description is not None:
                columns = [d[0] for d in cur.description]
                if max_rows is not None and max_rows >= 0:
                    rows = cur.fetchmany(max_rows + 1)
                    truncated = len(rows) > max_rows
                    rows = rows[:max_rows]
                else:
                    rows, truncated = cur.fetchall(), False
                conn.commit()
                return SqlResult(columns, [dict(r) for r in rows], rowcount=len(rows),
                                 truncated=truncated, is_query=True)
            rowcount = cur.rowcount
            conn.commit()
            return SqlResult(rowcount=max(rowcount, 0), is_query=False)
        except Exception as e:
            self._rollback_quietly()
            if _sqlite_unavailable(e):
                raise DatabaseUnavailableError(
                    "The local database could not be opened, so the statement did not run.") from e
            return SqlResult(error=error_text(e), kind=classify_error(e))

    def _rollback_quietly(self):
        try:
            conn = getattr(self._local, "conn", None)
            if conn is not None and conn.in_transaction:
                conn.rollback()
        except Exception:
            pass

    # ── SQL Translation ─────────────────────────────────────────────────────

    @staticmethod
    def _translate_sql(query: str) -> str:
        """Translate PostgreSQL SQL to SQLite-compatible SQL."""
        q = query

        # %s → ?  (positional params)
        q = q.replace("%s", "?")

        # ILIKE → LIKE (SQLite LIKE is case-insensitive for ASCII)
        q = q.replace(" ILIKE ", " LIKE ")
        q = q.replace(" ilike ", " LIKE ")

        # TRUNCATE TABLE → DELETE FROM
        if q.strip().upper().startswith("TRUNCATE"):
            import re
            q = re.sub(
                r'TRUNCATE\s+TABLE\s+(\w+)(\s+RESTRICT|\s+CASCADE)?',
                r'DELETE FROM \1',
                q,
                flags=re.IGNORECASE,
            )

        # Remove ::jsonb casts
        q = q.replace("::jsonb", "")
        q = q.replace("::JSONB", "")

        # SERIAL → handled by AUTOINCREMENT in schema, skip at runtime
        # JSONB → TEXT (handled in schema)

        return q

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _close_pool(self):
        """Compatibility stub matching PostgresManager."""
        self.shutdown_system()

    def force_shutdown(self):
        self.shutdown_system()

    def get_pool_stats(self) -> Dict[str, Any]:
        return {
            "backend": "sqlite",
            "db_path": self._db_path,
            "pool_initialized": hasattr(self._local, "conn") and self._local.conn is not None,
        }

    # ── Vector cache ────────────────────────────────────────────────────────

    def invalidate_vector_cache(self):
        with self._vector_cache_lock:
            self._embedding_cache = None
            for attr in ('ids_cache', 'matrix_cache', 'norms_cache'):
                if hasattr(self, attr):
                    delattr(self, attr)

    # ── Project Management (delegates to repo) ──────────────────────────────

    def get_all_projects(self, limit=1000):
        return self.project_repo.get_all_projects(limit=limit)

    def get_all_projects_summary(self, limit=1000):
        return self.project_repo.get_all_projects_summary(limit=limit)

    def record_project(self, name, template_used, target_directory, total_folders=0):
        return self.project_repo.record_project(name, template_used, target_directory, total_folders)

    def start_operation(self, project_id, operation_type):
        return self.project_repo.start_operation(project_id, operation_type)

    def update_operation(self, op_id, duration, items, errors, success):
        self.project_repo.update_operation(op_id, duration, items, errors, success)

    def record_task_detail(self, op_id, name, src, dst, size, duration, status, error=""):
        self.project_repo.record_task_detail(op_id, name, src, dst, size, duration, status, error)

    # ── Stock Library (delegates to repo) ───────────────────────────────────

    def add_stock_asset(self, path, thumb_path="", proxy_path="", tags=None, metadata=None):
        return self.stock_repo.add_stock_asset(path, thumb_path, proxy_path, tags, metadata)

    def add_stock_assets_batch(self, assets_list):
        self.stock_repo.add_stock_assets_batch(assets_list)

    def update_stock_asset_paths(self, asset_id, thumb_path=None, proxy_path=None, file_path=None):
        self.stock_repo.update_stock_asset_paths(asset_id, thumb_path, proxy_path, file_path)

    def update_asset_tags(self, asset_id, new_tags):
        return self.stock_repo.update_asset_tags(asset_id, new_tags)

    def update_asset_metadata(self, asset_id, metadata_str, tags_str):
        return self.stock_repo.update_asset_metadata(asset_id, metadata_str, tags_str)

    def count_stock_assets(self, search_query=None, file_types=None, asset_ids=None) -> int:
        """How many assets match the filter, so the count agrees with the list."""
        return self.stock_repo.count_stock_assets(search_query, file_types, asset_ids)

    def list_stock_paths(self):
        """Just the paths, for the ingest to tell what it already holds."""
        return self.stock_repo.list_stock_paths()

    def get_stock_count(self):
        return self.stock_repo.get_stock_count()

    def get_all_stock_assets(self, limit=None, offset=0, search_query=None, file_types=None, asset_ids=None):
        return self.stock_repo.get_all_stock_assets(limit, offset, search_query, file_types, asset_ids)

    def get_stock_file_types(self):
        return self.stock_repo.get_stock_file_types()

    def get_stock_tags(self):
        return self.stock_repo.get_stock_tags()

    def remove_stock_asset(self, asset_id):
        return self.stock_repo.remove_stock_asset(asset_id)

    def remove_stock_asset_by_path(self, file_path):
        return self.stock_repo.remove_stock_asset_by_path(file_path)

    def clear_stock_library(self):
        return self.stock_repo.clear_stock_library()

    def clear_stock_assets(self):
        return self.clear_stock_library()

    # ── Tracking / Dashboard ────────────────────────────────────────────────

    def save_tracking_project(self, code, name, config_json):
        q = """
            INSERT INTO tracking_projects (code, name, config_json, last_updated)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = excluded.name,
                config_json = excluded.config_json,
                last_updated = excluded.last_updated
        """
        return bool(self.execute_update(q, (code, name, config_json, datetime.now().isoformat())))

    def get_tracking_project(self, code):
        q = "SELECT config_json FROM tracking_projects WHERE code=%s"
        res = self.execute_query(q, (code,), fetch="one")
        return json.loads(res['config_json']) if res else None

    def get_all_tracking_projects(self):
        q = "SELECT config_json FROM tracking_projects WHERE active=1 ORDER BY code"
        rows = self.execute_query(q) or []
        return [json.loads(r['config_json']) for r in rows if r.get('config_json')]

    def delete_tracking_project(self, code):
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM tracking_tasks WHERE project_code=?", (code,))
            conn.execute("DELETE FROM tracking_shots WHERE project_code=?", (code,))
            conn.execute("DELETE FROM tracking_projects WHERE code=?", (code,))
            conn.execute("DELETE FROM change_history WHERE project_code=?", (code,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Delete tracking project failed: {e}")
            return False

    def save_tracking_shots(self, project_code, shots_data):
        if not shots_data:
            return
        try:
            timestamp = datetime.now().isoformat()
            sql = """
                INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, data_json, last_updated, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT (project_code, reel, shot_name) DO UPDATE SET
                    status=excluded.status,
                    priority=excluded.priority,
                    data_json=excluded.data_json,
                    last_updated=excluded.last_updated,
                    version=tracking_shots.version + 1
            """
            values = [(project_code, _reel_of(s[3]), s[0], s[1], s[2], s[3], timestamp)
                      for s in shots_data]
            conn = self._get_conn()
            conn.executemany(sql, values)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Save shots failed: {e}")
            return False

    def get_tracking_shots(self, project_code):
        from .tracking_repository import shot_row_to_dict
        q = ("SELECT id, reel, shot_name, data_json, version FROM tracking_shots "
             "WHERE project_code=%s")
        rows = self.execute_query(q, (project_code,)) or []
        return [d for d in (shot_row_to_dict(r) for r in rows) if d is not None]

    def update_tracking_shot_safe(self, project_code, shot_name, data_json,
                                  current_version, reel=None):
        from .tracking_repository import status_and_priority
        timestamp = datetime.now().isoformat()
        if reel is None:
            reel = _reel_of(data_json)
        status, priority = status_and_priority(data_json)
        q = """
            UPDATE tracking_shots
            SET data_json=%s, status=%s, priority=%s, version=version+1, last_updated=%s
            WHERE project_code=%s AND reel=%s AND shot_name=%s AND version=%s
        """
        result = self.execute_query(
            q, (data_json, status, priority, timestamp, project_code, reel, shot_name, current_version),
            fetch="rowcount"
        )
        return (result or 0) > 0

    def _get_tracking_tasks_columns(self) -> set:
        cache = getattr(self, "_tracking_tasks_columns_cache", None)
        if cache:
            return cache
        conn = self._get_conn()
        cur = conn.execute("PRAGMA table_info(tracking_tasks)")
        rows = cur.fetchall()
        cols = {r["name"] for r in rows}
        self._tracking_tasks_columns_cache = cols
        return cols

    def get_tracking_tasks(self, project_code):
        cols = self._get_tracking_tasks_columns()
        if "artist_name" in cols:
            artist_select = "t.artist_name AS artist"
        elif "artist" in cols:
            artist_select = "t.artist AS artist_name"
        else:
            artist_select = "NULL AS artist"

        q = """
            SELECT t.*, {artist_select}
            FROM tracking_tasks t
            JOIN tracking_shots s ON t.shot_id = s.id
            WHERE s.project_code = %s
        """.format(artist_select=artist_select)
        rows = self.execute_query(q, (project_code,)) or []
        return [dict(r) for r in rows]

    def save_tracking_tasks(self, project_code, tasks_data):
        if not tasks_data:
            return
        try:
            cols = self._get_tracking_tasks_columns()
            artist_col = "artist" if "artist" in cols else "artist_name"
            has_project_code = "project_code" in cols

            insert_columns = ["shot_id"]
            if has_project_code:
                insert_columns.append("project_code")
            insert_columns.extend(["department", "status", artist_col, "artist_id", "bid_days", "target_date"])

            conflict_target = "(project_code, shot_id, department)" if has_project_code else "(shot_id, department)"
            placeholders = ", ".join(["?"] * len(insert_columns))

            update_assignments = [
                "status = excluded.status",
                f"{artist_col} = excluded.{artist_col}",
                "artist_id = excluded.artist_id",
                "bid_days = excluded.bid_days",
                "target_date = excluded.target_date",
            ]
            if has_project_code:
                update_assignments.append("project_code = excluded.project_code")

            sql = f"""
                INSERT INTO tracking_tasks ({', '.join(insert_columns)})
                VALUES ({placeholders})
                ON CONFLICT {conflict_target} DO UPDATE SET
                    {', '.join(update_assignments)}
            """

            values = []
            for t in tasks_data:
                row = [t["shot_id"]]
                if has_project_code:
                    row.append(project_code)
                row.extend([
                    t["department"],
                    t.get("status", ""),
                    t.get("artist", t.get("artist_name", "")),
                    t.get("artist_id"),
                    t.get("bid_days", 0.0),
                    t.get("target", t.get("target_date", "")),
                ])
                values.append(tuple(row))

            conn = self._get_conn()
            conn.executemany(sql, values)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Save tasks failed: {e}")
            return False

    # ── Users ───────────────────────────────────────────────────────────────

    def sync_users(self, users_dict):
        if not users_dict:
            return True
        try:
            timestamp = datetime.now().isoformat()
            sql = """
                INSERT INTO ut_users (username, display_name, roles, password_hash, job_title, profile_pic_path, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (username) DO UPDATE SET
                    display_name = excluded.display_name,
                    roles = excluded.roles,
                    password_hash = CASE WHEN excluded.password_hash != '' THEN excluded.password_hash ELSE ut_users.password_hash END,
                    job_title = CASE WHEN excluded.job_title != '' THEN excluded.job_title ELSE ut_users.job_title END,
                    profile_pic_path = CASE WHEN excluded.profile_pic_path != '' THEN excluded.profile_pic_path ELSE ut_users.profile_pic_path END,
                    last_synced = excluded.last_synced
            """
            values = []
            for username, data in users_dict.items():
                roles = data.get("roles", [])
                if not roles and "role" in data:
                    roles = [data["role"]]
                elif not roles:
                    roles = ["Artist"]
                roles_str = json.dumps(roles if isinstance(roles, list) else [roles])
                values.append((
                    username,
                    data.get("display_name", ""),
                    roles_str,
                    data.get("password_hash", ""),
                    data.get("job_title", ""),
                    data.get("profile_pic_path", ""),
                    timestamp,
                ))

            conn = self._get_conn()
            conn.executemany(sql, values)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Sync users failed: {e}")
            return False

    def get_user_profile_pic(self, username):
        res = self.execute_query("SELECT profile_pic_path FROM ut_users WHERE username=%s", (username,), fetch="one")
        return res['profile_pic_path'] if res else None

    def update_user_profile_pic(self, username, path):
        return (self.execute_query("UPDATE ut_users SET profile_pic_path=%s WHERE username=%s", (path, username), fetch="rowcount") or 0) > 0

    def get_user_id(self, name_or_user):
        res = self.execute_query("SELECT id FROM ut_users WHERE username=%s", (name_or_user,), fetch="one")
        if res:
            return res['id']
        res = self.execute_query("SELECT id FROM ut_users WHERE display_name LIKE %s", (name_or_user,), fetch="one")
        return res['id'] if res else None

    # ── Embeddings / Vector Search ──────────────────────────────────────────

    def update_asset_embedding(self, asset_id, embedding_json):
        q = "UPDATE stock_library SET embedding=%s WHERE id=%s"
        success = (self.execute_query(q, (embedding_json, asset_id), fetch="rowcount") or 0) > 0
        if success:
            self.invalidate_vector_cache()
        return success

    def search_similar_assets(self, query_embedding, limit=50):
        try:
            import numpy as np
        except ImportError:
            logger.warning("numpy not available — vector search disabled in SQLite mode")
            return []

        with self._vector_cache_lock:
            if self._embedding_cache is None:
                rows = self.execute_query("SELECT id, embedding FROM stock_library WHERE embedding IS NOT NULL") or []
                if not rows:
                    return []
                ids, vecs = [], []
                for r in rows:
                    try:
                        v = r['embedding']
                        if isinstance(v, str):
                            v = json.loads(v)
                        if v:
                            ids.append(r['id'])
                            vecs.append(v)
                    except Exception:
                        continue
                if not vecs:
                    return []

                self.ids_cache = np.array(ids)
                self.matrix_cache = np.array(vecs, dtype=np.float32)
                self.norms_cache = np.linalg.norm(self.matrix_cache, axis=1)
                self.norms_cache[self.norms_cache == 0] = 1e-10
                self._embedding_cache = True

            qvec = np.array(query_embedding, dtype=np.float32)
            qnorm = np.linalg.norm(qvec) or 1e-10
            dots = np.dot(self.matrix_cache, qvec)
            sims = dots / (self.norms_cache * qnorm)
            top = np.argsort(sims)[-limit:][::-1]
            return [{'id': int(self.ids_cache[i]), 'score': float(sims[i])} for i in top if sims[i] > 0]

    # ── Maintenance ─────────────────────────────────────────────────────────

    def perform_maintenance(self, days_to_keep=30):
        try:
            cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            self.execute_query("DELETE FROM task_details WHERE timestamp < %s", (cutoff,), fetch="none")
        except Exception as e:
            logger.error(f"Maintenance error: {e}")

    def cleanup_stale_sessions(self):
        end = datetime.now().isoformat()
        self.execute_query(
            "UPDATE operations SET end_time=%s, success=0, errors=errors+1 WHERE end_time IS NULL",
            (end,), fetch="none"
        )

    # ── Change History & Audit ──────────────────────────────────────────────

    def log_change_event(self, project_code, entity_type, entity_id, user_id, action_type,
                         field, old_val, new_val, **shot):
        """
        One line of change history. user_id is the author's username (see
        change_history.py); shot may carry shot_id, shot_name, reel and
        department. Returns the WriteResult.
        """
        from .change_history import log_change
        return log_change(self, project_code, entity_type, entity_id, user_id, action_type,
                          field, old_val, new_val, **shot)

    def get_history(self, project_code=None, shot_name=None, limit=200, **shot):
        """History, newest first - see change_history.read_history()."""
        from .change_history import read_history
        try:
            return read_history(self, project_code, shot_name, limit, **shot)
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logger.exception(f"Failed to fetch history for project={project_code}, shot={shot_name}: {e}")
            return []

    # ── Stubs for less critical features ────────────────────────────────────

    def get_error_statistics(self):
        return {'total_errors': 0, 'recent_errors': []}

    def get_asset_statistics(self):
        return {'total_assets': 0, 'recent_assets': []}

    def get_compliance_data(self):
        return {'audit_trail': []}

    def export_data(self, table, path):
        return True
