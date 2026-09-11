"""
Schema for the HR and IT modules.

Additive only - this adds tables and columns, and never drops or rewrites
anything that already holds data. Safe to run repeatedly; every statement
checks for itself first.

What it puts in, and why:

    holiday_calendar    nothing about leave can be counted without it
    comp_off_ledger     comp-off is earned from attendance, so it needs a
                        ledger with a source and an expiry, not a counter
    leave_year_close    the line a leave year is drawn under: what was carried
                        into the next one and what lapsed. Without it, accrual
                        from the joining date simply grows forever and a
                        carry-forward cap can never actually apply
    asset_assignments   the link between a person joining and a machine going
                        out - and therefore the only way offboarding can know
                        what to collect back
    leave_requests +    a two-stage approval chain, and the charged day count
                        stored at the time of the decision so a later policy
                        change cannot silently rewrite history
    it_tickets +        an owner, impact and urgency, and the first-response
                        timestamp an SLA is measured from
"""

import logging

logger = logging.getLogger(__name__)


def _is_postgres(db) -> bool:
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


TABLES_PG = {
    "holiday_calendar": """
        CREATE TABLE IF NOT EXISTS holiday_calendar (
            id SERIAL PRIMARY KEY,
            holiday_date DATE NOT NULL,
            name VARCHAR(160) NOT NULL,
            location VARCHAR(80) DEFAULT 'All',
            UNIQUE (holiday_date, location)
        )""",
    "comp_off_ledger": """
        CREATE TABLE IF NOT EXISTS comp_off_ledger (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(80) NOT NULL,
            earned_on DATE NOT NULL,
            days NUMERIC(4,2) NOT NULL DEFAULT 0,
            reason VARCHAR(200) DEFAULT '',
            expires_on DATE,
            consumed NUMERIC(4,2) NOT NULL DEFAULT 0,
            source VARCHAR(40) DEFAULT 'attendance'
        )""",
    "asset_assignments": """
        CREATE TABLE IF NOT EXISTS asset_assignments (
            id SERIAL PRIMARY KEY,
            machine_name VARCHAR(120) NOT NULL,
            user_id VARCHAR(80) NOT NULL,
            issued_on DATE,
            returned_on DATE,
            issued_by VARCHAR(80),
            note VARCHAR(255) DEFAULT ''
        )""",
    "leave_year_close": """
        CREATE TABLE IF NOT EXISTS leave_year_close (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(80) NOT NULL,
            leave_year INTEGER NOT NULL,
            closing_balance NUMERIC(5,2) NOT NULL DEFAULT 0,
            carried NUMERIC(5,2) NOT NULL DEFAULT 0,
            lapsed NUMERIC(5,2) NOT NULL DEFAULT 0,
            closed_on DATE,
            closed_by VARCHAR(80),
            UNIQUE (user_id, leave_year)
        )""",
    "licence_readings": """
        CREATE TABLE IF NOT EXISTS licence_readings (
            id SERIAL PRIMARY KEY,
            software_name VARCHAR(120) NOT NULL,
            taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seats_in_use INTEGER NOT NULL DEFAULT 0,
            seats_total INTEGER NOT NULL DEFAULT 0
        )""",
}

TABLES_SQLITE = {
    "holiday_calendar": """
        CREATE TABLE IF NOT EXISTS holiday_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holiday_date DATE NOT NULL,
            name TEXT NOT NULL,
            location TEXT DEFAULT 'All',
            UNIQUE (holiday_date, location)
        )""",
    "comp_off_ledger": """
        CREATE TABLE IF NOT EXISTS comp_off_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            earned_on DATE NOT NULL,
            days REAL NOT NULL DEFAULT 0,
            reason TEXT DEFAULT '',
            expires_on DATE,
            consumed REAL NOT NULL DEFAULT 0,
            source TEXT DEFAULT 'attendance'
        )""",
    "asset_assignments": """
        CREATE TABLE IF NOT EXISTS asset_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            issued_on DATE,
            returned_on DATE,
            issued_by TEXT,
            note TEXT DEFAULT ''
        )""",
    "leave_year_close": """
        CREATE TABLE IF NOT EXISTS leave_year_close (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            leave_year INTEGER NOT NULL,
            closing_balance REAL NOT NULL DEFAULT 0,
            carried REAL NOT NULL DEFAULT 0,
            lapsed REAL NOT NULL DEFAULT 0,
            closed_on DATE,
            closed_by TEXT,
            UNIQUE (user_id, leave_year)
        )""",
    "licence_readings": """
        CREATE TABLE IF NOT EXISTS licence_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            software_name TEXT NOT NULL,
            taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seats_in_use INTEGER NOT NULL DEFAULT 0,
            seats_total INTEGER NOT NULL DEFAULT 0
        )""",
}

