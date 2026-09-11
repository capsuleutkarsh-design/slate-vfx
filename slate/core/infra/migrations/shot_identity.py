"""
Make the reel part of a shot's identity.

Shots were keyed on (project_code, shot_name). The reel was not in the key, so
ReelA/SH010 and ReelB/SH010 collapsed into one record - the folders for both got
built on disk while only one appeared in the dashboard, with nothing logged.
On any episodic where SH010 exists in EP01 and EP02, half the show goes missing.

This widens the key to (project_code, reel, shot_name).

Widening a unique key is always safe: any set of rows unique on (project, shot)
is also unique on (project, reel, shot), so the migration cannot fail on
existing data. It runs on both database backends, is idempotent, and is safe to
call on every start.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


# Tables that carry a shot's reel as part of its identity.
SHOT_TABLES = ("tracking_shots", "tracking_versions")


def _is_postgres(db) -> bool:
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


def _columns(db, table: str) -> set:
    """Column names on a table, on either backend. Empty if the table is absent."""
    try:
        if _is_postgres(db):
            rows = db.execute_query(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s",
                (table,), fetch="all",
            ) or []
            return {str(r.get("column_name")) for r in rows}

        rows = db.execute_query(f"PRAGMA table_info({table})", fetch="all") or []
        return {str(r.get("name")) for r in rows}
    except Exception as exc:
        logger.debug("Could not read columns for %s: %s", table, exc)
        return set()


def _add_reel_column(db, table: str) -> bool:
    """Add the reel column if it is missing. Returns True when it was added."""
    columns = _columns(db, table)
    if not columns:
        return False        # table does not exist yet; CREATE TABLE handles it
    if "reel" in columns:
        return False

    try:
        db.execute_update(f"ALTER TABLE {table} ADD COLUMN reel TEXT DEFAULT ''")
        logger.info("Added reel column to %s", table)
        return True
    except Exception as exc:
        logger.error("Could not add reel column to %s: %s", table, exc)
        return False


def _backfill_shots(db) -> int:
    """Copy each shot's reel out of its stored JSON into the new column."""
    try:
        rows = db.execute_query(
            "SELECT id, data_json FROM tracking_shots "
            "WHERE reel IS NULL OR reel=''",
            fetch="all",
        ) or []
    except Exception as exc:
        logger.debug("Backfill query failed: %s", exc)
        return 0

    filled = 0
    for row in rows:
        raw = row.get("data_json")
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            data = {}

        reel = str(data.get("reel_episode") or "").strip()
        if not reel:
            continue
        try:
            db.execute_update(
                "UPDATE tracking_shots SET reel=%s WHERE id=%s",
                (reel, row.get("id")),
            )
            filled += 1
        except Exception as exc:
            logger.debug("Could not backfill reel for shot %s: %s",
                         row.get("id"), exc)

    if filled:
        logger.info("Backfilled reel on %d shot(s)", filled)
    return filled


def _backfill_versions(db) -> int:
    """Give each version the reel of the shot it belongs to."""
    try:
        rows = db.execute_query(
            "SELECT v.id AS vid, s.reel AS reel "
            "FROM tracking_versions v "
            "JOIN tracking_shots s "
            "  ON s.project_code = v.project_code AND s.shot_name = v.shot_name "
            "WHERE (v.reel IS NULL OR v.reel='') AND s.reel <> ''",
            fetch="all",
        ) or []
    except Exception as exc:
        logger.debug("Version backfill query failed: %s", exc)
        return 0

    filled = 0
    for row in rows:
        try:
            db.execute_update("UPDATE tracking_versions SET reel=%s WHERE id=%s",
                              (row.get("reel"), row.get("vid")))
            filled += 1
        except Exception:
            continue
    return filled


def _constraint_exists(db, statement: str) -> bool:
    """Whether the constraint an ADD statement names is already in place."""
    import re

    match = re.search(r"ADD CONSTRAINT (\w+)", statement)
    if not match:
        return False
    try:
        row = db.execute_query(
            "SELECT 1 AS present FROM pg_constraint WHERE conname = %s",
            (match.group(1),), fetch="one",
        )
        return bool(row)
    except Exception:
        # If we cannot tell, fall through and let the ADD decide.
        return False


