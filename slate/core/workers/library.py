import logging

from PySide6.QtCore import QThread, Signal


class StockLoaderWorker(QThread):
    """
    One page of the stock library, read off the interface thread.

    With the page come the facts the screen shows beside it, read in the same
    trip so they agree with it: how many assets match (MED-006), and - for a
    fresh list - the categories with their counts, the person's favourites
    and the studio picks (MED-012).
    """
    finished_signal = Signal(list)
    # {"assets", "total", "categories", "favorites", "picks", "error"}
    loaded = Signal(dict)

    def __init__(self, lib_manager, query=None, limit=None, offset=0, file_types=None, asset_ids=None,
                 sort=None, category=None, visual=None, with_facets=False):
        super().__init__()
        self.lib_manager = lib_manager
        self.query = query
        self.limit = limit
        self.offset = offset
        self.file_types = file_types
        self.asset_ids = asset_ids
        self.sort = sort
        self.category = category
        self.visual = visual
        self.with_facets = with_facets

    def run(self):
        result = {"assets": [], "total": None, "categories": None, "favorites": None,
                  "picks": None, "removed": None, "roots": None, "error": ""}
        try:
            assets = self.lib_manager.search_library(
                query=self.query, limit=self.limit, offset=self.offset,
                file_types=self.file_types, asset_ids=self.asset_ids,
                sort=self.sort, category=self.category, visual=self.visual,
            )
            result["assets"] = assets
            result["total"] = self.lib_manager.get_total_count(
                query=self.query, file_types=self.file_types, asset_ids=self.asset_ids,
                category=self.category, visual=self.visual)
            if self.with_facets:
                result["categories"] = self.lib_manager.get_category_counts()
                result["favorites"] = self.lib_manager.get_favorite_count()
                result["picks"] = self.lib_manager.get_pick_count()
                # For the people who manage the library: what Removed holds,
                # and whether Rescan has folders to look in (MED2-028, MED2-020).
                lib = self.lib_manager
                result["removed"] = getattr(lib, "get_removed_count", lambda: 0)()
                result["roots"] = len(getattr(lib, "ingest_roots", list)() or [])
        except Exception as exc:
            # A failed read is said as such, never shown as an empty library.
            logging.exception("The stock library could not be read: %s", exc)
            result["error"] = str(exc) or type(exc).__name__
        self.loaded.emit(result)
        self.finished_signal.emit(result["assets"])

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
