"""
The production tables (Scheduling and Bidding), brought up to date on both
backends. Additive and repeatable, like workplace_schema.py; registered in
registry.py's PRODUCTION block.

    prod_scheduling     owner, department, effort_days (who is on what,
                        PRD-012); completed_on; created/updated by and when
                        (PRD-037); legacy_dates keeps the original text of a
                        date that could not be read.
                        start_date / end_date become real DATE columns on
                        PostgreSQL (PRD-019). They were TEXT: 'None',
                        '30/09/2026' and reversed dates were all accepted, and
                        '15/10/2026' sorted before '2026-09-30'. Every row is
                        first rewritten as ISO (DD/MM/YYYY read day first),
                        anything unreadable becomes NULL with its text kept
                        in legacy_dates, then the type changes - so the
                        conversion cannot fail half way and nothing typed is
                        lost. SQLite keeps TEXT, holding ISO only.
                        depends_on_id gets a foreign key with ON DELETE SET
                        NULL (PRD-020); links to milestones that no longer
                        exist are cleared first and noted in legacy_dates.
    prod_bidding        estimated_budget REAL (float4: cents wrong, whole
                        rupees lost on large bids) -> NUMERIC(15,2) (PRD-082);
                        client, day rate, discount, tax, totals, revisions,
                        who and when, archive (PRD-078/088/095/096/098/123).
    prod_bid_lines      the line items of a bid (PRD-089).
    tracking_tasks      actual_days - the days a department really spent on
                        a shot, for comparing a won bid with what happened
                        (PRD-124). The dashboard owns the field; it is only
                        added here.

repair_bid_budgets (run once) puts back the cents float4 lost: a budget that
differs from cost / (1 - margin) by no more than float4 rounding is set to
the exact figure. Existing bids keep their numbers - the numbers they were
meant to have.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .registry import is_postgres

logger = logging.getLogger(__name__)


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


# ------------------------------------------------------------------ tables

BID_LINES_PG = """
    CREATE TABLE IF NOT EXISTS prod_bid_lines (
        id SERIAL PRIMARY KEY,
        bid_id INTEGER NOT NULL REFERENCES prod_bidding(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        label TEXT NOT NULL DEFAULT '',
        shot_name TEXT DEFAULT '',
        reel TEXT DEFAULT '',
        department TEXT DEFAULT '',
        complexity TEXT DEFAULT '',
        shot_count INTEGER NOT NULL DEFAULT 1,
        days_per_shot NUMERIC(8,2) NOT NULL DEFAULT 0,
        day_rate NUMERIC(15,2) NOT NULL DEFAULT 0,
        days NUMERIC(12,2) NOT NULL DEFAULT 0,
        cost NUMERIC(15,2) NOT NULL DEFAULT 0,
        notes TEXT DEFAULT ''
    )"""

BID_LINES_SQLITE = """
    CREATE TABLE IF NOT EXISTS prod_bid_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bid_id INTEGER NOT NULL REFERENCES prod_bidding(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        label TEXT NOT NULL DEFAULT '',
        shot_name TEXT DEFAULT '',
        reel TEXT DEFAULT '',
        department TEXT DEFAULT '',
        complexity TEXT DEFAULT '',
        shot_count INTEGER NOT NULL DEFAULT 1,
        days_per_shot NUMERIC NOT NULL DEFAULT 0,
        day_rate NUMERIC NOT NULL DEFAULT 0,
        days NUMERIC NOT NULL DEFAULT 0,
        cost NUMERIC NOT NULL DEFAULT 0,
        notes TEXT DEFAULT ''
    )"""

# (table, column, PostgreSQL type, SQLite type)
COLUMNS = (
    ("prod_scheduling", "owner", "TEXT", "TEXT"),
    ("prod_scheduling", "department", "TEXT", "TEXT"),
    ("prod_scheduling", "effort_days", "NUMERIC(8,2)", "NUMERIC"),
    ("prod_scheduling", "completed_on", "DATE", "TEXT"),
    ("prod_scheduling", "legacy_dates", "TEXT", "TEXT"),
    ("prod_scheduling", "created_by", "TEXT", "TEXT"),
    ("prod_scheduling", "created_at", "TIMESTAMP", "TEXT"),
    ("prod_scheduling", "updated_by", "TEXT", "TEXT"),
    ("prod_scheduling", "updated_at", "TIMESTAMP", "TEXT"),

    # The columns the bidding tab has always written. workplace_schema adds
    # most of them too (REAL on SQLite); listed so this module alone is enough.
    ("prod_bidding", "project_code", "VARCHAR(255)", "TEXT"),
    ("prod_bidding", "project_name", "TEXT", "TEXT"),
    ("prod_bidding", "client_name", "TEXT", "TEXT"),
    ("prod_bidding", "shot_count", "INTEGER", "INTEGER"),
    ("prod_bidding", "complexity", "VARCHAR(50)", "TEXT"),
    ("prod_bidding", "estimated_days", "NUMERIC(15,2)", "NUMERIC"),
    ("prod_bidding", "target_margin", "NUMERIC(5,2)", "NUMERIC"),
    ("prod_bidding", "estimated_cost", "NUMERIC(15,2)", "NUMERIC"),
    ("prod_bidding", "currency", "VARCHAR(3)", "TEXT"),
    # New with line items, tax and revisions.
    ("prod_bidding", "day_rate", "NUMERIC(15,2)", "NUMERIC"),
    ("prod_bidding", "discount_percent", "NUMERIC(5,2)", "NUMERIC"),
    ("prod_bidding", "tax_percent", "NUMERIC(5,2)", "NUMERIC"),
    ("prod_bidding", "tax_label", "TEXT", "TEXT"),
    ("prod_bidding", "tax_amount", "NUMERIC(15,2)", "NUMERIC"),
    ("prod_bidding", "total_amount", "NUMERIC(15,2)", "NUMERIC"),
    ("prod_bidding", "notes", "TEXT", "TEXT"),
    ("prod_bidding", "bid_group", "INTEGER", "INTEGER"),
    ("prod_bidding", "revision", "INTEGER", "INTEGER"),
    ("prod_bidding", "created_by", "TEXT", "TEXT"),
    ("prod_bidding", "created_at", "TIMESTAMP", "TEXT"),
    ("prod_bidding", "updated_by", "TEXT", "TEXT"),
    ("prod_bidding", "updated_at", "TIMESTAMP", "TEXT"),
    ("prod_bidding", "sent_at", "TIMESTAMP", "TEXT"),
    ("prod_bidding", "decided_by", "TEXT", "TEXT"),
    ("prod_bidding", "decided_at", "TIMESTAMP", "TEXT"),
    ("prod_bidding", "archived_at", "TIMESTAMP", "TEXT"),
    ("prod_bidding", "archived_by", "TEXT", "TEXT"),

    ("tracking_tasks", "actual_days", "NUMERIC(8,2)", "NUMERIC"),
)

# Money and day counts that must be exact on PostgreSQL: (column, type).
_BID_NUMERIC = (
    ("estimated_budget", "NUMERIC(15,2)"),
    ("estimated_cost", "NUMERIC(15,2)"),
    ("estimated_days", "NUMERIC(15,2)"),
    ("target_margin", "NUMERIC(5,2)"),
)


def apply_migration(db) -> bool:
    if db is None:
        return False
    postgres = is_postgres(db)
    ok = True

    if _table_exists(db, "prod_bidding"):
        if not db.execute_update(BID_LINES_PG if postgres else BID_LINES_SQLITE):
            logger.error("prod_bid_lines could not be created.")
            ok = False
        db.execute_update("CREATE INDEX IF NOT EXISTS idx_bid_lines_bid ON prod_bid_lines (bid_id)")

    for table, column, pg_type, lite_type in COLUMNS:
        _add_column(db, table, column, pg_type, lite_type)

    if _table_exists(db, "prod_bidding"):
        if postgres:
            for column, wanted in _BID_NUMERIC:
                current = _column_type(db, "prod_bidding", column)
                if current and current != "numeric":
                    result = db.execute_update(
                        "ALTER TABLE prod_bidding ALTER COLUMN %s TYPE %s USING ROUND(%s::numeric, 2)"
                        % (column, wanted, column))
                    if not result:
                        logger.error("prod_bidding.%s stays %s: %s", column, current,
                                     getattr(result, "error", ""))
                        ok = False
        # Every bid is its own first revision until somebody revises it.
        db.execute_update("UPDATE prod_bidding SET bid_group = id WHERE bid_group IS NULL")
        db.execute_update("UPDATE prod_bidding SET revision = 1 WHERE revision IS NULL")
        db.execute_update("CREATE INDEX IF NOT EXISTS idx_bidding_group ON prod_bidding (bid_group)")

    if _table_exists(db, "prod_scheduling"):
        if not _normalise_schedule_dates(db):
            ok = False
        _clear_broken_dependencies(db)
        if postgres:
            ok = _schedule_dates_to_date_type(db) and ok
            _schedule_constraints(db)
        db.execute_update("CREATE INDEX IF NOT EXISTS idx_sched_project ON prod_scheduling (project_code)")
    return ok


# ------------------------------------------------------------------ scheduling

def _iso(value):
    """(iso text or None, readable?) for one stored date value."""
    from slate.core.domain.dates import parse_date
    if value is None:
        return None, True
    if isinstance(value, date):
        return value.isoformat(), True
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "nan"):
        return None, True
    parsed = parse_date(text)
    if parsed is None:
        return None, False
    return parsed.isoformat(), True


def _normalise_schedule_dates(db) -> bool:
    """
    Rewrite every stored date as ISO. A date that does not read is set to
    NULL with its text kept in legacy_dates. Repeatable: rows that are already
    ISO (or DATE) are left alone.
    """
    if is_postgres(db) and _column_type(db, "prod_scheduling", "start_date") == "date" \
            and _column_type(db, "prod_scheduling", "end_date") == "date":
        return True
    rows = db.execute_query("SELECT id, start_date, end_date, legacy_dates FROM prod_scheduling",
                            fetch="all")
    if rows is None:
        return False
    fixed = 0
    for row in rows:
        row = dict(row)
        start, start_ok = _iso(row.get("start_date"))
        end, end_ok = _iso(row.get("end_date"))
        same = (str(row.get("start_date") or "") == (start or "")
                and str(row.get("end_date") or "") == (end or ""))
        if same:
            continue
        legacy = str(row.get("legacy_dates") or "")
        lost = []
        if not start_ok:
            lost.append(f"start was '{row.get('start_date')}'")
        if not end_ok:
            lost.append(f"end was '{row.get('end_date')}'")
        if lost:
            legacy = "; ".join(x for x in (legacy, ", ".join(lost)) if x)
        if db.execute_update("UPDATE prod_scheduling SET start_date = %s, end_date = %s, "
                             "legacy_dates = %s WHERE id = %s",
                             (start, end, legacy or None, row["id"])):
            fixed += 1
    if fixed:
        logger.info("Rewrote the dates of %d milestone(s) as ISO.", fixed)
    return True


def _clear_broken_dependencies(db) -> None:
    """A link to a milestone that no longer exists (or to itself) is cleared and noted."""
    rows = db.execute_query(
        "SELECT s.id, s.depends_on_id, s.legacy_dates FROM prod_scheduling s "
        "LEFT JOIN prod_scheduling p ON p.id = s.depends_on_id "
        "WHERE s.depends_on_id IS NOT NULL AND (p.id IS NULL OR s.depends_on_id = s.id)",
        fetch="all") or []
    for row in rows:
        row = dict(row)
        note = f"depended on missing milestone #{row['depends_on_id']}"
        legacy = "; ".join(x for x in (str(row.get("legacy_dates") or ""), note) if x)
        db.execute_update("UPDATE prod_scheduling SET depends_on_id = NULL, legacy_dates = %s "
                          "WHERE id = %s", (legacy, row["id"]))
    if rows:
        logger.info("Cleared %d broken milestone dependency link(s).", len(rows))


def _schedule_dates_to_date_type(db) -> bool:
    ok = True
    for column in ("start_date", "end_date"):
        current = _column_type(db, "prod_scheduling", column)
        if not current or current == "date":
            continue
        result = db.execute_update(
            "ALTER TABLE prod_scheduling ALTER COLUMN %s TYPE DATE USING NULLIF(%s, '')::date"
            % (column, column))
        if result:
            logger.info("prod_scheduling.%s is now a DATE column.", column)
        else:
            logger.error("prod_scheduling.%s could not become DATE: %s", column,
                         getattr(result, "error", ""))
            ok = False
    return ok


def _constraint_names(db, kind: str, column: str = None) -> list:
    sql = ("SELECT c.conname AS name FROM pg_constraint c "
           "JOIN pg_class t ON t.oid = c.conrelid "
           "WHERE t.relname = 'prod_scheduling' AND c.contype = %s")
    params = [kind]
    if column:
        sql += (" AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = t.oid "
                "AND a.attnum = ANY(c.conkey) AND a.attname = %s)")
        params.append(column)
    rows = db.execute_query(sql, tuple(params), fetch="all") or []
    return [dict(r)["name"] for r in rows]


def _schedule_constraints(db) -> None:
    """The dependency foreign key (ON DELETE SET NULL) and the date order check."""
    try:
        wanted_fk = "prod_scheduling_depends_on_fk"
        existing = _constraint_names(db, "f", "depends_on_id")
        if wanted_fk not in existing:
            # An older key without ON DELETE (Alembic's) is replaced.
            for name in existing:
                db.execute_update('ALTER TABLE prod_scheduling DROP CONSTRAINT "%s"' % name)
            db.execute_update(
                "ALTER TABLE prod_scheduling ADD CONSTRAINT %s FOREIGN KEY (depends_on_id) "
                "REFERENCES prod_scheduling(id) ON DELETE SET NULL" % wanted_fk)
        if "prod_scheduling_dates_order" not in _constraint_names(db, "c"):
            # NOT VALID: rows already reversed are shown with a warning rather
            # than blocking the upgrade; every new or changed row is checked.
            db.execute_update(
                "ALTER TABLE prod_scheduling ADD CONSTRAINT prod_scheduling_dates_order "
                "CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date) NOT VALID")
    except Exception as exc:                     # constraints are a safety net, not a requirement
        logger.warning("Scheduling constraints not added: %s", exc)


# ------------------------------------------------------------------ one-off repair

def _dec(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def repair_bid_budgets(db):
    """
    Put back what float4 lost from estimated_budget: where the stored figure
    is within float4 rounding of cost / (1 - margin), store the exact figure.
    Anything further off was typed or computed differently and is left alone.
    Returns the number of bids repaired.
    """
    if not _table_exists(db, "prod_bidding"):
        return 0
    rows = db.execute_query(
        "SELECT id, estimated_cost, target_margin, estimated_budget FROM prod_bidding",
        fetch="all") or []
    fixed = 0
    cent = Decimal("0.01")
    for row in rows:
        row = dict(row)
        cost, margin, budget = (_dec(row.get("estimated_cost")), _dec(row.get("target_margin")),
                                _dec(row.get("estimated_budget")))
        if cost is None or margin is None or budget is None or cost <= 0 or not 0 <= margin < 100:
            continue
        exact = (cost / (1 - margin / Decimal(100))).quantize(cent, rounding=ROUND_HALF_UP)
        if exact == budget:
            continue
        # float4 keeps 24 bits of mantissa: its rounding is at most 2^-24 of
        # the value, plus the cent the old code then rounded to.
        tolerance = exact * Decimal(2) ** -23 + cent
        if abs(exact - budget) <= tolerance:
            if db.execute_update("UPDATE prod_bidding SET estimated_budget = %s WHERE id = %s",
                                 (exact, row["id"])):
                fixed += 1
    if fixed:
        logger.info("Restored the exact budget of %d bid(s) that float4 had rounded.", fixed)
    return fixed
