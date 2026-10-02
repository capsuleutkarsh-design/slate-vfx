"""
The stock library's own schema: what the Stock Viewer needs beyond a path.

The table held a file path, a name, a tag string and a metadata blob. Every
richer thing the screen showed was worked out again on each read - and lost:

  * the category came from the file extension, so the twelve categories the
    ingest worked out collapsed to two after a Refresh (MED-012);
  * a sequence was stored as its first frame, so "muzzle_flash[24].png" came
    back as "muzzle_flash.1001.png" with no frame count (MED-011);
  * there was nowhere to keep visual tags, a search text, who added an asset,
    which folders the library is ingested from, or that an asset was deleted
    a moment ago and might be wanted back (MED-005, MED-026, MED-034, MED-040);
  * favourites were a shared "Favorite" tag nobody could set (MED-009).

Added here, on both backends, additively and repeatably:

  stock_library   display_name, category, visual_tags, search_text,
                  is_sequence, frame_first, frame_last, frame_count, pattern,
                  added_by, ingest_root, deleted_at, deleted_by
  stock_favorites one row per person per starred asset
  stock_picks     the studio's own picks, marked by the people who manage
                  the library
  stock_roots     the folders the library was ingested from, for Rescan

repair_stock_library (once) mends rows written before this: tags split into
letters ("P,e,n,d,i,n,g") or stuck at "Pending", empty categories, names and
search text. It only reads the stored paths - never the files - so it is quick
on a large library and safe on a server whose share is not mounted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .registry import is_postgres
from .workplace_schema import _column_exists, _table_exists

logger = logging.getLogger(__name__)


# (column, PostgreSQL type, SQLite type)
STOCK_COLUMNS = (
    ("display_name", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("category", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("visual_tags", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("search_text", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("is_sequence", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
    ("frame_first", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
    ("frame_last", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
    ("frame_count", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
    ("pattern", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("added_by", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("ingest_root", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    ("deleted_at", "TIMESTAMP", "TEXT"),
    ("deleted_by", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
)

TABLES_PG = (
    """CREATE TABLE IF NOT EXISTS stock_favorites (
        username TEXT NOT NULL,
        stock_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (username, stock_id)
    )""",
    """CREATE TABLE IF NOT EXISTS stock_picks (
        stock_id INTEGER PRIMARY KEY,
        picked_by TEXT DEFAULT '',
        picked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS stock_roots (
        root_path TEXT PRIMARY KEY,
        added_by TEXT DEFAULT '',
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_scan TIMESTAMP
    )""",
)

TABLES_SQLITE = (
    """CREATE TABLE IF NOT EXISTS stock_favorites (
        username TEXT NOT NULL,
        stock_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (username, stock_id)
    )""",
    """CREATE TABLE IF NOT EXISTS stock_picks (
        stock_id INTEGER PRIMARY KEY,
        picked_by TEXT DEFAULT '',
        picked_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS stock_roots (
        root_path TEXT PRIMARY KEY,
        added_by TEXT DEFAULT '',
        added_at TEXT DEFAULT (datetime('now')),
        last_scan TEXT
    )""",
)

INDEXES = (
    ("idx_stock_category", "stock_library (category)"),
    ("idx_stock_deleted", "stock_library (deleted_at)"),
    ("idx_stock_fav_user", "stock_favorites (username)"),
)


def apply_migration(db) -> bool:
    """Columns, tables and indexes. Safe to run on every start."""
    if db is None:
        return False
    pg = is_postgres(db)
    ok = True

    for statement in (TABLES_PG if pg else TABLES_SQLITE):
        if not db.execute_update(statement):
            logger.error("Stock table not created: %s", getattr(db, "last_error", lambda: "")())
            ok = False

    if _table_exists(db, "stock_library"):
        for column, pg_type, lite_type in STOCK_COLUMNS:
            if _column_exists(db, "stock_library", column):
                continue
            result = db.execute_update("ALTER TABLE stock_library ADD COLUMN %s %s"
                                       % (column, pg_type if pg else lite_type))
            if not result:
                logger.error("Could not add stock_library.%s: %s", column,
                             getattr(result, "error", ""))
                ok = False

    for name, definition in INDEXES:
        try:
            db.execute_update(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
        except Exception as exc:
            logger.debug("Index %s skipped: %s", name, exc)
    return ok


def repaired_row(row: dict) -> dict:
    """
    The values an old row should have, from what is stored (no file access).

    Returns only the fields that change.
    """
    from slate.core.domain.metadata_engine import SmartMetadataManager
    from slate.core.domain.stock_search import (
        build_search_text, normalise_tags, real_tags, tags_text,
    )

    changes = {}
    path_text = str(row.get("file_path") or "")
    path = Path(path_text) if path_text else None

    stored_tags = row.get("tags") or ""
    tags = real_tags(stored_tags)
    if not tags and path is not None:
        # "Pending" was never replaced (MED-001): the tags the ingest would
        # have given it come from the path alone.
        _category, tags = SmartMetadataManager.get_smart_tags(path)
    new_tags = tags_text(tags)
    if new_tags != (stored_tags or ""):
        changes["tags"] = new_tags

    if not (row.get("display_name") or "").strip():
        changes["display_name"] = row.get("file_name") or (path.name if path else "")

    if not (row.get("category") or "").strip() and path is not None:
        changes["category"] = SmartMetadataManager.classify_category(path) or "Uncategorized"

    merged = dict(row)
    merged.update(changes)
    search = build_search_text(merged)
    if search != (row.get("search_text") or ""):
        changes["search_text"] = search
    return changes


def repair_stock_library(db) -> bool:
    """One-off: mend rows written before the fixes above."""
    if db is None or not _table_exists(db, "stock_library"):
        return True
    if not _column_exists(db, "stock_library", "search_text"):
        return False
    rows = db.execute_query(
        "SELECT id, file_path, file_name, tags, metadata, display_name, category, "
        "visual_tags, search_text, is_sequence FROM stock_library") or []

    from slate.core.infra.transaction import atomic

    fixed = 0
    batch = []
    for raw in rows:
        row = dict(raw)
        try:
            changes = repaired_row(row)
        except Exception as exc:
            logger.debug("Stock row %s not repaired: %s", row.get("id"), exc)
            continue
        if changes:
            batch.append((row["id"], changes))

    for start in range(0, len(batch), 500):
        chunk = batch[start:start + 500]
        with atomic(db) as tx:
            for row_id, changes in chunk:
                columns = sorted(changes)
                tx.write("UPDATE stock_library SET %s WHERE id = %%s"
                         % ", ".join(f"{c} = %s" for c in columns),
                         tuple(changes[c] for c in columns) + (row_id,))
        fixed += len(chunk)
    logger.info("Stock library repair: %d of %d rows mended.", fixed, len(rows))
    return json.dumps({"rows": len(rows), "mended": fixed})
