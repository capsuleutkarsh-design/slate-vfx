"""Stock-library persistence methods — backend-agnostic (works with both PostgreSQL and SQLite)."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# A database that is down must not look like a studio with no data. The manager
# raises DatabaseUnavailableError precisely so a read cannot quietly come back
# empty; catching it here and returning a fallback puts the fault straight back.
# So it is re-raised, and anything else is logged before the fallback is used.
try:
    from .postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

logger = logging.getLogger(__name__)

# The two categories that are not a value of the category column.
ALL = "All"
FAVORITES = "Favorites"
STUDIO_PICKS = "Studio picks"
REMOVED = "Removed"          # deleted assets, for the people who can restore them

# Every way the list can be ordered, and how. Each ends on the id so a page
# boundary never splits or repeats rows that tie (MED-004).
SORTS = {
    "newest": "ingest_date DESC, id DESC",
    "oldest": "ingest_date ASC, id ASC",
    "name": "LOWER(COALESCE(NULLIF(display_name, ''), file_name)) ASC, id ASC",
    "name_desc": "LOWER(COALESCE(NULLIF(display_name, ''), file_name)) DESC, id DESC",
    "type": "file_type ASC, LOWER(file_name) ASC, id ASC",
    "type_desc": "file_type DESC, LOWER(file_name) DESC, id DESC",
    "size": "file_size DESC, id DESC",
    "size_asc": "file_size ASC, id ASC",
    "category": "LOWER(category) ASC, LOWER(file_name) ASC, id ASC",
    "category_desc": "LOWER(category) DESC, LOWER(file_name) DESC, id DESC",
}

# Only rows nobody has deleted. A deleted row stays for a while so the
# deletion can be undone (MED-034), and must be invisible everywhere meanwhile.
LIVE = "deleted_at IS NULL"


def _is_postgres(db) -> bool:
    """Whether this handle talks to PostgreSQL (a manager or the DatabaseManager proxy)."""
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


def _now():
    return datetime.now().replace(microsecond=0)


def _rows(result) -> int:
    """Rows changed by a write, whatever shape the manager returned."""
    rows = getattr(result, "rows", None)
    if rows is not None:
        return int(rows or 0)
    if isinstance(result, bool):
        return int(result)
    try:
        return int(result or 0)
    except (TypeError, ValueError):
        return 0


class StockRepository:
    """Stock-library persistence methods extracted from PostgresManager."""

    def __init__(self, db):
        self.db = db

    def _invalidate(self):
        invalidate = getattr(self.db, "invalidate_vector_cache", None)
        if callable(invalidate):
            try:
                invalidate()
            except Exception:
                pass

    # ------------------------------------------------------------ writing

    def add_stock_asset(
        self,
        path: str,
        thumb_path: str = "",
        proxy_path: str = "",
        tags: Optional[List[str]] = None,
        metadata: dict = None,
    ) -> int:
        """
        Add (or refresh) one asset; returns its id, 0 when it was not stored.

        The path is stored exactly as the ingest stores it, str(Path(...)).
        It used to go through GlobalConfig.abstract_path ("$SERVER/..."), so
        an imported file and the same file ingested were two rows (MED-027).
        """
        try:
            written = self.add_stock_assets_batch([{
                "file_path": str(path), "thumb_path": thumb_path or "",
                "proxy_path": proxy_path or "", "tags": tags, "metadata": metadata or {},
            }])
            if not written:
                return 0
            row = self.db.execute_query(
                "SELECT id FROM stock_library WHERE file_path = %s",
                (str(Path(str(path))),), fetch="one")
            return int(dict(row)["id"]) if row else 0
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logger.error(f"Add Stock Asset Failed: {e}")
            return 0

    @staticmethod
    def _row_values(asset: Dict[str, Any]):
        from slate.core.domain.stock_search import build_search_text, tags_text, visual_text

        p = Path(str(asset.get("file_path") or asset.get("path") or ""))
        try:
            size = p.stat().st_size if p.exists() else int(asset.get("file_size") or 0)
        except OSError:
            size = int(asset.get("file_size") or 0)
        metadata = asset.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata) if metadata.strip() else {}
            except ValueError:
                metadata = {}
        display = (asset.get("display_name") or asset.get("name") or p.name or "")
        record = dict(asset)
        record.update({"file_path": str(p), "display_name": display, "metadata": metadata})
        return (
            str(p), p.name, size, p.suffix.lower(),
            str(asset.get("thumb_path") or ""),
            str(asset.get("proxy_path") or ""),
            tags_text(asset.get("tags")),
            json.dumps(metadata or {}),
            display,
            str(asset.get("category") or ""),
            visual_text(asset.get("visual_tags")),
            build_search_text(record),
            1 if asset.get("is_sequence") else 0,
            int(asset.get("frame_first") or 0),
            int(asset.get("frame_last") or 0),
            int(asset.get("frame_count") or 0),
            str(asset.get("pattern") or ""),
            str(asset.get("added_by") or ""),
            str(asset.get("ingest_root") or ""),
        )

    # Re-ingesting or importing a file the library already has must never
    # throw away what was learned about it: real tags and metadata are kept
    # unless something real replaces them (an empty value or the old
    # "Pending" placeholder does not), and the cached pictures are kept unless
    # new ones were made. A deleted asset that comes back is live again.
    _UPSERT_COLUMNS = (
        "file_path, file_name, file_size, file_type, thumb_path, proxy_path, tags, metadata, "
        "display_name, category, visual_tags, search_text, is_sequence, frame_first, "
        "frame_last, frame_count, pattern, added_by, ingest_root"
    )
    _ON_CONFLICT = """
        ON CONFLICT (file_path) DO UPDATE SET
            file_name = EXCLUDED.file_name,
            file_size = EXCLUDED.file_size,
            file_type = EXCLUDED.file_type,
            thumb_path = CASE WHEN EXCLUDED.thumb_path = '' THEN stock_library.thumb_path
                              ELSE EXCLUDED.thumb_path END,
            proxy_path = CASE WHEN EXCLUDED.proxy_path = '' THEN stock_library.proxy_path
                              ELSE EXCLUDED.proxy_path END,
            tags = CASE WHEN EXCLUDED.tags = '' THEN stock_library.tags ELSE EXCLUDED.tags END,
            metadata = CASE WHEN EXCLUDED.metadata IN ('', '{}') THEN stock_library.metadata
                            ELSE EXCLUDED.metadata END,
            display_name = EXCLUDED.display_name,
            category = CASE WHEN EXCLUDED.category = '' THEN stock_library.category
                            ELSE EXCLUDED.category END,
            visual_tags = CASE WHEN EXCLUDED.visual_tags = '' THEN stock_library.visual_tags
                               ELSE EXCLUDED.visual_tags END,
            search_text = EXCLUDED.search_text,
            is_sequence = EXCLUDED.is_sequence,
            frame_first = EXCLUDED.frame_first,
            frame_last = EXCLUDED.frame_last,
            frame_count = EXCLUDED.frame_count,
            pattern = EXCLUDED.pattern,
            added_by = CASE WHEN stock_library.added_by IS NULL OR stock_library.added_by = ''
                            THEN EXCLUDED.added_by ELSE stock_library.added_by END,
            ingest_root = CASE WHEN EXCLUDED.ingest_root = '' THEN stock_library.ingest_root
                               ELSE EXCLUDED.ingest_root END,
            deleted_at = NULL,
            deleted_by = ''
    """

    def add_stock_assets_batch(self, assets_list: List[Dict[str, Any]]) -> int:
        """Insert or refresh many assets in one statement. Returns how many were sent."""
        if not assets_list:
            return 0
        values = [self._row_values(asset) for asset in assets_list
                  if (asset.get("file_path") or asset.get("path"))]
        if not values:
            return 0
        # One row per path: a batch naming a file twice would make PostgreSQL
        # refuse the whole statement ("cannot affect row a second time").
        unique = {}
        for row in values:
            unique[row[0]] = row
        values = list(unique.values())

        try:
            if _is_postgres(self.db):
                from psycopg2.extras import execute_values
                sql = ("INSERT INTO stock_library (" + self._UPSERT_COLUMNS + ") VALUES %s"
                       + self._ON_CONFLICT)
                with self.db.get_connection() as conn:
                    with conn.cursor() as cur:
                        execute_values(cur, sql, values)
                        conn.commit()
            else:
                placeholders = ", ".join(["?"] * len(values[0]))
                sql = ("INSERT INTO stock_library (" + self._UPSERT_COLUMNS + ") VALUES ("
                       + placeholders + ")" + self._ON_CONFLICT.replace("EXCLUDED.", "excluded."))
                with self.db.get_connection() as conn:
                    conn.executemany(sql, values)
                    conn.commit()
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logger.error(f"Batch add failed: {e}")
            raise
        self._invalidate()
        return len(values)

    # Columns update_assets_by_path may set, and how each is written.
    _UPDATABLE = ("thumb_path", "proxy_path", "tags", "metadata", "visual_tags",
                  "search_text", "category", "display_name", "is_sequence",
                  "frame_first", "frame_last", "frame_count", "pattern")

    @classmethod
    def _update_values(cls, fields: Dict[str, Any]) -> Dict[str, Any]:
        from slate.core.domain.stock_search import tags_text, visual_text

        out = {}
        for key in cls._UPDATABLE:
            if key not in fields:
                continue
            value = fields[key]
            if key == "tags":
                value = tags_text(value)
            elif key == "visual_tags":
                value = visual_text(value)
            elif key == "metadata":
                value = value if isinstance(value, str) else json.dumps(value or {})
            elif key == "is_sequence":
                value = 1 if value else 0
            elif key in ("frame_first", "frame_last", "frame_count"):
                value = int(value or 0)
            elif value is None:
                value = ""
            else:
                value = str(value)
            out[key] = value
        return out

    def update_assets_by_path(self, updates: Sequence[Dict[str, Any]]) -> int:
        """
        Write what an analysis found, keyed by file path, in one transaction.

        The ingest used to hand this to LibraryManager.update_asset, which only
        wrote tags and metadata for assets in its own in-memory list - and the
        ingest's list was always empty, so every asset stayed tagged
        "Pending" with metadata {} (MED-001). Returns the rows changed.
        """
        from .transaction import atomic

        changed = 0
        pending = []
        for update in updates or []:
            path = update.get("file_path") or update.get("path")
            fields = self._update_values(update)
            if path and fields:
                pending.append((str(Path(str(path))), fields))
        if not pending:
            return 0
        with atomic(self.db) as tx:
            for path, fields in pending:
                columns = list(fields)
                result = tx.write(
                    "UPDATE stock_library SET %s WHERE file_path = %%s"
                    % ", ".join(f"{c} = %s" for c in columns),
                    tuple(fields[c] for c in columns) + (path,))
                changed += _rows(result)
        self._invalidate()
        return changed

    def update_stock_asset_paths(self, asset_id, thumb_path=None, proxy_path=None, file_path=None):
        try:
            updates = []
            params = []

            if thumb_path is not None:
                updates.append("thumb_path=%s")
                params.append(str(thumb_path))
            if proxy_path is not None:
                updates.append("proxy_path=%s")
                params.append(str(proxy_path))

            if not updates:
                return 0

            sql = f"UPDATE stock_library SET {', '.join(updates)} WHERE "
            if asset_id:
                sql += "id=%s"
                params.append(asset_id)
            elif file_path:
                sql += "file_path=%s"
                params.append(str(Path(str(file_path))))
            else:
                logger.warning("update_stock_asset_paths: No ID or Path provided")
                return 0

            return _rows(self.db.write(sql, tuple(params)))
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logger.error(f"Failed to update asset paths: {e}")
            return 0

    def update_asset_tags(self, asset_id, new_tags) -> bool:
        return self.set_tags(asset_id, new_tags)

    def update_asset_metadata(self, asset_id, metadata_str, tags_str) -> bool:
        from slate.core.domain.stock_search import tags_text
        if not isinstance(metadata_str, str):
            metadata_str = json.dumps(metadata_str or {})
        return _rows(self.db.write(
            "UPDATE stock_library SET metadata=%s, tags=%s WHERE id=%s",
            (metadata_str, tags_text(tags_str), asset_id))) > 0

    def set_tags(self, asset_id, tags) -> bool:
        """
        Replace an asset's tags and refresh what it can be found by.

        The search text holds the tags too, so it is rebuilt from the stored
        row with the new tags in it.
        """
        from slate.core.domain.stock_search import build_search_text, tags_text

        try:
            asset_id = int(asset_id)
        except (TypeError, ValueError):
            return False
        row = self.db.execute_query(
            "SELECT id, file_path, file_name, display_name, category, metadata, visual_tags, "
            "is_sequence FROM stock_library WHERE id = %s", (asset_id,), fetch="one")
        if not row:
            return False
        record = dict(row)
        record["tags"] = tags_text(tags)
        result = self.db.write(
            "UPDATE stock_library SET tags = %s, search_text = %s WHERE id = %s",
            (record["tags"], build_search_text(record), asset_id))
        return _rows(result) > 0

    # ------------------------------------------------------------ reading

    def list_stock_paths(self) -> List[Dict]:
        """
        Just the paths, for the ingest to tell what it already has.

        The ingest used to read every column of every row - including the
        similarity vectors - to build that list, which on a large library is a
        great deal of network traffic before the first new file is even looked at.

        Deleted rows are included, with deleted_at set: the ingest leaves them
        out, so a deletion sticks however often the folder is rescanned
        (MED2-002). Restoring is done from "Removed".

        Strict: a failed read raises. Read as "no paths", every file would be
        new and the save would bring deleted assets back.
        """
        rows = self.db.execute_query(
            "SELECT id, file_path, file_size, thumb_path, metadata, deleted_at "
            "FROM stock_library", strict=True) or []
        return [dict(r) for r in rows]

    def get_stock_count(self) -> int:
        result = self.db.execute_query(
            f"SELECT COUNT(*) AS count FROM stock_library WHERE {LIVE}", fetch="one")
        if not result:
            return 0
        if isinstance(result, dict):
            return int(result.get("count", 0))
        return int(result[0]) if result else 0

    # Everything the interface actually shows. "embedding" is deliberately
    # absent: it holds the similarity-search vectors, which are large, are never
    # displayed, and were being carried across the network on every page.
    BROWSE_COLUMNS = (
        "id, file_path, file_name, file_size, file_type, "
        "thumb_path, proxy_path, tags, metadata, ingest_date, "
        "display_name, category, visual_tags, is_sequence, frame_first, frame_last, "
        "frame_count, pattern, added_by"
    )

    def _where(self, search_query=None, file_types=None, asset_ids=None, category=None,
               visual=None, username=None):
        """The filter shared by the listing and the count, so they agree."""
        from slate.core.domain.stock_search import escape_like, search_terms

        # "Removed" lists the deleted rows (MED2-028); everything else the live ones.
        clauses, params = ["deleted_at IS NOT NULL" if category == REMOVED else LIVE], []

        if asset_ids:
            clauses.append("id IN (%s)" % ",".join(["%s"] * len(asset_ids)))
            params.extend(int(a) for a in asset_ids)

        # Every word must start a word of search_text, which holds the names,
        # tags, category, folders, resolution words, frame rate, codec and kind
        # (MED-026) already lower case and split into words. The start of a
        # word, not anywhere in it: "HD" found every "uhd" (MED2-026). Typed
        # text is matched literally: "_" and "%" are LIKE wildcards (MED-025).
        for word, whole in search_terms(search_query):
            clauses.append("(' ' || COALESCE(search_text, '')) LIKE %s ESCAPE '\\'")
            params.append(("% " if whole else "%") + escape_like(word) + "%")

        if file_types:
            clauses.append("file_type IN (%s)" % ",".join(["%s"] * len(file_types)))
            params.extend(file_types)

        # The category is filtered here, with the paging, so the list, the page
        # count and the total all agree. It was filtered on screen over the
        # first page only, so most of a category was silently missing (MED-007).
        if category and category not in (ALL, REMOVED):
            if category == FAVORITES:
                clauses.append("id IN (SELECT stock_id FROM stock_favorites WHERE username = %s)")
                params.append(str(username or ""))
            elif category == STUDIO_PICKS:
                clauses.append("id IN (SELECT stock_id FROM stock_picks)")
            elif category == "Uncategorized":
                clauses.append("(category IS NULL OR category = '' OR category = %s)")
                params.append(category)
            else:
                clauses.append("category = %s")
                params.append(category)

        # Whole tags only: 'Warm' must not match 'Warmup' (MED-005).
        if visual and str(visual).strip().lower() not in ("", "any"):
            clauses.append("(',' || LOWER(COALESCE(visual_tags, '')) || ',') LIKE %s ESCAPE '\\'")
            params.append("%," + escape_like(str(visual).strip().lower()) + ",%")

        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def count_stock_assets(self, search_query=None, file_types=None,
                           asset_ids=None, category=None, visual=None,
                           username=None) -> int:
        """
        How many assets match, with the filters applied.

        The plain total was being used to drive paging even when a search was
        narrowing the results, so the number on screen disagreed with the list
        and paging through filtered results misbehaved.
        """
        where, params = self._where(search_query, file_types, asset_ids, category, visual, username)
        row = self.db.execute_query(
            "SELECT COUNT(*) AS count FROM stock_library" + where,
            tuple(params), fetch="one")
        if not row:
            return 0
        return int(row.get("count", 0)) if isinstance(row, dict) else int(row[0])

    def get_all_stock_assets(
        self, limit=None, offset=0, search_query=None, file_types=None, asset_ids=None,
        sort=None, category=None, visual=None, username=None,
    ) -> List[Dict]:
        q = f"SELECT {self.BROWSE_COLUMNS} FROM stock_library"
        where, params = self._where(search_query, file_types, asset_ids, category, visual, username)
        q += where
        q += " ORDER BY " + SORTS.get(str(sort or "newest"), SORTS["newest"])
        if limit is not None:
            q += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset)])

        results = self.db.execute_query(q, tuple(params)) or []
        return [dict(r) for r in results]

    def get_stock_file_types(self) -> List[str]:
        q = f"SELECT DISTINCT file_type FROM stock_library WHERE {LIVE} ORDER BY file_type"
        res = self.db.execute_query(q) or []
        return [r["file_type"] for r in res if r.get("file_type")]

    def get_category_counts(self) -> Dict[str, int]:
        """{category: live assets}, for the sidebar (MED-012, MED-068)."""
        rows = self.db.execute_query(
            f"SELECT COALESCE(NULLIF(category, ''), 'Uncategorized') AS category, "
            f"COUNT(*) AS n FROM stock_library WHERE {LIVE} "
            f"GROUP BY COALESCE(NULLIF(category, ''), 'Uncategorized')") or []
        return {str(r["category"]): int(r["n"]) for r in rows}

    def get_stock_tags(self) -> List[str]:
        from slate.core.domain.stock_search import real_tags

        rows = self.db.execute_query(
            f"SELECT tags FROM stock_library WHERE tags IS NOT NULL AND tags != '' AND {LIVE}") or []
        seen = {}
        for r in rows:
            for tag in real_tags(r.get("tags")):
                seen.setdefault(tag.lower(), tag)
        return sorted(seen.values(), key=str.lower)

    # ------------------------------------------------------- favourites, picks

    def favorite_ids(self, username, ids: Iterable = None) -> set:
        """The ids this person has starred (optionally only among `ids`)."""
        if not username:
            return set()
        sql = "SELECT stock_id FROM stock_favorites WHERE username = %s"
        params = [str(username)]
        ids = [int(i) for i in (ids or []) if str(i).isdigit()]
        if ids:
            sql += " AND stock_id IN (%s)" % ",".join(["%s"] * len(ids))
            params.extend(ids)
        rows = self.db.execute_query(sql, tuple(params)) or []
        return {int(r["stock_id"]) for r in rows}

    def set_favorite(self, username, asset_id, on: bool = True) -> bool:
        """Star or unstar one asset for one person. True when the database took it."""
        if not username or asset_id in (None, ""):
            return False
        if on:
            result = self.db.write(
                "INSERT INTO stock_favorites (username, stock_id) VALUES (%s, %s) "
                "ON CONFLICT (username, stock_id) DO NOTHING", (str(username), int(asset_id)))
        else:
            result = self.db.write(
                "DELETE FROM stock_favorites WHERE username = %s AND stock_id = %s",
                (str(username), int(asset_id)))
        return bool(result)

    def pick_ids(self, ids: Iterable = None) -> set:
        sql = "SELECT stock_id FROM stock_picks"
        params = []
        ids = [int(i) for i in (ids or []) if str(i).isdigit()]
        if ids:
            sql += " WHERE stock_id IN (%s)" % ",".join(["%s"] * len(ids))
            params.extend(ids)
        rows = self.db.execute_query(sql, tuple(params)) or []
        return {int(r["stock_id"]) for r in rows}

    def set_pick(self, asset_id, on: bool = True, by: str = "") -> bool:
        if asset_id in (None, ""):
            return False
        if on:
            result = self.db.write(
                "INSERT INTO stock_picks (stock_id, picked_by) VALUES (%s, %s) "
                "ON CONFLICT (stock_id) DO NOTHING", (int(asset_id), str(by or "")))
        else:
            result = self.db.write("DELETE FROM stock_picks WHERE stock_id = %s", (int(asset_id),))
        return bool(result)

    # ------------------------------------------------------------- roots

    def add_root(self, root_path, by: str = "") -> bool:
        """Remember a folder the library is ingested from (for Rescan)."""
        path = str(Path(str(root_path)))
        result = self.db.write(
            "INSERT INTO stock_roots (root_path, added_by, last_scan) VALUES (%s, %s, %s) "
            "ON CONFLICT (root_path) DO UPDATE SET last_scan = EXCLUDED.last_scan",
            (path, str(by or ""), _now()))
        return bool(result)

    def list_roots(self) -> List[str]:
        rows = self.db.execute_query(
            "SELECT root_path FROM stock_roots ORDER BY root_path") or []
        return [str(r["root_path"]) for r in rows if r.get("root_path")]

    def remove_root(self, root_path) -> bool:
        return bool(self.db.write("DELETE FROM stock_roots WHERE root_path = %s",
                                  (str(Path(str(root_path))),)))

    # ------------------------------------------------------------ deleting

    def soft_delete(self, asset_ids: Iterable, by: str = "") -> int:
        """Hide assets, keeping them so the deletion can be undone. Returns rows hidden."""
        ids = [int(i) for i in asset_ids if str(i).isdigit()]
        if not ids:
            return 0
        result = self.db.write(
            "UPDATE stock_library SET deleted_at = %%s, deleted_by = %%s "
            "WHERE %s AND id IN (%s)" % (LIVE, ",".join(["%s"] * len(ids))),
            (_now(), str(by or "")) + tuple(ids))
        self._invalidate()
        return _rows(result)

    def restore(self, asset_ids: Iterable) -> int:
        ids = [int(i) for i in asset_ids if str(i).isdigit()]
        if not ids:
            return 0
        result = self.db.write(
            "UPDATE stock_library SET deleted_at = NULL, deleted_by = '' WHERE id IN (%s)"
            % ",".join(["%s"] * len(ids)), tuple(ids))
        self._invalidate()
        return _rows(result)

    def purge_deleted(self, older_than: datetime = None, asset_ids: Iterable = None) -> List[Dict]:
        """
        Let go of what deleted rows hold once their Undo time is over: their
        favourites, picks and cached pictures. Returns the cached file paths to
        clean.

        The row itself stays, marked deleted: it is what keeps a Rescan from
        adding the file again (MED2-002), and "Removed" can still restore it
        (its thumbnail is made again then).

        older_than limits it to rows deleted before then; asset_ids to those rows.
        """
        clauses = ["deleted_at IS NOT NULL",
                   "(COALESCE(thumb_path, '') != '' OR COALESCE(proxy_path, '') != '')"]
        params: List[Any] = []
        if older_than is not None:
            clauses.append("deleted_at < %s")
            params.append(older_than)
        if asset_ids is not None:
            ids = [int(i) for i in asset_ids if str(i).isdigit()]
            if not ids:
                return []
            clauses.append("id IN (%s)" % ",".join(["%s"] * len(ids)))
            params.extend(ids)
        where = " WHERE " + " AND ".join(clauses)
        rows = [dict(r) for r in (self.db.execute_query(
            "SELECT id, thumb_path, proxy_path FROM stock_library" + where, tuple(params)) or [])]
        if not rows:
            return []
        from .transaction import atomic
        gone = [int(r["id"]) for r in rows]
        marks = ",".join(["%s"] * len(gone))
        with atomic(self.db) as tx:
            tx.write(f"DELETE FROM stock_favorites WHERE stock_id IN ({marks})", tuple(gone))
            tx.write(f"DELETE FROM stock_picks WHERE stock_id IN ({marks})", tuple(gone))
            tx.write(f"UPDATE stock_library SET thumb_path = '', proxy_path = '' "
                     f"WHERE id IN ({marks})", tuple(gone))
        self._invalidate()
        return rows

    def cached_files(self) -> List[Dict]:
        """Every row's cached thumbnail and proxy, for Clear Library to remove."""
        rows = self.db.execute_query(
            "SELECT id, thumb_path, proxy_path FROM stock_library") or []
        return [dict(r) for r in rows]

    def remove_stock_asset(self, asset_id) -> bool:
        try:
            if asset_id is None:
                return False
            result = self.db.write(
                "DELETE FROM stock_library WHERE id=%s", (int(asset_id),))
            deleted = _rows(result) > 0
            if deleted:
                self.db.write("DELETE FROM stock_favorites WHERE stock_id = %s", (int(asset_id),))
                self.db.write("DELETE FROM stock_picks WHERE stock_id = %s", (int(asset_id),))
                self._invalidate()
            return deleted
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete stock asset by id {asset_id}: {e}")
            return False

    def remove_stock_asset_by_path(self, file_path: str) -> bool:
        try:
            if not file_path:
                return False
            result = self.db.write(
                "DELETE FROM stock_library WHERE file_path=%s", (str(file_path),))
            deleted = _rows(result) > 0
            if deleted:
                self._invalidate()
            return deleted
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete stock asset by path {file_path}: {e}")
            return False

    def clear_stock_library(self):
        if _is_postgres(self.db):
            ok = self.db.write("TRUNCATE TABLE stock_library RESTRICT")
        else:
            ok = self.db.write("DELETE FROM stock_library")
        if ok:
            self.db.write("DELETE FROM stock_favorites")
            self.db.write("DELETE FROM stock_picks")
            # And the folders: a Rescan right after a clear rebuilt the whole
            # library that had just been emptied (MED2-002).
            self.db.write("DELETE FROM stock_roots")
        self._invalidate()
        return bool(ok)
