"""
Background work for what is on screen: reading an asset's facts when it is
selected, making a missing thumbnail, and checking that source files exist.

All of it runs on the thread pool and comes back through the tab's signals,
so nothing touches the interface from another thread.

  * The analysis result is saved by file path, and the inspector refreshes
    when the asset is still shown (MED-020).
  * A card with no thumbnail on record gets one made once, and saved (MED-030).
  * Missing source files are found in one pass per page and marked (MED-059).
"""

import json
import logging
import os

from PySide6.QtCore import QRunnable, QThreadPool


class _Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.setAutoDelete(True)

    def run(self):
        try:
            self.fn()
        except Exception as exc:
            logging.exception("Stock background job failed: %s", exc)


class MetadataAnalysisMixin:
    """
    Mixin for StockBrowserTab handling background metadata extraction.
    """

    def _pool(self):
        pool = getattr(self, "_stock_pool", None)
        if pool is None:
            pool = QThreadPool(self)
            pool.setMaxThreadCount(2)
            self._stock_pool = pool
        return pool

    def trigger_background_analysis(self, path, asset):
        """Read an asset's facts in the background, save them, show them."""
        asset_id = str(asset.get('id', ''))
        lib = self.lib_manager

        def job():
            from .....core.domain.metadata_engine import SmartMetadataManager
            meta = SmartMetadataManager.extract_tech_metadata(path)
            if not meta:
                return
            saved = lib.update_asset(asset_id, {"file_path": path, "metadata": meta,
                                                "category": asset.get("category")})
            if not saved:
                logging.info("Analysis of %s was not stored (the row was not found).", path)
            if not getattr(self, "_is_closing", False):
                self._analysis_done.emit(asset_id, json.dumps(meta), path)

        self._pool().start(_Job(job))

    def _on_analysis_result(self, asset_id, meta_json, path):
        """On the interface thread: update the card and, if it is shown, the inspector."""
        try:
            meta = json.loads(meta_json)
        except ValueError:
            meta = {}
        update = {'id': asset_id, 'file_path': path, 'path': path, 'metadata': meta,
                  'status': 'ready'}
        self.model.update_item(update)
        self.inspector.refresh_facts(update)

    def make_missing_thumbnail(self, asset):
        """A card without a thumbnail asked for one: make it and save it, once."""
        path = asset.get("file_path") or asset.get("path")
        if not path:
            return
        lib = self.lib_manager

        def job():
            from .....core.domain.proxy_manager import proxy_manager
            from pathlib import Path
            if not os.path.exists(path):
                return
            ok, thumb = proxy_manager.generate_thumbnail(Path(path))
            if ok and thumb:
                lib.update_asset(asset.get("id"), {"file_path": path, "thumb_path": str(thumb)})
                if not getattr(self, "_is_closing", False):
                    self._thumbnail_made.emit(path, str(thumb))

        self._pool().start(_Job(job))

    def _on_thumbnail_made(self, path, thumb):
        self.model.update_item({'file_path': path, 'path': path, 'thumb_path': thumb})

    def check_missing_files(self, assets):
        """Which of these source files are not on disk - one background pass."""
        paths = [a.get("file_path") or a.get("path") for a in assets or []]
        paths = [p for p in paths if p]
        if not paths:
            return

        def job():
            missing = [p for p in paths if not os.path.exists(p)]
            if not getattr(self, "_is_closing", False):
                self._missing_checked.emit(paths, missing)

        self._pool().start(_Job(job))

    def _on_missing_checked(self, checked, missing):
        known = set(getattr(self, "_missing_paths", set()))
        known.difference_update(checked)
        known.update(missing)
        self._missing_paths = known
        self.model.set_missing(known)

    def purge_old_deletions(self):
        """Deleted assets past their undo time are removed for good, with their cache."""
        lib = self.lib_manager

        def job():
            purged = lib.purge_deleted()
            if purged:
                logging.info("Stock library: %d deleted assets purged.", purged)

        self._pool().start(_Job(job))
