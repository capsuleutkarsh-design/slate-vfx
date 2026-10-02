"""
The studio's stock library, as the Stock Viewer and the ingest see it.

Everything here reads and writes the database straight away. An earlier
version kept an in-memory copy of the library and only wrote tags and
metadata for assets it found in that copy - and the ingest's copy was always
empty, so after a complete ingest every asset was still tagged "Pending" with
no metadata (MED-001). The copy is still loaded for the few callers that ask
for the whole library at once, but nothing depends on it being there.

The legacy dictionary shape the interface reads ("name", "path", "tags",
"metadata" ...) is kept, with the stored facts added: display name, category,
sequence range, visual tags, who added it, and whether the person looking has
starred it or the studio has picked it.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..infra.global_config import GlobalConfig
from ..infra.stock_repository import ALL, STUDIO_PICKS, StockRepository

logger = logging.getLogger(__name__)

# How long a deleted asset can still be brought back. The Undo on the toast is
# the quick way; the rows also survive a restart of Slate for this long.
UNDO_WINDOW = timedelta(hours=24)


def _legacy_category(suffix):
    """What the category used to be when only the extension was known."""
    video_ext = {'.mov', '.mp4', '.avi', '.mkv', '.m4v', '.webm', '.mxf', '.r3d', '.ari'}
    image_ext = {'.jpg', '.jpeg', '.png', '.exr', '.dpx', '.tif', '.tiff', '.webp',
                 '.bmp', '.gif', '.tga', '.hdr'}
    suffix = str(suffix or "").lower()
    if suffix in video_ext:
        return "Stock Footage"
    if suffix in image_ext:
        return "Textures/Images"
    return "Uncategorized"


class LibraryManager:
    """
    Manages the Central Asset Library.
    Refactored to wrap DatabaseManager (PostgreSQL) for Single Source of Truth.
    Maintains legacy API for compatibility with GUI.
    """
    def __init__(self, db_manager=None, username=None):
        if db_manager is None:
            try:
                from ..infra.database_manager import database_manager
                db_manager = database_manager
            except Exception:
                db_manager = None
        self.db_manager = db_manager
        self.repo = StockRepository(db_manager)
        # Whose favourites "Favorites" means. The Stock Viewer sets it to the
        # person signed in.
        self.username = username or ""
        self.server_root = GlobalConfig.server_root()

        # Local cache file a remote "wipe cache" command removes.
        self.local_cache = GlobalConfig.local_cache_dir() / "Library_Cache.caplib"

        self.assets = []

    def set_user(self, username):
        self.username = str(username or "")

    def set_server_root(self, new_root_path: Path):
        """Dynamically switch the library root (Project Context)."""
        self.server_root = Path(new_root_path)
        self.assets = []
        logging.info(f"Switched Library Root to: {self.server_root}")

    # ------------------------------------------------------------ reading

    def load_library(self):
        """Loads every live asset from the database."""
        self.assets = self._convert_db_assets_to_legacy_format(
            self.repo.get_all_stock_assets()
        )

    def get_total_count(self, query=None, file_types=None, asset_ids=None,
                        category=None, visual=None):
        """
        How many assets there are, honouring any filter.

        Called with no filter this is the whole library. The browser passes its
        current search, category and visual filter so the count on screen
        matches the list (MED-006).
        """
        if query or file_types or asset_ids or (category and category != ALL) or visual:
            return self.repo.count_stock_assets(
                search_query=query, file_types=file_types, asset_ids=asset_ids,
                category=category, visual=visual, username=self.username)
        return self.repo.get_stock_count()

    def list_known_paths(self):
        """
        The paths already in the library, for the ingest to skip what it has.

        Deliberately not get_all_assets(): that reads every column of every row,
        which on a large library is a great deal of traffic before the scan even
        begins.
        """
        return self.repo.list_stock_paths()

    def search_library(self, query=None, limit=None, offset=0, file_types=None, asset_ids=None,
                       sort=None, category=None, visual=None):
        """
        One page of the library, filtered and ordered in the database.

        Returns assets in the legacy format. Does NOT update self.assets to
        avoid memory bloat.
        """
        rows = self.repo.get_all_stock_assets(
            limit=limit, offset=offset, search_query=query, file_types=file_types,
            asset_ids=asset_ids, sort=sort, category=category, visual=visual,
            username=self.username,
        )
        return self._convert_db_assets_to_legacy_format(rows)

    def save_library(self):
        """Legacy no-op: every change is written to the database as it happens."""
        pass

    def _convert_db_assets_to_legacy_format(self, db_rows):
        """Converts Database rows to the list-of-dicts format expected by UI."""
        from .stock_search import normalise_tags, real_tags

        legacy_assets = []
        if not db_rows:
            return legacy_assets

        ids = [r.get('id') for r in db_rows if r.get('id') is not None]
        try:
            favourites = self.repo.favorite_ids(self.username, ids) if self.username else set()
            picks = self.repo.pick_ids(ids)
        except Exception as exc:
            logging.debug("Favourites and picks not read: %s", exc)
            favourites, picks = set(), set()

        for row in db_rows:
            metadata = {}
            raw_meta = row.get('metadata')
            if raw_meta:
                try:
                    metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
                except Exception:
                    metadata = {}

            file_name = row.get('file_name') or Path(str(row.get('file_path') or '')).name
            display = row.get('display_name') or file_name
            category = row.get('category') or _legacy_category(row.get('file_type'))
            row_id = row.get('id')
            asset = {
                "id": str(row_id),
                "name": display,
                "display_name": display,
                "file_name": file_name,
                "path": row.get('file_path'),
                "file_path": row.get('file_path'),
                "file_type": row.get('file_type') or Path(str(row.get('file_path') or '')).suffix.lower(),
                "file_size": int(row.get('file_size') or 0),
                "category": category,
                "tags": real_tags(row.get('tags')),
                "visual_tags": normalise_tags(row.get('visual_tags')),
                "metadata": metadata,
                "thumb_path": row.get('thumb_path') or None,
                "proxy_path": row.get('proxy_path') or None,
                "ingest_date": row.get('ingest_date'),
                "is_sequence": bool(row.get('is_sequence')),
                "frame_first": int(row.get('frame_first') or 0),
                "frame_last": int(row.get('frame_last') or 0),
                "frame_count": int(row.get('frame_count') or 0),
                "pattern": row.get('pattern') or "",
                "added_by": row.get('added_by') or "",
                "is_favorite": int(row_id) in favourites if row_id is not None else False,
                "is_pick": int(row_id) in picks if row_id is not None else False,
                "status": "ready",
            }
            legacy_assets.append(asset)
        return legacy_assets

    def _infer_category_from_type(self, suffix):
        return _legacy_category(suffix)

    def get_all_assets(self):
        """Returns list of all assets."""
        if not self.assets:
            self.load_library()
        return self.assets

    def get_categories(self):
        """The categories that have assets in them, sorted."""
        return sorted(self.get_category_counts(), key=str.lower)

    def get_category_counts(self):
        """{category: how many live assets}."""
        try:
            return self.repo.get_category_counts()
        except Exception as exc:
            logging.warning("Categories could not be read: %s", exc)
            return {}

    def get_favorite_count(self):
        try:
            return len(self.repo.favorite_ids(self.username)) if self.username else 0
        except Exception:
            return 0

    def get_pick_count(self):
        try:
            return self.repo.count_stock_assets(category=STUDIO_PICKS)
        except Exception:
            return 0

    def get_all_tags(self):
        """Every tag in use, for completion in the tag editor."""
        try:
            return self.repo.get_stock_tags()
        except Exception as e:
            logging.warning(f"Failed to get tags from DB: {e}")
            return []

    # ------------------------------------------------------------ writing

    def add_asset(self, name, path, category, tags, metadata=None, thumb_path=None,
                  proxy_path=None):
        """
        Add an asset. Returns its new identifier, or 0 if it was not stored.
        """
        from .stock_search import normalise_tags
        self.repo.add_stock_assets_batch([{
            "file_path": str(path), "display_name": name, "category": category,
            "tags": normalise_tags(tags), "metadata": metadata or {},
            "thumb_path": thumb_path or "", "proxy_path": proxy_path or "",
            "added_by": self.username,
        }])
        row = self.db_manager.execute_query(
            "SELECT id FROM stock_library WHERE file_path=%s", (str(Path(str(path))),), fetch="one")
        new_id = int(dict(row)["id"]) if row else 0
        return new_id or 0

    def add_assets_batch(self, assets_list):
        """
        Store assets the ingest (or an import) found. Returns how many were sent.

        Tags go through normalise_tags, so a tag string is never split into
        its letters again (MED-003).
        """
        from .stock_search import normalise_tags

        db_ready_list = []
        for a in assets_list or []:
            if not isinstance(a, dict):
                continue
            path = a.get('file_path') or a.get('path')
            if not path:
                continue
            record = dict(a)
            record['file_path'] = path
            record['tags'] = normalise_tags(a.get('tags'))
            if not record.get('added_by'):
                record['added_by'] = self.username
            db_ready_list.append(record)
        if not db_ready_list:
            return 0
        return self.repo.add_stock_assets_batch(db_ready_list)

    def update_asset(self, asset_id, updated_data):
        """
        Write an asset's analysis to the database, by its file path.

        Returns True when a row was changed. The asset id the ingest carries is
        a fingerprint of its own, not the database's id, so the path is the key.
        """
        if not isinstance(updated_data, dict):
            return False
        data = dict(updated_data)
        if not (data.get('file_path') or data.get('path')):
            for asset in self.assets:
                if str(asset.get('id')) == str(asset_id):
                    data['file_path'] = asset.get('file_path') or asset.get('path')
                    break
        if not (data.get('file_path') or data.get('path')):
            logging.warning("update_asset: no path for asset %s", asset_id)
            return False
        if 'metadata' in data or 'tags' in data or 'category' in data:
            from .stock_search import build_search_text
            data['search_text'] = build_search_text(data)
        changed = self.repo.update_assets_by_path([data]) > 0
        for i, asset in enumerate(self.assets):
            if str(asset.get('id')) == str(asset_id):
                self.assets[i].update(updated_data)
        return changed

    def update_assets_batch(self, updates_list):
        """Write many analyses at once (one transaction). Returns rows changed."""
        from .stock_search import build_search_text
        prepared = []
        for update in updates_list or []:
            data = dict(update)
            if 'metadata' in data or 'tags' in data:
                data['search_text'] = build_search_text(data)
            prepared.append(data)
        return self.repo.update_assets_by_path(prepared)

    def update_asset_metadata(self, asset_path, metadata, tags):
        """Update an asset's details by path. Returns whether the change was stored."""
        from .stock_search import build_search_text
        meta = metadata if isinstance(metadata, dict) else {}
        if not meta and isinstance(metadata, str):
            try:
                meta = json.loads(metadata)
            except ValueError:
                meta = {}
        data = {"file_path": str(asset_path), "metadata": meta, "tags": tags}
        data["search_text"] = build_search_text(data)
        try:
            return self.repo.update_assets_by_path([data]) > 0
        except Exception as e:
            logging.exception(f"Failed to save metadata to database for {asset_path}: {e}")
            return False

    def set_tags(self, asset, tags):
        """Replace one asset's tags (asset dict or id). True when stored."""
        asset_id = asset.get('id') if isinstance(asset, dict) else asset
        return self.repo.set_tags(asset_id, tags)

    # ------------------------------------------------------- favourites, picks

    def set_favorite(self, asset_id, on=True, username=None):
        """Star (or unstar) an asset for the person signed in."""
        return self.repo.set_favorite(username or self.username, asset_id, on)

    def list_favorites(self, username=None):
        return self.repo.favorite_ids(username or self.username)

    def set_pick(self, asset_id, on=True, by=None):
        """Mark (or unmark) an asset as a studio pick."""
        return self.repo.set_pick(asset_id, on, by or self.username)

    # ------------------------------------------------------------- roots

    def remember_root(self, root_path):
        try:
            return self.repo.add_root(root_path, by=self.username)
        except Exception as exc:
            logging.warning("Ingest folder not remembered: %s", exc)
            return False

    def ingest_roots(self):
        try:
            return self.repo.list_roots()
        except Exception as exc:
            logging.warning("Ingest folders not read: %s", exc)
            return []

    # ------------------------------------------------------------ deleting

    @staticmethod
    def remove_cached_files(rows) -> tuple:
        """Delete the cached thumbnails and proxies listed. Returns (removed, failed)."""
        from .proxy_manager import ProxyManager
        removed = failed = 0
        for row in rows or []:
            for key in ("thumb_path", "proxy_path"):
                cached = row.get(key)
                if not cached:
                    continue
                try:
                    p = Path(ProxyManager.long_path(str(cached)))
                    if p.is_file():
                        p.unlink()
                        removed += 1
                except OSError as exc:
                    failed += 1
                    logging.warning("Could not remove cached file %s: %s", cached, exc)
        return removed, failed

    def delete_assets(self, assets) -> list:
        """
        Delete assets from the library, keeping them restorable for a while.

        Returns the ids actually deleted. The source files are never touched,
        and the cached thumbnails stay until the deletion can no longer be
        undone, so Undo brings back the pictures as well (MED-034).
        """
        ids = []
        for asset in assets or []:
            asset_id = asset.get('id') if isinstance(asset, dict) else asset
            if asset_id is not None and str(asset_id).isdigit():
                ids.append(int(asset_id))
            elif isinstance(asset, dict) and (asset.get('file_path') or asset.get('path')):
                row = self.db_manager.execute_query(
                    "SELECT id FROM stock_library WHERE file_path=%s",
                    (str(Path(str(asset.get('file_path') or asset.get('path')))),), fetch="one")
                if row:
                    ids.append(int(dict(row)["id"]))
        if not ids:
            return []
        hidden = self.repo.soft_delete(ids, by=self.username)
        if not hidden:
            return []
        gone = {int(r["id"]) for r in (self.db_manager.execute_query(
            "SELECT id FROM stock_library WHERE deleted_at IS NOT NULL AND id IN (%s)"
            % ",".join(["%s"] * len(ids)), tuple(ids)) or [])}
        self.assets = [a for a in self.assets if not (str(a.get('id')).isdigit()
                                                      and int(a.get('id')) in gone)]
        return sorted(gone)

    def delete_asset(self, asset: dict) -> bool:
        """Delete one asset (restorable). True when it was deleted."""
        return bool(self.delete_assets([asset]))

    def restore_assets(self, asset_ids) -> int:
        """Undo a deletion. Returns how many came back."""
        return self.repo.restore(asset_ids)

    def purge_deleted(self, older_than=None, asset_ids=None) -> int:
        """Remove deleted assets for good, with their cached files."""
        if older_than is None and asset_ids is None:
            older_than = datetime.now() - UNDO_WINDOW
        rows = self.repo.purge_deleted(older_than=older_than, asset_ids=asset_ids)
        self.remove_cached_files(rows)
        return len(rows)

    def remove_asset(self, asset_path) -> bool:
        """Remove an asset from the library by path, at once and for good."""
        path = str(asset_path)
        try:
            removed = self.repo.remove_stock_asset_by_path(str(Path(path)))
        except Exception as exc:
            logging.exception("Could not remove %s from the library: %s", path, exc)
            return False
        self.assets = [a for a in self.assets
                       if a.get('path') != path and a.get('file_path') != path]
        return removed

    def clear_all_assets(self):
        """
        Empty the whole library, and remove the thumbnails and proxies it made.

        The cached files used to stay behind on the shared cache folder,
        orphaned, although the confirmation said they were cleared (MED-015).
        Only the files the rows point at are removed: the cache folder itself
        is shared with other parts of Slate.

        Returns (ok, files_removed, files_failed).
        """
        try:
            rows = self.repo.cached_files()
            ok = self.repo.clear_stock_library()
            self.assets = []
            if not ok:
                return False, 0, 0
            removed, failed = self.remove_cached_files(rows)
            logging.info("Cleared the stock library: %d cached files removed, %d not.",
                         removed, failed)
            return True, removed, failed
        except Exception as e:
            logging.exception(f"Failed to clear library: {e}")
            return False, 0, 0

    def clear_database(self):
        """Legacy name for clear_all_assets()."""
        return self.clear_all_assets()[0]
