import logging


from .....utils.media_capabilities import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from .....core.workers.library import StockLoaderWorker
from ....components.qt_safety import safe_single_shot

class PaginationLoaderMixin:
    """
    Mixin for StockBrowserTab handling lazy loading, filtering, and threading.
    """

    def _cancel_loader_thread(self, timeout_ms=3000):
        worker = getattr(self, "loader_thread", None)
        if not worker:
            return

        if worker.isRunning():
            worker.requestInterruption()
            worker.wait(timeout_ms)

        if not worker.isRunning():
            worker.deleteLater()
        else:
            logging.warning("StockBrowserTab: loader_thread did not stop in time. Abandoning.")
            try:
                worker.finished_signal.disconnect()
            except Exception:
                pass
            worker.finished.connect(worker.deleteLater)

        if getattr(self, "loader_thread", None) is worker:
            self.loader_thread = None

    def _start_loader_thread(self, worker, append=False):
        self.loader_thread = worker
        setattr(worker, "_append_mode", bool(append))
        worker.finished_signal.connect(self._on_loader_finished)
        worker.start()

    def _on_loader_finished(self, assets):
        worker = self.sender()
        if worker is not getattr(self, "loader_thread", None):
            return

        append = bool(getattr(worker, "_append_mode", False))
        self.loader_thread = None
        worker.deleteLater()
        self.on_library_loaded(assets, append=append)

    def show_specific_assets(self, asset_ids):
        self.offset = 0
        self.limit = len(asset_ids) + 1  # Exact
        self.has_more = False
        
        self._cancel_loader_thread()
            
        self.gallery.progress_bar.setVisible(True)
        
        worker = StockLoaderWorker(self.lib_manager, asset_ids=asset_ids)
        self._start_loader_thread(worker, append=False)

    def load_library_from_server(self):
        """Initial load / refresh"""
        if getattr(self, "_is_closing", False):
            return
        self.offset = 0
        self.limit = 500
        self.has_more = True
        self.db_total = 0
        self.is_loading = False
        
        self._cancel_loader_thread()

        self.gallery.progress_bar.setVisible(True)
        self.gallery.progress_bar.setRange(0, 0) # Indeterminate
        self.gallery.set_loading_state(True)
        
        self.sidebar.set_controls_enabled(False)
        self.fetch_assets(append=False)

    def load_more_assets(self):
        """Called on scroll bottom"""
        if getattr(self, "is_loading", False) or not getattr(self, "has_more", False): 
            return
        self.fetch_assets(append=True)

    def _auto_fill_check(self):
        """Auto-load more if the viewport is not full yet."""
        if getattr(self, "_is_closing", False):
            return
        sb = self.gallery.asset_view.verticalScrollBar()
        if sb.maximum() == 0 and getattr(self, "has_more", False) and not getattr(self, "is_loading", False):
            self.fetch_assets(append=True)

    def fetch_assets(self, append=False):
        self.is_loading = True
        self.gallery.progress_bar.setVisible(True)
        if not append:
            self.gallery.set_loading_state(True)
        self._cancel_loader_thread()
        
        filters = self.gallery.get_filter_state()
        query = filters.get('search')
        media_type = filters.get('media_type')
        
        file_types = None
        if media_type == "Images":
            file_types = sorted(IMAGE_EXTENSIONS)
        elif media_type == "Videos":
             file_types = sorted(VIDEO_EXTENSIONS)

        # Remember what is being asked for. The count and the text filter below
        # both read these back; until now nothing ever set them, so the count
        # quietly reported the whole library while a search narrowed the list,
        # and the typed search was wiped from the view on every page.
        self.current_search = query if query else None
        self.current_file_types = file_types
        
        worker = StockLoaderWorker(
            self.lib_manager, 
            query=query if query else None, 
            limit=self.limit, 
            offset=self.offset,
            file_types=file_types
        )
        self._start_loader_thread(worker, append=append)

    def on_library_loaded(self, new_assets, append=False):
        self.is_loading = False
        self.sidebar.set_controls_enabled(True)
        self.gallery.progress_bar.setVisible(False)
        self.gallery.set_loading_state(False)
        
        if not new_assets:
            self.has_more = False
            if not append:
                self.model.clear_assets()
                self.update_ui_counts()
            return

        if len(new_assets) < self.limit:
            self.has_more = False
        else:
            self.has_more = True
            
        self.offset += len(new_assets)

        if append:
            # Capture scrollbar value to prevent layout layoutChanged jump
            sb = self.gallery.asset_view.verticalScrollBar()
            current_val = sb.value()
            self.model.add_assets(new_assets)
            # Safe restore AFTER the event loop processes the proxy model insertion
            safe_single_shot(0, self, lambda val=current_val: sb.setValue(val))
        else:
            self.model.load_data(new_assets)
            
        try:
            # Count what is actually being shown. The plain total was used even
            # while a search narrowed the list, so the number on screen
            # disagreed with it and paging through results misbehaved.
            self.db_total = self.lib_manager.get_total_count(
                query=getattr(self, "current_search", None) or None,
                file_types=getattr(self, "current_file_types", None) or None,
            )
        except Exception:
            self.db_total = self.model.rowCount()

        # Only when the list is rebuilt. Appending the next page of an endless
        # scroll used to recalculate the whole sidebar from the database again,
        # once per page.
        if not append:
            self.sidebar.update_categories(self.lib_manager.get_categories())

        self.update_ui_counts()
        self.apply_post_load_filters()

        if self.has_more:
            safe_single_shot(100, self, self._auto_fill_check)

    def apply_filters(self):
        """Triggered by UI filter changes. Resets search."""
        self.offset = 0
        self.has_more = True
        self._cancel_loader_thread()
        self.fetch_assets(append=False)

    def apply_post_load_filters(self):
        filters = self.gallery.get_filter_state()
        self.proxy_model.set_category(getattr(self, "current_category", "All"))
        
        # Keep whatever was typed. Clearing it here dropped an active search
        # as soon as the next page of results arrived.
        self.proxy_model.set_text_filter(getattr(self, "current_search", "") or "")
        self.proxy_model.set_visual_tag_filter(filters.get('visual', ''))
        self.proxy_model.set_media_type_filter("All")
        
        self.update_ui_counts()
