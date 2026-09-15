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
    it_tickets +        an owner, impact and urgency, the first-response
                        timestamp an SLA is measured from, and how long the
                        ticket sat waiting on the person who raised it
    ut_users +          joining date, employment type, who they report to,
                        location and last working day. Leave accrual, holiday
                        location, supervisor scoping and offboarding each need
                        one of these, and none of them had anywhere to live
"""

import logging

logger = logging.getLogger(__name__)


def _is_postgres(db) -> bool:
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


TABLES_PG = {
    # The table the whole Leave tab reads and writes.
    #
    # It was created in exactly two places, and neither of them was this one:
    # sqlite_manager's base schema, and a single Alembic revision. Alembic runs
    # on PostgreSQL only, is not bundled into an installed build, and cannot
    # reach one - there is no alembic.ini, no versions folder and no alembic
    # program on a workstation. So on SQLite the table existed and on every
    # PostgreSQL studio it did not, which is why the Leave tab worked in local
    # fallback and failed against the real database.
    #
    # The columns below are the base ones. The approval chain's columns are in
    # COLUMNS further down, and were already being added to a table that on
    # PostgreSQL had never been created.
    "leave_requests": """
        CREATE TABLE IF NOT EXISTS leave_requests (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(80) NOT NULL,
            type VARCHAR(50) NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            half_day BOOLEAN DEFAULT FALSE,
            reason VARCHAR(255),
            status VARCHAR(30) DEFAULT 'Pending',
            approved_by VARCHAR(80),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
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
    # The purchase side of a licence. This existed only in an Alembic revision,
    # and Alembic runs on PostgreSQL alone - so on the local database the table
    # was never created at all, the repository caught the error and returned
    # nothing, and the Licences tab showed "no licences recorded" for ever. A
    # studio running without a server had a tab that could not work and did not
    # say so.
    "software_licenses": """
        CREATE TABLE IF NOT EXISTS software_licenses (
            id SERIAL PRIMARY KEY,
            software_name VARCHAR(100) NOT NULL,
            total_seats INTEGER DEFAULT 0,
            active_seats INTEGER DEFAULT 0,
            expiration_date DATE
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
    # Also here, even though sqlite_manager's base schema creates it. This
    # module is meant to be able to bring either backend up to the shape the
    # workplace code expects, and one that quietly depends on another file
    # having run first is the reason this was missing on PostgreSQL.
    "leave_requests": """
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
        )""",
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
    "software_licenses": """
        CREATE TABLE IF NOT EXISTS software_licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            software_name TEXT NOT NULL,
            total_seats INTEGER DEFAULT 0,
            active_seats INTEGER DEFAULT 0,
            expiration_date DATE
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
    # The last five columns that existed only in an Alembic revision, and so
    # existed nowhere a studio could reach. it_tickets.priority is read in
    # twenty-five files; the IT service desk has been querying a column that is
    # not there on every PostgreSQL studio. Types are taken from the revisions
    # they came from, so a database that did once get Alembic applied sees no
    # change at all.
    ("hardware_inventory", "cpu", "VARCHAR(100)", "TEXT"),
    ("hardware_inventory", "location", "VARCHAR(255) DEFAULT 'N/A'", "TEXT DEFAULT 'N/A'"),
    ("it_tickets", "priority", "VARCHAR(20) DEFAULT 'Medium'", "TEXT DEFAULT 'Medium'"),
    ("it_tickets", "resolved_at", "TIMESTAMP", "TIMESTAMP"),
    ("payroll_records", "overtime_hours", "NUMERIC(5,2) DEFAULT 0", "REAL DEFAULT 0"),

    # A studio whose leave_requests was made by the old Alembic revision has
    # the thin version of it - no half day, no reason, no created_at - so those
    # are added here too rather than only in the CREATE above, which does
    # nothing to a table that already exists.
    ("leave_requests", "half_day", "BOOLEAN DEFAULT FALSE", "BOOLEAN DEFAULT 0"),
    ("leave_requests", "reason", "VARCHAR(255)", "TEXT"),
    ("leave_requests", "created_at", "TIMESTAMP", "TIMESTAMP"),
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
    # A holiday is a holiday somewhere. Without the person's location, a
    # Chennai-only holiday is charged against a Mumbai artist's leave.
    ("ut_users", "location", "VARCHAR(40)", "TEXT"),
    # When somebody actually leaves. Offboarding had no date, so "kit not
    # returned" fired on the day notice was given rather than after the last
    # working day.
    ("ut_users", "last_day", "DATE", "DATE"),

    # Readings were matched to a purchase by software name, so two contracts
    # for the same product shared one peak and both were reported as
    # over-subscribed.
    ("licence_readings", "licence_id", "INTEGER", "INTEGER"),

    # The resolution clock ran while a ticket was waiting on the person who
    # raised it. These two record how long it was parked so the SLA measures
    # time IT actually had the ticket.
    ("it_tickets", "waiting_since", "TIMESTAMP", "TIMESTAMP"),
    ("it_tickets", "waiting_seconds", "INTEGER", "INTEGER"),

    # The bidding tab writes all six of these, and the base schema on both
    # backends has none of them - they were added by an Alembic revision, and
    # Alembic runs on PostgreSQL alone. So saving a bid worked on a studio
    # server that had been migrated and raised UndefinedColumn everywhere else,
    # including on every local database.
    ("prod_bidding", "project_name", "TEXT", "TEXT"),
    ("prod_bidding", "project_code", "VARCHAR(255)", "TEXT"),
    ("prod_bidding", "shot_count", "INTEGER", "INTEGER"),
    ("prod_bidding", "complexity", "VARCHAR(50)", "TEXT"),
    ("prod_bidding", "estimated_days", "NUMERIC(15,2)", "REAL"),
    ("prod_bidding", "target_margin", "NUMERIC(5,2)", "REAL"),
    ("prod_bidding", "estimated_cost", "NUMERIC(15,2)", "REAL"),

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
    ("idx_readings_licence", "licence_readings (licence_id, taken_at)"),
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