# Columns added to tables that already exist. (table, column, postgres type, sqlite type)
COLUMNS = [
    ("leave_requests", "days_charged", "NUMERIC(4,2)", "REAL"),
    ("leave_requests", "supervisor_by", "VARCHAR(80)", "TEXT"),
    ("leave_requests", "supervisor_at", "TIMESTAMP", "TIMESTAMP"),
    ("leave_requests", "hr_by", "VARCHAR(80)", "TEXT"),
    ("leave_requests", "hr_at", "TIMESTAMP", "TIMESTAMP"),
    ("leave_requests", "decision_note", "VARCHAR(255)", "TEXT"),

    ("it_tickets", "assigned_to", "VARCHAR(80)", "TEXT"),
    ("it_tickets", "impact", "VARCHAR(20)", "TEXT"),
    ("it_tickets", "urgency", "VARCHAR(20)", "TEXT"),
    ("it_tickets", "first_response_at", "TIMESTAMP", "TIMESTAMP"),

    ("hardware_inventory", "purchased_on", "DATE", "DATE"),
    ("hardware_inventory", "warranty_until", "DATE", "DATE"),

    # Accrual counts completed months since joining, so without this every
    # person is credited from 1 January regardless of when they started.
    ("ut_users", "joined_on", "DATE", "DATE"),
    ("ut_users", "employment", "VARCHAR(20)", "TEXT"),
    ("ut_users", "reports_to", "VARCHAR(80)", "TEXT"),

    ("onboarding_workflows", "direction", "VARCHAR(16)", "TEXT"),
    ("onboarding_workflows", "owner_team", "VARCHAR(16)", "TEXT"),
    ("onboarding_workflows", "asset_name", "VARCHAR(120)", "TEXT"),
]

INDEXES = [
    ("idx_comp_off_user", "comp_off_ledger (user_id)"),
    ("idx_asset_user", "asset_assignments (user_id)"),
    ("idx_asset_open", "asset_assignments (machine_name, returned_on)"),
    ("idx_tickets_assigned", "it_tickets (assigned_to)"),
    ("idx_holiday_date", "holiday_calendar (holiday_date)"),
    ("idx_onboarding_user", "onboarding_workflows (user_id)"),
    ("idx_leave_close_user", "leave_year_close (user_id)"),
]


def _table_exists(db, name: str) -> bool:
    try:
        if _is_postgres(db):
            row = db.execute_query(
                "SELECT 1 AS present FROM information_schema.tables "
                "WHERE table_name = %s", (name,), fetch="one")
        else:
            row = db.execute_query(
                "SELECT 1 AS present FROM sqlite_master "
                "WHERE type='table' AND name=?", (name,), fetch="one")
        return bool(row)
    except Exception:
        return False


def _column_exists(db, table: str, column: str) -> bool:
    try:
        if _is_postgres(db):
            row = db.execute_query(
                "SELECT 1 AS present FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column), fetch="one")
            return bool(row)
        rows = db.execute_query("PRAGMA table_info(%s)" % table) or []
        names = {(r["name"] if isinstance(r, dict) else r[1]) for r in rows}
        return column in names
    except Exception:
        return False


def apply_migration(db) -> bool:
    """Bring the workplace tables up to date. Returns True when in shape."""
    if db is None:
        return False

    postgres = _is_postgres(db)
    tables = TABLES_PG if postgres else TABLES_SQLITE

    made = 0
    for name, sql in tables.items():
        try:
            # execute_update reports rows affected, and DDL affects none - so
            # its return value says nothing about whether this worked. Ask the
            # database afterwards instead.
            db.execute_update(sql)
            if _table_exists(db, name):
                made += 1
        except Exception as exc:
            logger.debug("Workplace table %s skipped: %s", name, exc)

    added = 0
    for table, column, pg_type, lite_type in COLUMNS:
        if not _table_exists(db, table):
            continue
        if _column_exists(db, table, column):
            continue
        try:
            db.execute_update("ALTER TABLE %s ADD COLUMN %s %s" % (
                table, column, pg_type if postgres else lite_type))
            added += 1
        except Exception as exc:
            logger.debug("Column %s.%s skipped: %s", table, column, exc)

    for name, definition in INDEXES:
        try:
            db.execute_update("CREATE INDEX IF NOT EXISTS %s ON %s" % (name, definition))
        except Exception as exc:
            logger.debug("Index %s skipped: %s", name, exc)

    logger.info("Workplace schema checked (%d table(s), %d column(s) added).", made, added)
    return True
