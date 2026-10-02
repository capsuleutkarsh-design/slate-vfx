"""
Schema and one-off repairs for the IT screens (Hardware, Licences, IT Support,
Deployment).

Additive, both backends, safe to run on every start. The repairs that rewrite
old values run once (registry once=True) and only ever move a value to the
canonical spelling of the same thing - no row is deleted, nothing is dropped.

What it adds, and why:

    it_ticket_comments.internal / kind
                        notes IT keep among themselves (never shown to the
                        requester), and event lines ("Resolved by Ravi: ...")
                        told apart from replies
    it_tickets.response_met / resolution_met / resolution_hours
                        whether the promise was kept, stored when a ticket is
                        resolved, so a report can say so afterwards
    it_tickets.raised_by
                        who logged a ticket on somebody else's behalf (a phone
                        call or a walk-up at the desk)
    software_licenses.annual_cost / currency / vendor / contract_ref / notes
                        what a renewal is decided on besides the seat count
    licence_readings.source / recorded_by
                        typed by hand, back-filled or imported from the
                        licence server's report - and by whom
    licence_reminders   one reminder per licence per threshold, so the bell
                        does not repeat itself every start
    hardware_inventory.serial_number / asset_tag
                        what an insurer or an audit asks for
    it_deployments.version / notes / completed_at / completed_by
                        which version went where, why it failed, and who
                        recorded the outcome when

The old it_licenses table (and its license_key column) is left exactly as it
is: its rows are copied once into software_licenses (name, seats, expiry -
never the key) so they appear on the Licences screen, and the table is kept,
labelled legacy, not dropped.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .registry import is_postgres
from .workplace_schema import _column_exists, _table_exists

logger = logging.getLogger(__name__)


COLUMNS = [
    ("it_ticket_comments", "internal", "BOOLEAN DEFAULT FALSE", "INTEGER DEFAULT 0"),
    ("it_ticket_comments", "kind", "VARCHAR(20) DEFAULT 'reply'", "TEXT DEFAULT 'reply'"),
    ("it_tickets", "response_met", "BOOLEAN", "INTEGER"),
    ("it_tickets", "resolution_met", "BOOLEAN", "INTEGER"),
    ("it_tickets", "resolution_hours", "NUMERIC(10,2)", "REAL"),
    ("it_tickets", "raised_by", "VARCHAR(80)", "TEXT"),
    # Money as an exact decimal on PostgreSQL; as text on SQLite, whose
    # NUMERIC would turn it into a float. Read through money.to_decimal.
    ("software_licenses", "annual_cost", "NUMERIC(15,2)", "TEXT"),
    ("software_licenses", "currency", "VARCHAR(3)", "TEXT"),
    ("software_licenses", "vendor", "VARCHAR(120)", "TEXT"),
    ("software_licenses", "contract_ref", "VARCHAR(120)", "TEXT"),
    ("software_licenses", "notes", "TEXT", "TEXT"),
    ("licence_readings", "source", "VARCHAR(20) DEFAULT 'manual'", "TEXT DEFAULT 'manual'"),
    ("licence_readings", "recorded_by", "VARCHAR(80)", "TEXT"),
    ("hardware_inventory", "serial_number", "VARCHAR(120)", "TEXT"),
    ("hardware_inventory", "asset_tag", "VARCHAR(60)", "TEXT"),
    ("hardware_inventory", "type", "TEXT DEFAULT ''", "TEXT"),
    ("hardware_inventory", "location", "VARCHAR(255)", "TEXT"),
    ("hardware_inventory", "purchased_on", "DATE", "DATE"),
    ("hardware_inventory", "warranty_until", "DATE", "DATE"),
    ("it_deployments", "version", "VARCHAR(60)", "TEXT"),
    ("it_deployments", "notes", "TEXT", "TEXT"),
    ("it_deployments", "completed_at", "TIMESTAMP", "TIMESTAMP"),
    ("it_deployments", "completed_by", "VARCHAR(80)", "TEXT"),
]

TABLES_PG = {
    "licence_reminders": """
        CREATE TABLE IF NOT EXISTS licence_reminders (
            id SERIAL PRIMARY KEY,
            licence_id INTEGER NOT NULL,
            threshold INTEGER NOT NULL,
            expiry DATE,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (licence_id, threshold, expiry)
        )""",
}

TABLES_SQLITE = {
    "licence_reminders": """
        CREATE TABLE IF NOT EXISTS licence_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            licence_id INTEGER NOT NULL,
            threshold INTEGER NOT NULL,
            expiry DATE,
            sent_at TEXT DEFAULT (datetime('now')),
            UNIQUE (licence_id, threshold, expiry)
        )""",
}

INDEXES = [
    ("idx_ticket_comments_ticket", "it_ticket_comments (ticket_id)"),
    ("idx_deployments_machine", "it_deployments (target_machine)"),
]


def _add_column(db, table, column, pg_type, lite_type) -> bool:
    if not _table_exists(db, table) or _column_exists(db, table, column):
        return False
    result = db.execute_update("ALTER TABLE %s ADD COLUMN %s %s" % (
        table, column, pg_type if is_postgres(db) else lite_type))
    if not result:
        logger.error("Could not add %s.%s: %s", table, column, getattr(result, "error", ""))
        return False
    return True


def apply_migration(db) -> bool:
    """Bring the IT tables up to date. Repeatable."""
    if db is None:
        return False
    postgres = is_postgres(db)
    for name, sql in (TABLES_PG if postgres else TABLES_SQLITE).items():
        db.execute_update(sql)
        if not _table_exists(db, name):
            logger.error("IT table %s could not be created.", name)

    added = sum(1 for spec in COLUMNS if _add_column(db, *spec))

    for name, definition in INDEXES:
        table = definition.split(" ", 1)[0]
        if _table_exists(db, table):
            db.execute_update("CREATE INDEX IF NOT EXISTS %s ON %s" % (name, definition))

    if postgres and _table_exists(db, "it_tickets"):
        # New tickets are written with a P-code; the column's old default was
        # the word 'Medium', which the desk showed as 'MEDIUM' and sorted last.
        db.execute_update("ALTER TABLE it_tickets ALTER COLUMN priority SET DEFAULT 'P3'")
    if postgres and _table_exists(db, "it_licenses"):
        db.execute_update(
            "COMMENT ON TABLE it_licenses IS 'Legacy: superseded by software_licenses. "
            "Rows were copied there once (it_adopt_legacy_licences); kept, not dropped.'")

    logger.info("IT schema checked (%d column(s) added).", added)
    return True


# ------------------------------------------------------------- one-off repairs

def normalise_ticket_values(db) -> bool:
    """
    Stored ticket statuses and priorities in their canonical spelling.

    'waiting on you' fell out of Home's count; 'On Hold' fell out of every
    filter but Everything; 'Medium' showed as 'MEDIUM' and sorted after P4.
    """
    from slate.core.domain import service_desk as sd
    if not _table_exists(db, "it_tickets"):
        return True
    for status in sd.STATUSES:
        db.execute_update(
            "UPDATE it_tickets SET status = %s WHERE LOWER(TRIM(status)) = %s AND status <> %s",
            (status, status.lower(), status))
    known = tuple(s.lower() for s in sd.STATUSES)
    db.execute_update(
        "UPDATE it_tickets SET status = 'Open' WHERE status IS NULL OR LOWER(TRIM(status)) NOT IN (%s)"
        % ", ".join(["%s"] * len(known)), known)

    for word, code in sd.PRIORITY_WORDS.items():
        db.execute_update("UPDATE it_tickets SET priority = %s WHERE LOWER(TRIM(priority)) = %s",
                          (code, word))
    for code in sd.PRIORITIES:
        db.execute_update(
            "UPDATE it_tickets SET priority = %s WHERE LOWER(TRIM(priority)) = %s AND priority <> %s",
            (code, code.lower(), code))
    codes = tuple(sd.PRIORITIES)
    db.execute_update(
        "UPDATE it_tickets SET priority = 'P3' WHERE priority IS NULL OR priority NOT IN (%s)"
        % ", ".join(["%s"] * len(codes)), codes)
    return True


def normalise_hardware(db) -> bool:
    """
    One way to store 'nothing recorded' (NULL, not 'N/A' from Sync and '' from
    Add) and one spelling per machine status ('active' was missed by the
    Active filter).
    """
    from slate.core.domain import hardware as hw
    if not _table_exists(db, "hardware_inventory"):
        return True
    blank_junk_hardware_values(db)
    for status in hw.STATUSES:
        db.execute_update(
            "UPDATE hardware_inventory SET status = %s "
            "WHERE LOWER(TRIM(status)) = %s AND status <> %s",
            (status, status.lower(), status))
    return True


def blank_junk_hardware_values(db) -> bool:
    """
    Specs that say nothing - 'N/A', 'None', '', and the 'None GB' / ' GB' the
    old Live Ops sync wrote for a missing RAM figure - stored as NULL. Its own
    once-step so databases that already ran it_normalise_hardware get it too.
    """
    from slate.core.domain import hardware as hw
    if not _table_exists(db, "hardware_inventory"):
        return True
    junk = ", ".join("'%s'" % j for j in hw.JUNK)
    for column in ("location", "cpu", "gpu", "ram", "storage"):
        if _column_exists(db, "hardware_inventory", column):
            db.execute_update(
                "UPDATE hardware_inventory SET %s = NULL "
                "WHERE TRIM(%s) = '' OR LOWER(TRIM(%s)) IN (%s)"
                % (column, column, column, junk))
    return True


def adopt_legacy_licences(db) -> bool:
    """
    Copy the old it_licenses rows into software_licenses, once.

    Only the name, the seat count and the expiry. The licence key is a
    credential; it stays where it is and is not shown anywhere new.
    """
    if not (_table_exists(db, "it_licenses") and _table_exists(db, "software_licenses")):
        return True
    rows = db.execute_query(
        "SELECT software_name, seats_total, seats_used, expiry_date FROM it_licenses",
        fetch="all")
    if rows is None:
        return False
    from slate.core.domain.dates import parse_date
    copied = 0
    for row in rows:
        row = dict(row)
        name = str(row.get("software_name") or "").strip()[:100]
        if not name:
            continue
        there = db.execute_query(
            "SELECT id FROM software_licenses WHERE LOWER(software_name) = LOWER(%s)",
            (name,), fetch="one")
        if there:
            continue
        try:
            seats = max(0, int(row.get("seats_total") or 0))
            used = max(0, int(row.get("seats_used") or 0))
        except (TypeError, ValueError):
            seats, used = 0, 0
        result = db.execute_update(
            "INSERT INTO software_licenses (software_name, total_seats, active_seats, "
            "expiration_date, notes) VALUES (%s, %s, %s, %s, %s)",
            (name, seats, used, parse_date(row.get("expiry_date")),
             "Copied from the old licence list on %s." % datetime.now().strftime("%d %b %Y")))
        if result:
            copied += 1
    logger.info("Legacy licences copied into software_licenses: %d.", copied)
    return "copied %d" % copied
