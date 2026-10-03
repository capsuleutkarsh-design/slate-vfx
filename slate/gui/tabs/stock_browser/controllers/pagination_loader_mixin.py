"""
Loading the library page by page, with everything filtered in the database.

The search, category, visual filter, media type and sort all go to the
database with the page, so the list, the paging and the total agree
(MED-004, MED-005, MED-006, MED-007). The total and the category counts are
read on the loader thread with the page. After a clear or the last delete the
total is 0, so the gallery says the library is empty rather than "no match"
(MED-016). The category list is only rebuilt when it changed, and keeps the
category being shown highlighted (MED-013).
"""

import logging

from .....utils.media_capabilities import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from .....core.workers.library import StockLoaderWorker
from ....components.qt_safety import safe_single_shot

PAGE_SIZE = 300


class PaginationLoaderMixin:
    """
    Mixin for StockBrowserTab handling lazy loading, filtering, and threading.
    """

    def _cancel_loader_thread(self, timeout_ms=3000):
        worker = getattr(self, "loader_thread", None)
        if not worker:
            return
        try:
            worker.loaded.disconnect()
        except Exception:
            pass
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait(timeout_ms)
        if not worker.isRunning():
            worker.deleteLater()
        else:
            logging.warning("StockBrowserTab: loader_thread did not stop in time. Abandoning.")
            worker.finished.connect(worker.deleteLater)
        if getattr(self, "loader_thread", None) is worker:
            self.loader_thread = None

    def _start_loader_thread(self, worker, append=False):
        self.loader_thread = worker
        setattr(worker, "_append_mode", bool(append))
        worker.loaded.connect(self._on_loader_finished)
        worker.start()

    def _on_loader_finished(self, result):
        worker = self.sender()
        if worker is not getattr(self, "loader_thread", None):
            return
        append = bool(getattr(worker, "_append_mode", False))
        self.loader_thread = None
        worker.deleteLater()
        self.on_library_loaded(result, append=append)

    def show_specific_assets(self, asset_ids):
        self.offset = 0
        self.limit = len(asset_ids) + 1
        self.has_more = False
        self._cancel_loader_thread()
        self.gallery.progress_bar.setVisible(True)
        worker = StockLoaderWorker(self.lib_manager, asset_ids=asset_ids)
        self._start_loader_thread(worker, append=False)

    def load_library_from_server(self):
        """Initial load / reload."""
        if getattr(self, "_is_closing", False):
            return
        self._first_load_done = True
        self.offset = 0
        self.limit = PAGE_SIZE
        self.has_more = True
        self.is_loading = False
        self._cancel_loader_thread()
        self.gallery.progress_bar.setVisible(True)
        self.gallery.progress_bar.setRange(0, 0)
        self.gallery.set_loading_state(True)
        self.sidebar.set_controls_enabled(False)
        self.fetch_assets(append=False)

    # F5 / Ctrl+R in the main window call one of these.
    def refresh(self):
        self.load_library_from_server()

    def load_more_assets(self):
        """Called on scroll bottom: one more page, never all of them at once."""
        if getattr(self, "is_loading", False) or not getattr(self, "has_more", False):
            return
        self.fetch_assets(append=True)

    def _auto_fill_check(self):
        """Load one more page if the first one does not fill the view."""
        if getattr(self, "_is_closing", False):
            return
        bar = self.gallery.active_view().verticalScrollBar()
        if bar.maximum() == 0 and getattr(self, "has_more", False) and not getattr(self, "is_loading", False):
            self.fetch_assets(append=True)

    def current_query(self) -> dict:
        """What is being asked of the database right now."""
        filters = self.gallery.get_filter_state()
        media_type = filters.get('media_type')
        file_types = None
        if media_type == "Images":
            file_types = sorted(IMAGE_EXTENSIONS)
        elif media_type == "Videos":
            file_types = sorted(VIDEO_EXTENSIONS)
        return {
            "query": filters.get('search') or None,
            "file_types": file_types,
            "sort": filters.get('sort') or "newest",
            "category": getattr(self, "current_category", "All") or "All",
            "visual": filters.get('visual') or None,
        }

    def fetch_assets(self, append=False):
        self.is_loading = True
        self.gallery.progress_bar.setVisible(True)
        if not append:
            self.offset = 0
            self.gallery.set_loading_state(True)
        self._cancel_loader_thread()

        ask = self.current_query()
        # Remembered for the count and for anything that reads them back.
        self.current_search = ask["query"]
        self.current_file_types = ask["file_types"]
        worker = StockLoaderWorker(
            self.lib_manager,
            query=ask["query"], limit=getattr(self, "limit", PAGE_SIZE),
            offset=getattr(self, "offset", 0) if append else 0,
            file_types=ask["file_types"], sort=ask["sort"], category=ask["category"],
            visual=ask["visual"], with_facets=not append,
        )
        self._start_loader_thread(worker, append=append)

    def on_library_loaded(self, result, append=False):
        """A page arrived (result is the loader's dict, or a plain list from older callers)."""
        if isinstance(result, list):
            result = {"assets": result, "total": None}
        new_assets = list(result.get("assets") or [])
        self.is_loading = False
        self.sidebar.set_controls_enabled(True)
        self.gallery.progress_bar.setVisible(False)

        if result.get("error"):
            self.gallery.set_loading_state(False)
            self._show_load_error(result["error"])
            return
        self._clear_load_error()

        limit = getattr(self, "limit", PAGE_SIZE)
        self.has_more = len(new_assets) >= limit
        if append:
            self.offset = getattr(self, "offset", 0) + len(new_assets)
            if new_assets:
                bar = self.gallery.active_view().verticalScrollBar()
                current_val = bar.value()
                self.model.add_assets(new_assets)
                safe_single_shot(0, self, lambda val=current_val: bar.setValue(val))
        else:
            self.offset = len(new_assets)
            self.model.load_data(new_assets)

        if result.get("total") is not None:
            self.db_total = int(result["total"])
        elif not append:
            self.db_total = len(new_assets)

        if result.get("categories") is not None:
            self._show_categories(result["categories"], result.get("favorites"),
                                  result.get("picks"), result.get("removed"))
        if result.get("roots") is not None:
            self.sidebar.set_root_count(result["roots"])

        self.gallery.set_loading_state(False)
        self.apply_post_load_filters()
        if not append:
            self._keep_inspector_in_step()
        # A fresh list drops the selection without saying so.
        self.sidebar.set_selection_count(len(self.gallery.selected_rows()))
        self.check_missing_files(new_assets)
        if self.has_more:
            safe_single_shot(100, self, self._auto_fill_check)

    def _keep_inspector_in_step(self):
        """
        After a fresh list (Reload, a search, a category): the asset the
        inspector shows is selected again when it is in the list, and the
        inspector is cleared when it is not (MED2-001). A model reset drops
        the selection without a word, so the inspector went on showing - and
        acting on - an asset that was no longer listed.
        """
        shown = self.inspector.current_asset
        if shown is None:
            return
        row = self.model._row_of(shown)
        index = (self.proxy_model.mapFromSource(self.model.index(row, 0))
                 if row is not None else None)
        if index is None or not index.isValid():
            self.inspector.clear()
            return
        self.inspector.refresh_facts(self.model.assets[row])
        self.gallery.select_row(index.row())

    def _show_categories(self, counts, favorites=None, picks=None, removed=None):
        self.sidebar.update_categories(counts, favorites, picks,
                                       current=getattr(self, "current_category", "All"),
                                       removed=removed)

    def _refresh_categories(self):
        """Counts after a change (delete, favourite, pick) without reloading the list."""
        lib = self.lib_manager
        try:
            self._show_categories(lib.get_category_counts(), lib.get_favorite_count(),
                                  lib.get_pick_count(), lib.get_removed_count())
            self.sidebar.set_root_count(len(lib.ingest_roots() or []))
        except Exception as exc:
            logging.debug("Category counts not refreshed: %s", exc)
        self.update_ui_counts()

    def apply_filters(self):
        """Triggered by UI filter changes: a fresh first page."""
        self.offset = 0
        self.has_more = True
        self._cancel_loader_thread()
        self.fetch_assets(append=False)

    def apply_post_load_filters(self):
        self.proxy_model.set_category(getattr(self, "current_category", "All"))
        self.proxy_model.set_media_type_filter(self.gallery.get_filter_state().get("media_type", "All"))
        self.update_ui_counts()