# (table, column, PostgreSQL type it must have, USING expression to convert
#  what is there, new default). Only consulted on PostgreSQL; SQLite does not
# enforce column types and takes 1, 0, true and false alike.
TYPE_FIXES = (
    ("leave_requests", "half_day", "boolean", "(half_day::int <> 0)", "FALSE"),
)


def _column_type(db, table: str, column: str) -> str:
    """The column's data_type as information_schema reports it, or '' if unknown."""
    try:
        row = db.execute_query(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, column), fetch="one")
        return str((row or {}).get("data_type") or "").lower()
    except Exception as exc:
        logger.debug("Could not read the type of %s.%s: %s", table, column, exc)
        return ""


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

    # Columns whose type drifted between the database this software created
    # a year ago and the one it creates today. PostgreSQL is strict about
    # them: an INTEGER column refuses a boolean and a BOOLEAN column refuses
    # a 1 - so whichever the repository wrote, half the studios' databases
    # rejected it, and the repository reported the save as done anyway. One
    # type, made so here, and the repositories write that type.
    converted = 0
    if postgres:
        for table, column, wanted, using, default in TYPE_FIXES:
            if not _table_exists(db, table):
                continue
            current = _column_type(db, table, column)
            if not current or current == wanted:
                continue
            try:
                db.execute_update(
                    "ALTER TABLE %s ALTER COLUMN %s DROP DEFAULT, "
                    "ALTER COLUMN %s TYPE %s USING %s, "
                    "ALTER COLUMN %s SET DEFAULT %s"
                    % (table, column, column, wanted, using, column, default))
                if _column_type(db, table, column) == wanted:
                    converted += 1
                    logger.info("Converted %s.%s from %s to %s.", table, column, current, wanted)
                else:
                    logger.error("Could not convert %s.%s from %s to %s; leave "
                                 "requests will not save until it is.", table, column, current, wanted)
            except Exception as exc:
                logger.error("Converting %s.%s to %s failed: %s", table, column, wanted, exc)

    for name, definition in INDEXES:
        try:
            db.execute_update("CREATE INDEX IF NOT EXISTS %s ON %s" % (name, definition))
        except Exception as exc:
            logger.debug("Index %s skipped: %s", name, exc)

    logger.info("Workplace schema checked (%d table(s), %d column(s) added, %d column type(s) converted).",
                made, added, converted)
    return True
