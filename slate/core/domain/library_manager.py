import logging
import json
import shutil
from pathlib import Path
from datetime import datetime
from ..infra.global_config import GlobalConfig
# from .database_manager import database_manager # REMOVED: Injected instead

class LibraryManager:
    """
    Manages the Central Asset Library.
    Refactored to wrap DatabaseManager (PostgreSQL) for Single Source of Truth.
    Maintains legacy API for compatibility with GUI.
    """
    def __init__(self, db_manager=None):
        if db_manager is None:
            try:
                from ..infra.database_manager import database_manager
                db_manager = database_manager
            except Exception:
                db_manager = None
        self.db_manager = db_manager
        self.server_root = GlobalConfig.server_root()
        # self.library_file = ... # Deprecated
        
        # Local cache (can keep for now or remove)
        self.local_cache = GlobalConfig.local_cache_dir() / "Library_Cache.caplib"
        
        self.assets = []
        # No longer need direct folder checks for .caplib
        
    def _ensure_directories(self):
        pass # Database handles its own path

    def set_server_root(self, new_root_path: Path):
        """Dynamically switch the library root (Project Context)."""
        self.server_root = Path(new_root_path)
        self.assets_dir = self.server_root / "Assets"
        self.library_file = self.assets_dir / "Library.caplib"
        self._ensure_directories()
        # Clear current assets from memory to force reload
        self.assets = []
        logging.info(f"Switched Library Root to: {self.server_root}")

    def load_library(self):
        """
        Loads assets from the Database (Total Recall).
        """
        # Default loads everything (limit=None)
        self.assets = self._convert_db_assets_to_legacy_format(
            self.db_manager.get_all_stock_assets()
        )

    def get_total_count(self, query=None, file_types=None, asset_ids=None):
        """
        How many assets there are, honouring any filter.

        Called with no filter this is the whole library, as before. The browser
        passes its current search so the count on screen matches the list.
        """
        if query or file_types or asset_ids:
            counter = getattr(self.db_manager, "count_stock_assets", None)
            if callable(counter):
                try:
                    return counter(search_query=query, file_types=file_types,
                                   asset_ids=asset_ids)
                except Exception as exc:
                    logging.debug("Filtered count unavailable: %s", exc)
        return self.db_manager.get_stock_count()

    def list_known_paths(self):
        """
        The paths already in the library, for the ingest to skip what it has.

        Deliberately not get_all_assets(): that reads every column of every row,
        which on a large library is a great deal of traffic before the scan even
        begins.
        """
        lister = getattr(self.db_manager, "list_stock_paths", None)
        if callable(lister):
            try:
                return lister()
            except Exception as exc:
                logging.debug("Path listing unavailable, falling back: %s", exc)
        return [{"id": a.get("id"), "file_path": a.get("file_path") or a.get("path"),
                 "thumb_path": a.get("thumb_path")} for a in self.get_all_assets()]

    def search_library(self, query=None, limit=None, offset=0, file_types=None, asset_ids=None):
        """
        Direct passthrough to DB for scalable search.
        Returns list of assets in legacy format.
        Does NOT update full local cache self.assets to avoid memory bloat.
        """
        rows = self.db_manager.get_all_stock_assets(
            limit=limit, 
            offset=offset, 
            search_query=query, 
            file_types=file_types,
            asset_ids=asset_ids
        )
        return self._convert_db_assets_to_legacy_format(rows)

    def save_library(self):
        """
        Legacy stub. Changes are now atomic via database_manager.
        Refreshes local list.
        """
        # OPTIMIZATION: Do NOT force full reload here. 
        # UI calls this expecting valid state, but we maintain state incrementally now.
        pass

    def _update_cache(self):
        # Optional: Keep local JSON cache for offline redundancy if needed
        # For now, we rely on Database
        pass

    def _convert_db_assets_to_legacy_format(self, db_rows):
        """Converts Database rows to the list-of-dicts format expected by UI."""
        legacy_assets = []
        if not db_rows:
            return legacy_assets
            
        for row in db_rows:
            # Load metadata from DB if available
            metadata = {}
            if 'metadata' in row.keys() and row['metadata']:
                try:
                    import json
                    metadata = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']
                except Exception:
                    metadata = {}
            
            # Ensure metadata has required keys for UI
            if not metadata:
                metadata = {
                    'width': 0,
                    'height': 0,
                    'fps': 0.0,
                    'duration_sec': 0.0,
                    'codec': 'unknown'
                }
            
            # Map DB columns to legacy JSON keys
            legacy_assets.append({
                "id": str(row['id']),
                "name": row['file_name'],
                "file_name": row['file_name'],  # Add for compatibility
                "path": row['file_path'],
                "file_path": row['file_path'],  # Add for compatibility
                "category": self._infer_category_from_type(row['file_type']),
                "tags": row['tags'] if row['tags'] else [],
                "metadata": metadata,
                "thumb_path": row['thumb_path'],
                "proxy_path": row['proxy_path'],
                "added_by": "System"
            })
        return legacy_assets

    def _infer_category_from_type(self, suffix):
        # Simple helper since DB currently lacks 'category' column
        video_ext = ['.mov', '.mp4', '.avi', '.mkv']
        image_ext = ['.jpg', '.png', '.exr', '.dpx', '.tif', '.tiff']
        if suffix in video_ext: return "Stock Footage"
        if suffix in image_ext: return "Textures/Images"
        return "Uncategorized"

    def add_asset(self, name, path, category, tags, metadata=None, thumb_path=None, proxy_path=None):
        """
        Add an asset. Returns its new identifier, or 0 if it was not stored.

        This used to return nothing at all, so a caller could not tell a
        successful add from a failed one - the same silent-failure pattern
        found throughout the database layer.
        """
        # 1. Add to SQLite
        tags_list = tags.split(',') if isinstance(tags, str) else tags
        new_id = self.db_manager.add_stock_asset(path, thumb_path, proxy_path, tags_list)
        
        # 2. Update local state incrementally (Avoid Full Reload)
        # We need to construct the asset dict manually to match legacy format
        # If DB returns ID, we can use it.
        if new_id:
             new_asset = {
                "id": str(new_id),
                "name": name,
                "path": str(path),
                "category": category,
                "tags": tags_list if isinstance(tags_list, str) else ",".join(tags_list),
                "metadata": metadata,
                "thumb_path": str(thumb_path) if thumb_path else None,
                "proxy_path": str(proxy_path) if proxy_path else None,
                "added_by": "System"
             }
             self.assets.append(new_asset)
        else:
             # Fallback if DB insert fails or doesn't return ID (shouldn't happen)
             self.load_library()
        return new_id or 0

    def add_assets_batch(self, assets_list):
        # Convert legacy dicts to what DB Expects if needed, or just pass list if keys match
        # DB expects: {'file_path', 'thumb_path', 'proxy_path', 'tags'}
        
        db_ready_list = []
        for a in assets_list:
            db_ready_list.append({
                'file_path': a.get('path'),
                'thumb_path': a.get('thumb_path'),
                'proxy_path': a.get('proxy_path'),
                'tags': a.get('tags')
            })
            
        self.db_manager.add_stock_assets_batch(db_ready_list)
        
        # OPTIMIZATION: Incremental Memory Update
        # Instead of reloading 18,000 items from DB, we just confirm they are added.
        # Since ingestor already has the data, we trust it. 
        # But we need IDs. The batch insert doesn't return IDs easily in SQLite without individual queries.
        # However, for the UI, `asset_ingestor` emits `assets_batch_signal` with temporary IDs or new IDs if it can.
        # It is SAFER to NOT reload here. The UI (StockBrowser) listens for `assets_batch_signal` from the Worker to update the View.
        # LibraryManager's job is just to persist. We can update self.assets lazily or not at all until next startup/refresh.
        
        # We will append to self.assets assuming successful insert, to keep memory roughly in sync
        # BUT without IDs, it's tricky.
        # Strategy: Do NOTHING to memory here. Let the UI rely on the signal it receives from IngestWorker.
        # When user restarts or hits refresh, they get the full DB.
        pass

    def get_all_assets(self):
        """Returns list of all assets."""
        if not self.assets:
            self.load_library()
        return self.assets

    def get_categories(self):
        """Returns list of unique categories."""
        # OPTIMIZATION: Try to use direct DB query first to avoid full load
        try:
            if hasattr(self.db_manager, 'get_stock_file_types'):
                file_types = self.db_manager.get_stock_file_types()
                categories = set()
                for ft in file_types:
                    cat = self._infer_category_from_type(ft.lower())
                    if cat: categories.add(cat)
                return sorted(list(categories))
        except Exception as e:
            logging.warning(f"Failed to get categories from DB, falling back: {e}")

        # Fallback to loading everything if DB method fails or missing
        if not self.assets:
            self.load_library()
        
        categories = set()
        for asset in self.assets:
            cat = asset.get('category')
            if cat: categories.add(cat)
            
        return sorted(list(categories))

    def update_asset(self, asset_id, updated_data):
        """Updates an existing asset's metadata."""
        # Removed self.load_library() check - trust current state
        
        found = False
        asset_idx = -1
        
        # 1. Update in Memory
        for i, asset in enumerate(self.assets):
            if asset.get('id') == str(asset_id):
                # Update fields
                if isinstance(updated_data, dict):
                    self.assets[i].update(updated_data)
                    
                    # Ensure tags/serialization consistency
                    t_val = self.assets[i].get('tags')
                    if isinstance(t_val, list):
                        self.assets[i]['tags'] = ",".join([str(t) for t in t_val])
                        
                    m_val = self.assets[i].get('metadata')
                    if isinstance(m_val, dict):
                        self.assets[i]['metadata'] = json.dumps(m_val)
                
                found = True
                asset_idx = i
                break
        
        # 2. Update DB (Always attempt update even if not in local cache)
        # Using file_path ensures we find the record even if ID is mismatched or cache is stale
        thumb = updated_data.get('thumb_path')
        proxy = updated_data.get('proxy_path')
        
        if 'thumb_path' in updated_data or 'proxy_path' in updated_data:
            # Always prefer file_path for DB lookup — Python-generated IDs don't match DB SERIAL IDs
            f_path = updated_data.get('file_path') 
            if not f_path and found:
                 f_path = self.assets[asset_idx].get('file_path')
            
            # Use file_path as primary key for DB update (asset_id may be a Python hash, not a real DB id)
            self.db_manager.update_stock_asset_paths(None, thumb, proxy, file_path=f_path)

        # Handle Metadata/Tags (if specific keys present)
        if found and ('metadata' in updated_data or 'tags' in updated_data):
            # We use latest from memory which is already updated
            meta_str = self.assets[asset_idx].get('metadata')
            tags_str = self.assets[asset_idx].get('tags')
            # Safety: ensure meta_str is a JSON string, not a dict
            if isinstance(meta_str, dict):
                meta_str = json.dumps(meta_str)
            if isinstance(tags_str, list):
                tags_str = ",".join([str(t) for t in tags_str])
            # Fetch real DB id from file_path for metadata update
            f_path = updated_data.get('file_path') or (self.assets[asset_idx].get('file_path') if found else None)
            real_id = self._get_db_id_by_path(f_path) if f_path else asset_id
            self.db_manager.update_asset_metadata(real_id, meta_str, tags_str)

        # Handle Embedding
        if found and 'embedding' in updated_data:
            f_path = updated_data.get('file_path') or (self.assets[asset_idx].get('file_path') if found else None)
            real_id = self._get_db_id_by_path(f_path) if f_path else asset_id
            self.db_manager.update_asset_embedding(real_id, updated_data['embedding'])

    def _get_db_id_by_path(self, file_path):
        """Fetch the real DB-assigned id for a given file_path."""
        try:
            row = self.db_manager.execute_query(
                "SELECT id FROM stock_library WHERE file_path=%s", (str(file_path),), fetch="one"
            )
            return row['id'] if row else None
        except Exception:
            return None

    def get_all_tags(self):
        """Returns list of unique tags."""
        # OPTIMIZATION: Try to use direct DB query first to avoid full load
        try:
            if hasattr(self.db_manager, 'get_stock_tags'):
                return self.db_manager.get_stock_tags()
        except Exception as e:
            logging.warning(f"Failed to get tags from DB, falling back: {e}")

        # Fallback to loading everything if DB method fails or missing
        if not self.assets:
            self.load_library()
            
        tags = set()
        for asset in self.assets:
            t_val = asset.get('tags')
            if t_val:
                # Handle string vs list
                if isinstance(t_val, str):
                    t_clean = t_val.replace('[', '').replace(']', '').replace("'", "")
                    for t in t_clean.split(','):
                        if t.strip(): tags.add(t.strip())
                elif isinstance(t_val, list):
                    for t in t_val: tags.add(str(t))
                    
        return sorted(list(tags))

    def clear_database(self):
        """Clears all assets from the library."""
        self.assets = []
        # self.save_library() # No longer triggers logic
        self.db_manager.clear_stock_assets() # Assuming this exists
        logging.info("Library cleared.")

    def update_assets_batch(self, updates_list):
        """
        Updates multiple assets efficiently with a single save.
        """
        if not updates_list: return
        
        updates_map = {u['id']: u for u in updates_list if 'id' in u}
        
        modified = False
        for i, asset in enumerate(self.assets):
            asset_id = asset.get('id')
            if asset_id in updates_map:
                update_data = updates_map[asset_id]
                self.assets[i].update(update_data)
                
                if 'tags' in update_data:
                    t_val = self.assets[i].get('tags')
                    if isinstance(t_val, list):
                        self.assets[i]['tags'] = ",".join([str(t) for t in t_val])
                
                if 'metadata' in update_data:
                    m_val = self.assets[i].get('metadata')
                    if isinstance(m_val, dict):
                        self.assets[i]['metadata'] = json.dumps(m_val)
                
                modified = True
        
        if modified:
             pass # No DB batch update implemented yet, relying on memory until restart

    def remove_asset(self, asset_path) -> bool:
        """
        Remove an asset from the library. Returns whether it was removed.

        This used to drop the asset from the in-memory list and nothing else,
        so it reappeared the moment the library was reloaded. Nothing in the
        interface calls it - deletion goes through delete_asset, which also
        clears the cached picture - but left as it was it is a trap for whoever
        reaches for the obvious name next.
        """
        path = str(asset_path)
        removed = False
        try:
            remover = getattr(self.db_manager, "remove_stock_asset_by_path", None)
            if callable(remover):
                removed = bool(remover(path))
        except Exception as exc:
            logging.exception("Could not remove %s from the library: %s", path, exc)
            return False

        self.assets = [a for a in self.assets
                       if a.get('path') != path and a.get('file_path') != path]
        return removed

    def delete_asset(self, asset: dict) -> bool:
        """
        Remove one asset from stock library and clean generated cache files.

        Removes:
        - Database row (by id, fallback by file_path)
        - Generated proxy file (if exists)
        - Generated thumbnail file (if exists)

        Keeps:
        - Original source media file
        """
        if not isinstance(asset, dict):
            return False

        asset_id = asset.get('id')
        file_path = asset.get('file_path') or asset.get('path')
        proxy_path = asset.get('proxy_path')
        thumb_path = asset.get('thumb_path')

        for cached_path in (proxy_path, thumb_path):
            if not cached_path:
                continue
            try:
                p = Path(cached_path)
                if p.exists() and p.is_file():
                    p.unlink()
            except Exception as e:
                logging.warning(f"Failed to remove cached file {cached_path}: {e}")

        db_deleted = False
        try:
            if asset_id is not None and hasattr(self.db_manager, 'remove_stock_asset'):
                db_deleted = bool(self.db_manager.remove_stock_asset(asset_id))

            if not db_deleted and file_path:
                if hasattr(self.db_manager, 'remove_stock_asset_by_path'):
                    db_deleted = bool(self.db_manager.remove_stock_asset_by_path(file_path))
                else:
                    result = self.db_manager.execute_query(
                        "DELETE FROM stock_library WHERE file_path=%s",
                        (str(file_path),),
                        fetch="rowcount"
                    )
                    db_deleted = bool(result and result > 0)
        except Exception as e:
            logging.exception(f"Failed to delete asset from database: {e}")
            db_deleted = False

        normalized_id = str(asset_id) if asset_id is not None else None
        normalized_path = str(file_path) if file_path else None
        filtered = []
        for item in self.assets:
            item_id = str(item.get('id')) if item.get('id') is not None else None
            item_path = str(item.get('file_path') or item.get('path') or "")
            if normalized_id and item_id == normalized_id:
                continue
            if normalized_path and item_path == normalized_path:
                continue
            filtered.append(item)
        self.assets = filtered

        return db_deleted

    def trash_asset(self, asset_path):
        """Moves an asset file to a server-side Trash folder instead of deleting it."""
        # self.load_library() # Remove load
        
        # 1. Ensure Trash Dir
        trash_dir = self.server_root / "_Trash"
        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.exception(f"Could not create trash dir: {e}")
            return False

        # 2. Find Asset
        asset_to_remove = None
        for a in self.assets:
             if a['path'] == str(asset_path):
                 asset_to_remove = a
                 break
        
        if not asset_to_remove:
            logging.warning(f"Try to trash unknown asset: {asset_path}")
            return False

        # 3. Move File (Renaming with Timestamp)
        src = Path(asset_path)
        if src.exists():
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            dest = trash_dir / f"{timestamp}_{src.name}"
            try:
                shutil.move(src, dest)
                logging.info(f"Trashed file: {src} -> {dest}")
            except Exception as e:
                logging.exception(f"Failed to move file to trash: {e}")
                return False
        else:
            logging.warning(f"File to trash not found on disk: {src}")

        # 4. Remove from DB
        self.assets = [a for a in self.assets if a['path'] != str(asset_path)]
        
        # Remove from database to maintain consistency
        try:
            if hasattr(self.db_manager, 'remove_stock_asset'):
                self.db_manager.remove_stock_asset(asset_to_remove['id'])
                logging.info(f"Removed asset {asset_to_remove['id']} from database")
            else:
                logging.warning("DatabaseManager.remove_stock_asset method not available")
        except Exception as e:
            logging.exception(f"Failed to remove asset from database: {e}")
            # Continue anyway as file is already trashed
        
        return True

    def update_asset_metadata(self, asset_path, metadata, tags):
        """Update an asset's details. Returns whether the change was stored."""
        """Updates metadata and tags for an existing asset."""
        updated = False
        asset_id = None
        
        meta_str = json.dumps(metadata) if isinstance(metadata, dict) else str(metadata)
        tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
        
        # Update in-memory cache
        for asset in self.assets:
            if asset.get('path') == str(asset_path):
                asset['metadata'] = meta_str
                asset['tags'] = tags_str
                asset_id = asset.get('id')
                updated = True
                break
        
        if updated and asset_id:
            # CRITICAL FIX: Save to database to persist metadata
            try:
                self.db_manager.update_asset_metadata(asset_id, meta_str, tags_str)
                logging.info(f"Updated metadata for {asset_path}")
                return True
            except Exception as e:
                logging.exception(f"Failed to save metadata to database for {asset_path}: {e}")
                return False

        logging.warning(f"Could not find asset to update: {asset_path}")
        return False

    def toggle_favorite_status(self, asset_id):
        """Toggles the 'Favorite' tag for an asset."""
        # self.load_library() # Remove load
        for i, asset in enumerate(self.assets):
            if asset.get('id') == str(asset_id):
                tags = asset.get('tags', "")
                # Normalize tags to list
                tag_list = tags.split(',') if isinstance(tags, str) else tags
                if not isinstance(tag_list, list): tag_list = []
                tag_list = [t.strip() for t in tag_list if t.strip()]
                
                if "Favorite" in tag_list:
                    tag_list.remove("Favorite")
                    logging.info(f"Removed Favorite: {asset_id}")
                else:
                    tag_list.append("Favorite")
                    logging.info(f"Added Favorite: {asset_id}")
                
                # Update DB
                if self.db_manager.update_asset_tags(asset_id, tag_list):
                    # Update local state
                    self.assets[i]['tags'] = ",".join(tag_list)
                    return True
                return False
        return False

    def clear_all_assets(self):
        """Clear all assets from the library (database and memory)."""
        try:
            # Clear database
            self.db_manager.clear_stock_library()
            
            # Clear in-memory cache
            self.assets = []
            
            logging.info("Cleared all assets from library")
            return True
        except Exception as e:
            logging.exception(f"Failed to clear library: {e}")
            return False
