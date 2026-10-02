"""
The change feed: one small table that says "this row of that table changed".

Open screens used to find out about other people's work by re-reading their
whole table on a timer - or, for most of the Operations screens, not at all
until Slate was restarted. The VFX Dashboard asked every three seconds and
then reloaded the entire project.

Database triggers now add one row here for every insert, update and delete on
the tables below, whichever program made the change. Each Slate window asks
"anything after the last id I saw?" every few seconds (slate/core/infra/
change_feed.py) - one indexed read that returns nothing almost all the time -
and tells each open screen only about its own tables and rows.

Additive and safe to run on every start. If a trigger cannot be created (the
database user lacks the right, a table is missing), that table is skipped and
logged, and the screens that watch it fall back to re-reading on a timer.
"""

import logging

from .workplace_schema import _column_exists, _is_postgres, _table_exists

logger = logging.getLogger(__name__)

FEED_TABLE = "slate_change_feed"

# table -> the column that names the thing that changed. For tasks and
# comments that is the shot or ticket they belong to, because that is what a
# screen shows.
WATCHED = {
    "it_tickets": "id",
    "it_ticket_comments": "ticket_id",
    "leave_requests": "id",
    "holiday_calendar": "id",
    "onboarding_workflows": "id",
    "hardware_inventory": "id",
    "asset_assignments": "id",
    "it_licenses": "id",
    "software_licenses": "id",
    "licence_readings": "licence_id",
    "tracking_shots": "id",
    "tracking_tasks": "shot_id",
    # Added with the shared table tools and the header notification centre,
    # so these screens refresh on other people's changes too.
    "it_deployments": "id",
    "prod_scheduling": "id",
    "prod_bidding": "id",
    "prod_bid_lines": "bid_id",
    "notifications": "user_id",
}

_PG_FEED = """
    CREATE TABLE IF NOT EXISTS slate_change_feed (
        id BIGSERIAL PRIMARY KEY,
        topic VARCHAR(64) NOT NULL,
        row_key TEXT,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_SQLITE_FEED = """
    CREATE TABLE IF NOT EXISTS slate_change_feed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL,
        row_key TEXT,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

# One function for every table: the key column comes in as the trigger's
# argument, and is read from the row as JSON so the column can be anything.
_PG_FUNCTION = """
    CREATE OR REPLACE FUNCTION slate_note_change() RETURNS trigger AS $$
    DECLARE
        rec JSONB;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            rec := to_jsonb(OLD);
        ELSE
            rec := to_jsonb(NEW);
        END IF;
        INSERT INTO slate_change_feed (topic, row_key)
        VALUES (TG_TABLE_NAME, rec ->> TG_ARGV[0]);
        RETURN NULL;
    END
    $$ LANGUAGE plpgsql
"""


def _pg_trigger(db, table, key) -> bool:
    # Only when missing. Creating a trigger locks the table, and every
    # workstation runs this at start - dropping and re-creating each time would
    # make a whole studio signing in at nine queue behind each other.
    try:
        present = db.execute_query(
            "SELECT 1 AS present FROM pg_trigger WHERE tgname = 'slate_change_feed' "
            "AND tgrelid = to_regclass(%s)", (table,), fetch="one")
        if present:
            return True
    except Exception:
        pass
    try:
        return bool(db.execute_update(
            "CREATE TRIGGER slate_change_feed AFTER INSERT OR UPDATE OR DELETE ON %s "
            "FOR EACH ROW EXECUTE PROCEDURE slate_note_change('%s')" % (table, key)))
    except Exception as exc:
        logger.warning("Change feed: no trigger on %s (%s)", table, exc)
        return False


def _sqlite_trigger(db, table, key) -> bool:
    ok = True
    for op, rec in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        name = "slate_feed_%s_%s" % (table, op.lower())
        try:
            db.execute_update(
                "CREATE TRIGGER IF NOT EXISTS %s AFTER %s ON %s BEGIN "
                "INSERT INTO slate_change_feed (topic, row_key) VALUES ('%s', CAST(%s.%s AS TEXT)); END"
                % (name, op, table, table, rec, key))
        except Exception as exc:
            logger.warning("Change feed: no %s trigger on %s (%s)", op, table, exc)
            ok = False
    return ok


def apply_migration(db) -> bool:
    """Create the feed and its triggers. True when the feed table exists."""
    postgres = _is_postgres(db)
    try:
        db.execute_update(_PG_FEED if postgres else _SQLITE_FEED)
        if postgres:
            db.execute_update(
                "CREATE INDEX IF NOT EXISTS idx_change_feed_changed ON slate_change_feed (changed_at)")
            has_function = db.execute_query(
                "SELECT 1 AS present FROM pg_proc WHERE proname = 'slate_note_change'", fetch="one")
            if not has_function:
                db.execute_update(_PG_FUNCTION)
    except Exception as exc:
        logger.warning("Change feed not available: %s", exc)
        return False

    watched = []
    for table, key in WATCHED.items():
        if not _table_exists(db, table) or not _column_exists(db, table, key):
            continue
        if (_pg_trigger if postgres else _sqlite_trigger)(db, table, key):
            watched.append(table)
    logger.info("Change feed watching: %s", ", ".join(watched) or "nothing")
    return _table_exists(db, FEED_TABLE)
