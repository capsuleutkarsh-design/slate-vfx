"""
Indexes for the stock library.

The table had exactly two: the identifier, and the unique file path. Every
browse sorts the whole table by ingest date, and every category filter reads
every row to check its file type. With a few hundred assets nobody notices; with
tens of thousands it is real time on every page turn.

Safe to run repeatedly - each statement checks for itself first.
"""

import logging


logger = logging.getLogger(__name__)


INDEXES = (
    # Every listing ends "ORDER BY ingest_date DESC". Without this the whole
    # table is sorted from scratch each time.
    ("idx_stock_ingest_date", "stock_library (ingest_date DESC)"),
    # The sidebar's category filter, and the file-type filter, both narrow on
    # this column.
    ("idx_stock_file_type", "stock_library (file_type)"),
    # Paging a filtered list reads type and date together.
    ("idx_stock_type_date", "stock_library (file_type, ingest_date DESC)"),
)


def _is_postgres(db) -> bool:
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


def apply_migration(db) -> bool:
    """Create the missing indexes. Returns True when the table is in shape."""
    if db is None:
        return False

    try:
        exists = db.execute_query(
            "SELECT 1 AS present FROM information_schema.tables "
            "WHERE table_name = 'stock_library'", fetch="one"
        ) if _is_postgres(db) else db.execute_query(
            "SELECT 1 AS present FROM sqlite_master "
            "WHERE type='table' AND name='stock_library'", fetch="one"
        )
        if not exists:
            # Nothing to index yet; the table is created with the schema.
            return True
    except Exception as exc:
        logger.debug("Could not check for the stock table: %s", exc)
        return False

    made = 0
    for name, definition in INDEXES:
        try:
            if db.execute_update(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}"):
                made += 1
        except Exception as exc:
            logger.debug("Index %s skipped: %s", name, exc)

    logger.info("Stock library indexes checked (%d statement(s) ran).", made)
    return True
