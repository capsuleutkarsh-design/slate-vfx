"""
Schema and data repairs for the shared data fixes (the "data foundation").

Additive and repeatable, like workplace_schema.py. The one-off repairs are
separate functions the registry runs once and records (registry.py).

    studio_settings             one table for settings that belong to the
                                studio rather than one machine
                                (see core/infra/studio_settings.py)
    attendance_log.metadata     TEXT -> JSONB on PostgreSQL. The attendance
                                code merges with "metadata || %s::jsonb",
                                which on a TEXT column is string
                                concatenation: '{"wfh": true}' became
                                '{"wfh": true}{}' at punch-out, which is not
                                JSON, so the WFH flag and the auto-logout
                                marker vanished, and an HR correction of an
                                existing day was refused outright
                                ("COALESCE types text and jsonb cannot be
                                matched"). Rows already concatenated are
                                merged back into one object first.
    change_history              shot_id, shot_name, reel, department columns,
                                so a shot's history is found by its id rather
                                than by LIKE 'SH010_%' (where '_' matches any
                                character and the reel is not considered)
    prod_bidding.currency       each bid's currency; bids that already exist
                                were made in dollars and are marked USD
    ut_users.is_service         accounts that are not people (admin, tester)
                                and must not be offered in people pickers
"""

from __future__ import annotations

import json
import logging
import socket

from .registry import is_postgres

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ helpers

def _table_exists(db, name: str) -> bool:
    from .workplace_schema import _table_exists as exists
    return exists(db, name)


def _column_exists(db, table: str, column: str) -> bool:
    from .workplace_schema import _column_exists as exists
    return exists(db, table, column)


def _column_type(db, table: str, column: str) -> str:
    from .workplace_schema import _column_type as col_type
    return col_type(db, table, column)


def _add_column(db, table: str, column: str, pg_type: str, lite_type: str) -> bool:
    """Add a column if it is missing. True when it was added just now."""
    if not _table_exists(db, table) or _column_exists(db, table, column):
        return False
    result = db.execute_update("ALTER TABLE %s ADD COLUMN %s %s" % (
        table, column, pg_type if is_postgres(db) else lite_type))
    if not result:
        logger.error("Could not add %s.%s: %s", table, column, getattr(result, "error", ""))
        return False
    return True


