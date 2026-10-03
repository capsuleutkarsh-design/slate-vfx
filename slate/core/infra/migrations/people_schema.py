"""
Schema for the people screens (Attendance, Leave, Joining & Leaving, Users).

Additive and repeatable, like workplace_schema.py: columns are added only when
missing, nothing is dropped, nothing that holds data is rewritten. The one-off
data repairs are separate functions the registry runs once (registry.py).

    leave_requests.supervisor_note / hr_note
                        each approval stage keeps its own note. Both stages
                        wrote decision_note, so HR's approval wiped what the
                        supervisor had said. decision_note is still written
                        (the latest note) for anything that reads it.
    leave_requests.half_day_part
                        'first' or 'second' half of the day. A supervisor
                        could not tell whether somebody was in that morning.
    leave_requests.route_note
                        why a request skipped a stage - somebody with no
                        manager, or a manager who cannot approve leave, goes
                        straight to HR instead of waiting for ever.
    leave_requests.cancel_reason / cancel_requested_at / cancelled_by /
    cancelled_at        withdrawing leave that was already approved, and HR
                        revoking it. Approved leave could not be undone at all.
    comp_off_spends     which comp-off ledger rows paid for which request, so
                        a cancelled comp-off day goes back where it came from.
    onboarding_workflows.completed_by / completed_at
                        who ticked "Final settlement processed", and when.
    onboarding_workflows.cycle
                        which joining (or leaving) list a line belongs to: a
                        freelancer back for the next show gets a new list, and
                        the finished one stays as it was. NULL is the first.
"""

from __future__ import annotations

import logging

from .registry import is_postgres

logger = logging.getLogger(__name__)


COLUMNS = (
    # (table, column, postgres type, sqlite type)
    ("leave_requests", "supervisor_note", "TEXT", "TEXT"),
    ("leave_requests", "hr_note", "TEXT", "TEXT"),
    ("leave_requests", "half_day_part", "VARCHAR(10)", "TEXT"),
    ("leave_requests", "route_note", "TEXT", "TEXT"),
    ("leave_requests", "cancel_reason", "TEXT", "TEXT"),
    ("leave_requests", "cancel_requested_at", "TIMESTAMP", "TIMESTAMP"),
    ("leave_requests", "cancelled_by", "VARCHAR(80)", "TEXT"),
    ("leave_requests", "cancelled_at", "TIMESTAMP", "TIMESTAMP"),
    ("onboarding_workflows", "completed_by", "VARCHAR(80)", "TEXT"),
    ("onboarding_workflows", "completed_at", "TIMESTAMP", "TIMESTAMP"),
    ("onboarding_workflows", "cycle", "INTEGER", "INTEGER"),
)

SPENDS_PG = """
    CREATE TABLE IF NOT EXISTS comp_off_spends (
        id SERIAL PRIMARY KEY,
        request_id INTEGER NOT NULL,
        ledger_id INTEGER NOT NULL,
        days NUMERIC(5,2) NOT NULL DEFAULT 0
    )"""

SPENDS_SQLITE = """
    CREATE TABLE IF NOT EXISTS comp_off_spends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        ledger_id INTEGER NOT NULL,
        days REAL NOT NULL DEFAULT 0
    )"""


def _helpers():
    from .workplace_schema import _column_exists, _table_exists
    return _table_exists, _column_exists


def apply_migration(db) -> bool:
    if db is None:
        return False
    table_exists, column_exists = _helpers()
    postgres = is_postgres(db)
    ok = True

    for table, column, pg_type, lite_type in COLUMNS:
        if not table_exists(db, table) or column_exists(db, table, column):
            continue
        result = db.execute_update("ALTER TABLE %s ADD COLUMN %s %s" % (
            table, column, pg_type if postgres else lite_type))
        if not result and not column_exists(db, table, column):
            logger.error("Could not add %s.%s: %s", table, column,
                         getattr(result, "error", ""))
            ok = False

    db.execute_update(SPENDS_PG if postgres else SPENDS_SQLITE)
    if not table_exists(db, "comp_off_spends"):
        logger.error("comp_off_spends could not be created.")
        ok = False
    return ok


# ------------------------------------------------------------ one-off repairs

def split_decision_notes(db) -> bool:
    """
    Put the note each stage wrote back with that stage.

    Before supervisor_note and hr_note existed both stages wrote decision_note.
    Where HR has decided, the note there is HR's (the supervisor's was
    overwritten and is gone); where only the supervisor has, it is theirs.
    """
    table_exists, column_exists = _helpers()
    if not table_exists(db, "leave_requests") or not column_exists(db, "leave_requests", "hr_note"):
        return False
    first = db.execute_update(
        "UPDATE leave_requests SET hr_note = decision_note "
        "WHERE hr_by IS NOT NULL AND hr_by <> '' AND hr_note IS NULL "
        "AND decision_note IS NOT NULL AND decision_note <> ''")
    second = db.execute_update(
        "UPDATE leave_requests SET supervisor_note = decision_note "
        "WHERE (hr_by IS NULL OR hr_by = '') AND supervisor_by IS NOT NULL "
        "AND supervisor_by <> '' AND supervisor_note IS NULL "
        "AND decision_note IS NOT NULL AND decision_note <> ''")
    return bool(first) and bool(second)


def clear_corrected_flags(db) -> bool:
    """
    Days HR already corrected while they were flagged auto-closed or missing a
    punch-out kept the flag (update_record merged it in). Clear it on rows
    that were hand-edited and have both times, marking them corrected.
    """
    import json
    from .foundation_data import merge_json_objects
    table_exists, _ = _helpers()
    if not table_exists(db, "attendance_log"):
        return False
    rows = db.execute_query(
        "SELECT id, punch_in, punch_out, metadata FROM attendance_log "
        "WHERE punch_out IS NOT NULL", fetch="all") or []
    ok = True
    postgres = is_postgres(db)
    for raw in rows:
        row = dict(raw)
        meta = merge_json_objects(row.get("metadata"))
        if not (meta.get("admin_edit") or meta.get("edited_by")):
            continue
        if not (meta.get("auto_logout") or meta.get("missing_punch_out")):
            continue
        meta.update({"auto_logout": False, "missing_punch_out": False, "corrected": True})
        meta.pop("cutoff", None)
        result = db.execute_update(
            "UPDATE attendance_log SET metadata = %s" + ("::jsonb" if postgres else "")
            + " WHERE id = %s", (json.dumps(meta), row["id"]))
        ok = ok and bool(result)
    return ok


EMPLOYMENT_VALUES = ("Staff", "Freelance", "Contract")


def normalise_employment(db) -> bool:
    """
    'staff' written by the joining dialog and 'Staff' by Users & Roles were
    two values for one thing: the table showed both and the edit dialog added
    a second 'staff' option. One spelling now.
    """
    table_exists, column_exists = _helpers()
    if not table_exists(db, "ut_users") or not column_exists(db, "ut_users", "employment"):
        return False
    ok = True
    for value in EMPLOYMENT_VALUES:
        result = db.execute_update(
            "UPDATE ut_users SET employment = %s "
            "WHERE LOWER(employment) = LOWER(%s) AND employment <> %s",
            (value, value, value))
        ok = ok and bool(result)
    return ok
