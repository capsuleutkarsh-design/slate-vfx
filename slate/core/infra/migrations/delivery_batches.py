"""
Migration: Add delivery batches and delivery items tables.

Item 4.4: Work goes to clients in packages, but the software only knew about
individual versions. This migration creates:
- tracking_deliveries: named delivery packages with date, recipient, notes
- tracking_delivery_items: junction table linking versions to delivery packages
"""

import logging


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracking_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL,
    name TEXT NOT NULL,
    recipient TEXT DEFAULT '',
    delivery_date TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tracking_delivery_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL REFERENCES tracking_deliveries(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL,
    status_at_delivery TEXT DEFAULT '',
    UNIQUE(delivery_id, version_id)
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracking_deliveries (
    id SERIAL PRIMARY KEY,
    project_code TEXT NOT NULL,
    name TEXT NOT NULL,
    recipient TEXT DEFAULT '',
    delivery_date TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracking_delivery_items (
    id SERIAL PRIMARY KEY,
    delivery_id INTEGER NOT NULL REFERENCES tracking_deliveries(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL,
    status_at_delivery TEXT DEFAULT '',
    UNIQUE(delivery_id, version_id)
);
"""


def _is_postgres(db) -> bool:
    """
    Which backend is behind this handle.

    Detecting by "does it have get_connection" does not work: the SQLite
    manager has one too, so SQLite was being handed the Postgres schema and the
    migration failed silently, leaving the tables uncreated.
    """
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


def _statements(schema: str):
    """Split a schema into single statements; both drivers run one at a time."""
    return [part.strip() for part in schema.split(";") if part.strip()]


def apply_migration(db) -> bool:
    """Apply delivery schema migration idempotently, on either backend."""
    if db is None:
        return False

    schema = POSTGRES_SCHEMA if _is_postgres(db) else SQLITE_SCHEMA

    try:
        for statement in _statements(schema):
            db.execute_update(statement)
        logging.debug("Delivery batches schema is present.")
        return True
    except Exception as exc:
        logging.exception("Failed to apply delivery batches migration: %s", exc)
        return False