def merge_json_objects(raw) -> dict:
    """
    One dict from whatever an attendance row's metadata holds.

    Handles the damage the TEXT column did: '{"wfh": true}{}' and
    '{}{"cutoff": "19:30:00", "auto_logout": true}' - several JSON objects
    written one after another. They are merged left to right, which is what
    "||" would have done on a JSONB column. Anything unreadable is {}.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw).strip()
    if not text:
        return {}
    decoder = json.JSONDecoder()
    merged: dict = {}
    position = 0
    while position < len(text):
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        if position >= len(text):
            break
        try:
            value, position = decoder.raw_decode(text, position)
        except ValueError:
            break
        if isinstance(value, dict):
            merged.update(value)
    return merged


# ------------------------------------------------------------------ schema

def apply_migration(db) -> bool:
    if db is None:
        return False
    postgres = is_postgres(db)

    from slate.core.infra.studio_settings import ensure_table
    if not ensure_table(db):
        logger.error("studio_settings could not be created.")

    _attendance_metadata_to_jsonb(db)

    for column, pg_type, lite_type in (
        ("shot_id", "INTEGER", "INTEGER"),
        ("shot_name", "TEXT", "TEXT"),
        ("reel", "TEXT", "TEXT"),
        ("department", "TEXT", "TEXT"),
    ):
        _add_column(db, "change_history", column, pg_type, lite_type)
    if _table_exists(db, "change_history"):
        db.execute_update("CREATE INDEX IF NOT EXISTS idx_change_history_shot "
                          "ON change_history (project_code, shot_id)")
        db.execute_update("CREATE INDEX IF NOT EXISTS idx_change_history_time "
                          "ON change_history (timestamp)")

    if _add_column(db, "prod_bidding", "currency", "VARCHAR(3)", "TEXT"):
        # FIX_PLAN: bids that already exist keep their numbers and are USD.
        # Only on the run that adds the column - a later bid saved without a
        # currency must not quietly become dollars.
        db.execute_update("UPDATE prod_bidding SET currency = 'USD' WHERE currency IS NULL")

    if _add_column(db, "ut_users", "is_service", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"):
        db.execute_update("UPDATE ut_users SET is_service = 1 "
                          "WHERE LOWER(username) IN ('admin', 'tester')")
    return True


def _attendance_metadata_to_jsonb(db) -> None:
    """PostgreSQL only: repair concatenated rows, then change the type."""
    if not is_postgres(db) or not _table_exists(db, "attendance_log"):
        return
    current = _column_type(db, "attendance_log", "metadata")
    if not current or current == "jsonb":
        return
    repaired = repair_attendance_metadata(db)
    result = db.execute_update(
        "ALTER TABLE attendance_log "
        "ALTER COLUMN metadata DROP DEFAULT, "
        "ALTER COLUMN metadata TYPE JSONB USING "
        "(CASE WHEN metadata IS NULL OR btrim(metadata) = '' THEN '{}' ELSE metadata END)::jsonb, "
        "ALTER COLUMN metadata SET DEFAULT '{}'::jsonb")
    if result:
        logger.info("attendance_log.metadata is now JSONB (%s row(s) repaired first).", repaired)
    else:
        logger.error("attendance_log.metadata could not be converted to JSONB: %s. "
                     "Edits to existing attendance days will keep failing until it is.",
                     getattr(result, "error", ""))


# ------------------------------------------------------------------ one-off repairs

def repair_attendance_metadata(db):
    """
    Rewrite attendance metadata that is not a single JSON object into one.

    On PostgreSQL this runs before the type change (which would refuse the
    broken rows); on SQLite, where the column stays TEXT and json_patch
    already merged correctly, it tidies anything older. Returns the number of
    rows rewritten.
    """
    if not _table_exists(db, "attendance_log"):
        return 0
    if is_postgres(db) and _column_type(db, "attendance_log", "metadata") == "jsonb":
        return 0
    rows = db.execute_query("SELECT id, metadata FROM attendance_log", fetch="all") or []
    fixed = 0
    for row in rows:
        row = dict(row)
        raw = row.get("metadata")
        if isinstance(raw, dict):
            continue
        text = "" if raw is None else str(raw).strip()
        try:
            if text and isinstance(json.loads(text), dict):
                continue
        except ValueError:
            pass
        merged = json.dumps(merge_json_objects(text))
        if db.execute_update("UPDATE attendance_log SET metadata = %s WHERE id = %s",
                             (merged, row["id"])):
            fixed += 1
    if fixed:
        logger.info("Repaired %d attendance row(s) whose metadata was not valid JSON.", fixed)
    return fixed


def repair_tracking_columns(db):
    """
    Put tracking_shots.status and .priority back in step with the shot data.

    Board and detail-panel edits updated only data_json, so the columns other
    screens read (Home's counts, reports) kept the old values - a shot could be
    'Final' in its data and 'OMIT' in its column. Returns rows corrected.
    """
    if not _table_exists(db, "tracking_shots"):
        return 0
    rows = db.execute_query(
        "SELECT id, status, priority, data_json FROM tracking_shots", fetch="all") or []
    fixed = 0
    for row in rows:
        row = dict(row)
        raw = row.get("data_json")
        try:
            data = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        from slate.core.infra.tracking_repository import status_and_priority
        status, priority = status_and_priority(data, row.get("status"), row.get("priority"))
        if str(status or "") == str(row.get("status") or "") and int(priority) == int(row.get("priority") or 0):
            continue
        if db.execute_update("UPDATE tracking_shots SET status = %s, priority = %s WHERE id = %s",
                             (status, priority, row["id"])):
            fixed += 1
    if fixed:
        logger.info("Brought %d shot(s)' status/priority columns back in line with their data.", fixed)
    return fixed


def backfill_change_history(db):
    """
    Fill the new shot columns on history written before they existed.

    Old rows name a shot as entity_id 'SH010' (entity_type 'shot') or
    'SH010_comp' (entity_type 'task', department after the last known
    department key). The shot id is filled only when the name is unique in
    its project - two reels holding SH010 cannot be told apart from an old
    row, and a guess would put one shot's history on the other.
    """
    if not _table_exists(db, "change_history") or not _column_exists(db, "change_history", "shot_id"):
        return 0
    try:
        from slate.core.domain.departments import department_keys
        departments = sorted(department_keys(), key=len, reverse=True)
    except Exception:
        departments = ["comp", "roto", "paint", "prep", "matchmove", "fx", "lighting", "dmp"]

    rows = db.execute_query(
        "SELECT id, project_code, entity_type, entity_id FROM change_history "
        "WHERE shot_name IS NULL AND entity_type IN ('shot', 'task')", fetch="all") or []
    if not rows:
        return 0

    shots = db.execute_query(
        "SELECT id, project_code, shot_name, reel FROM tracking_shots", fetch="all") or []
    by_name = {}
    for s in shots:
        s = dict(s)
        by_name.setdefault((s["project_code"], str(s["shot_name"]).lower()), []).append(s)

    filled = 0
    for row in rows:
        row = dict(row)
        entity = str(row.get("entity_id") or "")
        shot_name, department = entity, ""
        if row.get("entity_type") == "task":
            for key in departments:
                if entity.lower().endswith("_" + key.lower()):
                    shot_name, department = entity[: -(len(key) + 1)], key
                    break
        matches = by_name.get((row.get("project_code"), shot_name.lower()), [])
        shot_id = matches[0]["id"] if len(matches) == 1 else None
        reel = matches[0].get("reel") if len(matches) == 1 else None
        if db.execute_update(
                "UPDATE change_history SET shot_name = %s, department = %s, shot_id = %s, reel = %s "
                "WHERE id = %s", (shot_name, department, shot_id, reel, row["id"])):
            filled += 1
    logger.info("Filled shot details on %d history row(s).", filled)
    return filled


def adopt_machine_settings(db):
    """
    The first time a database has studio_settings, take the studio policy and
    bidding figures from the machine that starts first - they were stored per
    machine until now, and this is the only copy there is. Anything the
    studio has already saved in the database is left alone. After this the
    per-machine values are not read again.
    """
    from slate.core.infra.studio_settings import StudioSettings
    store = StudioSettings(db)
    host = socket.gethostname()
    taken = []

    if not store.is_set("attendance_policy"):
        policy = {}
        try:
            from slate.core.infra.global_config import GlobalConfig
            from slate.core.infra.studio_policy import SETTINGS_KEYS
            for setting, rule in SETTINGS_KEYS.items():
                value = GlobalConfig.get(setting, None)
                if value not in (None, ""):
                    policy[rule] = value
            auto = GlobalConfig.get("default_auto_logout_time", None)
            if auto:
                policy["auto_logout_time"] = str(auto)[:5]
        except Exception as exc:
            logger.debug("No per-machine studio policy to adopt: %s", exc)
        if policy:
            result = store.set("attendance_policy", policy, by=f"migrated from {host}")
            if result:
                taken.append("attendance_policy")
            else:
                logger.warning("This machine's studio policy was not adopted: %s", result.error)

    if not store.is_set("bidding"):
        try:
            from slate.core.infra.config_manager import ConfigManager
            supplied = (ConfigManager().settings or {}).get("bidding")
        except Exception:
            supplied = None
        if isinstance(supplied, dict) and supplied:
            if store.set("bidding", supplied, by=f"migrated from {host}"):
                taken.append("bidding")

    if taken:
        logger.info("Studio settings taken from %s: %s", host, ", ".join(taken))
    return ", ".join(taken) or True