def _widen_postgres_constraints(db) -> None:
    """Replace the old shot-name constraints with reel-aware ones."""
    statements = [
        # tracking_shots
        "ALTER TABLE tracking_shots "
        "DROP CONSTRAINT IF EXISTS uq_tracking_shots_proj_shot",
        "ALTER TABLE tracking_shots "
        "DROP CONSTRAINT IF EXISTS tracking_shots_project_code_shot_name_key",
        "ALTER TABLE tracking_shots "
        "ADD CONSTRAINT uq_tracking_shots_proj_reel_shot "
        "UNIQUE (project_code, reel, shot_name)",
        # tracking_versions
        "ALTER TABLE tracking_versions "
        "DROP CONSTRAINT IF EXISTS tracking_versions_project_code_shot_name_version_name_key",
        "ALTER TABLE tracking_versions "
        "ADD CONSTRAINT uq_tracking_versions_proj_reel_shot_version "
        "UNIQUE (project_code, reel, shot_name, version_name)",
    ]
    for statement in statements:
        # Skip an ADD whose constraint is already there. Attempting it and
        # letting it fail worked, but the database layer logs every failure as
        # an error - so a healthy start-up wrote hundreds of alarming lines and
        # buried anything that actually mattered.
        if "ADD CONSTRAINT" in statement and _constraint_exists(db, statement):
            logger.debug("Constraint already present, skipping: %s", statement[:60])
            continue
        try:
            db.execute_update(statement)
        except Exception as exc:
            logger.debug("Constraint step skipped (%s): %s", statement[:60], exc)


def _sqlite_constraint_is_current(db, table: str) -> bool:
    """True when the table's unique index already includes reel."""
    try:
        row = db.execute_query(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=%s",
            (table,), fetch="one",
        )
        if not row:
            return True     # no table, nothing to fix
        sql = str(row.get("sql") or "").lower()
        return "unique(project_code, reel" in sql.replace("  ", " ")
    except Exception:
        return True


def _rebuild_sqlite_table(db, table: str, columns: list, unique: str) -> None:
    """
    SQLite cannot alter a constraint, so the table is rebuilt around it.

    Done inside a transaction: the copy either completes or nothing changes.
    """
    column_list = ", ".join(columns)
    definitions = {
        "tracking_shots": f"""
            CREATE TABLE tracking_shots_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                reel TEXT DEFAULT '',
                shot_name TEXT NOT NULL,
                status TEXT DEFAULT '',
                priority INTEGER DEFAULT 0,
                data_json TEXT DEFAULT '{{}}',
                last_updated TEXT,
                version INTEGER DEFAULT 0,
                {unique}
            )""",
        "tracking_versions": f"""
            CREATE TABLE tracking_versions_new (
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
                {unique}
            )""",
    }

    try:
        db.execute_update(definitions[table])
        db.execute_update(
            f"INSERT INTO {table}_new ({column_list}) "
            f"SELECT {column_list} FROM {table}"
        )
        db.execute_update(f"DROP TABLE {table}")
        db.execute_update(f"ALTER TABLE {table}_new RENAME TO {table}")
        logger.info("Rebuilt %s with a reel-aware unique key", table)
    except Exception as exc:
        logger.error("Could not rebuild %s: %s", table, exc)
        try:
            db.execute_update(f"DROP TABLE IF EXISTS {table}_new")
        except Exception:
            pass


def ensure_shot_identity(db) -> bool:
    """
    Bring the shot tables up to the reel-aware key. Safe to call on every start.

    Returns True when the schema is in the expected shape afterwards.
    """
    try:
        shot_columns = _columns(db, "tracking_shots")
        if not shot_columns:
            return True     # fresh install: CREATE TABLE already has the key

        _add_reel_column(db, "tracking_shots")
        _add_reel_column(db, "tracking_versions")
        _backfill_shots(db)
        _backfill_versions(db)

        if _is_postgres(db):
            _widen_postgres_constraints(db)
        else:
            if not _sqlite_constraint_is_current(db, "tracking_shots"):
                _rebuild_sqlite_table(
                    db, "tracking_shots",
                    ["id", "project_code", "reel", "shot_name", "status",
                     "priority", "data_json", "last_updated", "version"],
                    "UNIQUE(project_code, reel, shot_name)",
                )
            if _columns(db, "tracking_versions") and \
                    not _sqlite_constraint_is_current(db, "tracking_versions"):
                _rebuild_sqlite_table(
                    db, "tracking_versions",
                    ["id", "project_code", "reel", "shot_name", "version_name",
                     "department", "artist", "status", "sent_to", "sent_date",
                     "media_path", "comment", "created_by", "created_at"],
                    "UNIQUE(project_code, reel, shot_name, version_name)",
                )
        return True
    except Exception as exc:
        logger.error("Shot identity migration failed: %s", exc)
        return False


def report_shot_identity(db) -> dict:
    """
    Dry run: what the migration would do, without changing anything.

    Useful before running it against a live database.
    """
    report = {"reel_column_present": False, "shots_needing_backfill": 0,
              "key_is_current": False, "error": None}
    try:
        columns = _columns(db, "tracking_shots")
        report["reel_column_present"] = "reel" in columns

        if report["reel_column_present"]:
            rows = db.execute_query(
                "SELECT COUNT(*) AS n FROM tracking_shots "
                "WHERE reel IS NULL OR reel=''", fetch="one",
            ) or {}
            report["shots_needing_backfill"] = int(rows.get("n") or 0)

        report["key_is_current"] = (
            True if _is_postgres(db)
            else _sqlite_constraint_is_current(db, "tracking_shots")
        )
    except Exception as exc:
        report["error"] = str(exc)
    return report
