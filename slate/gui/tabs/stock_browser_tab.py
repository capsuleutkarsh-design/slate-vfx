"""
The Stock Viewer: the studio's stock library.

Three panels - categories and the ingest on the left, the gallery in the
middle, the preview and facts on the right - over a library that is filtered,
sorted and counted in the database. This module wires them together; the
pieces live in stock_browser/.
"""

import os
from pathlib import Path

from PySide6.QtWidgets import QWidget, QHBoxLayout, QSplitter
from PySide6.QtCore import Qt, Signal, QTimer

from ...core.domain.asset_api import create_asset_api
from ...core.infra.design_tokens import ColorTokens as C
from ...core.infra.stock_repository import REMOVED
from ..stock_model import StockModel, asset_path, can_preview, preview_source
from .stock_browser.widgets import AssetSortFilterProxyModel
from ..components.qt_safety import safe_single_shot

from .stock_browser.ui.inspector import StockInspectorPanel
from .stock_browser.ui.sidebar import StockSidebar
from .stock_browser.ui.gallery import StockGallery

from .stock_browser.controllers.ingest_controller import StockIngestController, summary_sentence
from .stock_browser.controllers.library_action_mixin import LibraryActionMixin
from .stock_browser.controllers.pagination_loader_mixin import PaginationLoaderMixin
from .stock_browser.controllers.metadata_analysis_mixin import MetadataAnalysisMixin


class StockBrowserTab(
    QWidget,
    LibraryActionMixin,
    PaginationLoaderMixin,
    MetadataAnalysisMixin
):
    """
    Controller for the Stock Browser.
    Orchestrates:
    - Sidebar (Navigation/Ingest)
    - Gallery (Grid View/Filters)
    - Inspector (Preview/Metadata)
    - Background Workers (Ingest, Analysis)
    """
    # Results from the thread pool, delivered on the interface thread.
    _analysis_done = Signal(str, str, str)      # asset_id, meta_json, path
    _thumbnail_made = Signal(str, str)          # path, thumb
    _missing_checked = Signal(list, list)       # paths checked, paths missing

    def __init__(self, library_manager, user_roles=None, user_role=None, user_data=None):
        super().__init__()
        self.lib_manager = create_asset_api(library_manager=library_manager)
        self.can_ingest = self._resolve_ingest_permission(user_roles, user_role)
        self.username = self._resolve_username(user_data)
        setter = getattr(self.lib_manager, "set_user", None)
        if callable(setter) and self.username:
            try:
                setter(self.username)
            except Exception:
                pass

        self.model = StockModel()
        self.proxy_model = AssetSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)

        self.current_category = "All"
        self.loader_thread = None
        self._is_closing = False
        self._first_load_done = False
        self._user_sized = False
        self._missing_paths = set()
        self.db_total = 0
        self.offset = 0
        self.limit = 300
        self.has_more = False
        self.is_loading = False

        self.ingest_controller = StockIngestController(self, self.model, self.proxy_model, self.lib_manager)
        self.ingest_controller.username = self.username

        self._analysis_done.connect(self._on_analysis_result)
        self._thumbnail_made.connect(self._on_thumbnail_made)
        self._missing_checked.connect(self._on_missing_checked)

        self.setup_ui()
        self.setup_connections()

    # ------------------------------------------------------------- set-up
    @staticmethod
    def _resolve_username(user_data):
        data = dict(user_data or {})
        name = data.get("user_id") or data.get("username")
        if name:
            return str(name)
        try:
            from ...core.infra.app_context import AppContext
            return AppContext().current_username()
        except Exception:
            return ""

    def setup_ui(self):
        self.setObjectName("StockBrowserRoot")
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("StockMainSplitter")
        self.splitter.setHandleWidth(1)

        self.sidebar = StockSidebar(can_ingest=self.can_ingest)
        self.sidebar.setObjectName("StockSidebarPanel")
        self.gallery = StockGallery(self.model, self.proxy_model, can_manage_assets=self.can_ingest)
        self.gallery.setObjectName("StockGalleryPanel")
        self.inspector = StockInspectorPanel(can_manage=self.can_ingest)
        self.inspector.setObjectName("StockInspectorPanel")
        self.sidebar.setMinimumWidth(200)
        self.gallery.setMinimumWidth(380)
        self.inspector.setMinimumWidth(280)

        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.gallery)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([240, 800, 360])
        self._sidebar_expanded_width = 240
        self._inspector_expanded_width = 360
        self.splitter.setCollapsible(0, True)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, True)
        main_layout.addWidget(self.splitter)

        for panel in (self.sidebar, self.gallery, self.inspector):
            panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"""
            QWidget#StockBrowserRoot {{ background-color: {C.BG_PRIMARY}; }}
            QSplitter#StockMainSplitter::handle {{ background-color: {C.BORDER_DEFAULT}; }}
            QWidget#StockSidebarPanel {{ background-color: {C.BG_SIDEBAR}; }}
            QWidget#StockGalleryPanel {{ background-color: {C.BG_PRIMARY}; }}
            QWidget#StockInspectorPanel {{ background-color: {C.BG_PRIMARY}; }}
            """
        )
        self._apply_responsive_layout()

    def setup_connections(self):
        sb, g, ins, ic = self.sidebar, self.gallery, self.inspector, self.ingest_controller
        sb.category_selected.connect(self.on_category_changed)
        sb.ingest_requested.connect(self.start_ingest)
        sb.rescan_requested.connect(self.rescan_library)
        sb.refresh_requested.connect(self.load_library_from_server)
        sb.delete_selected_requested.connect(self.delete_selected_assets)
        sb.clear_library_requested.connect(self.clear_entire_library)
        sb.import_library_requested.connect(self.import_library_file)
        sb.export_library_requested.connect(self.export_library)
        sb.pause_requested.connect(self.toggle_ingest_pause)
        sb.stop_requested.connect(self.stop_ingest)
        sb.sidebar_toggle_requested.connect(self.toggle_sidebar)

        g.filter_changed.connect(self.apply_filters)
        g.selection_changed.connect(self.on_selection_changed)
        g.asset_double_clicked.connect(self.on_double_click)
        g.folders_dropped.connect(self.on_folders_dropped)
        g.files_dropped.connect(self._on_files_dropped)
        g.scroll_bottom_reached.connect(self.load_more_assets)
        g.delete_requested.connect(self.delete_selected_assets)
        g.restore_requested.connect(self.restore_selected_assets)
        g.sidebar_expand_requested.connect(self.toggle_sidebar)
        g.preview_requested.connect(self.open_quick_look)
        g.play_requested.connect(self.play_current)
        g.player_key.connect(self.inspector.player.handle_key)
        g.favorite_requested.connect(lambda: self.toggle_favorite())
        g.favorite_clicked.connect(self._on_card_star)
        g.pick_requested.connect(lambda: self.toggle_pick())
        g.tags_requested.connect(lambda: self.edit_tags_of())
        g.ingest_requested.connect(lambda: self.start_ingest(self._fast_mode()))
        g.clear_filters_requested.connect(self.clear_all_filters)

        ins.next_requested.connect(self.select_next_asset)
        ins.prev_requested.connect(self.select_prev_asset)
        ins.analysis_requested.connect(self.trigger_background_analysis)
        ins.favorite_toggled.connect(lambda asset, on: self.toggle_favorite([asset], on))
        ins.pick_toggled.connect(lambda asset, on: self.toggle_pick([asset], on))
        ins.tags_edit_requested.connect(self.edit_tags_of)

        ic.status_updated.connect(sb.set_ingest_state)
        ic.progress_updated.connect(sb.set_ingest_progress)
        ic.ingest_started.connect(lambda: sb.set_ingest_running(True))
        # The category counts follow a running ingest, every few seconds, so
        # what is in already can be browsed (MED2-038).
        self._category_refresh = QTimer(self)
        self._category_refresh.setSingleShot(True)
        self._category_refresh.setInterval(3000)
        self._category_refresh.timeout.connect(self._refresh_categories)
        ic.assets_ready.connect(lambda *_: self._category_refresh.isActive()
                                or self._category_refresh.start())
        ic.ingest_finished.connect(self._on_ingest_finished)
        ic.ingest_summary.connect(self._on_ingest_summary)
        ic.notice.connect(lambda message, level: self._notify(message, level))

        self.model.thumbnail_needed.connect(self.make_missing_thumbnail)
        self.proxy_model.layoutChanged.connect(self.update_ui_counts)
        self.proxy_model.rowsInserted.connect(lambda p, f, l: self.update_ui_counts())
        self.proxy_model.rowsRemoved.connect(lambda p, f, l: self.update_ui_counts())
        self.proxy_model.modelReset.connect(self.update_ui_counts)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

    # ------------------------------------------------------------ feedback
    def _notify(self, message: str, level: str = "info", details: str = "", action=None):
        """The shared toast: the action as a button (Undo, Open folder), details behind one."""
        from ..components.feedback import toast
        toast(self, message, level, action=action, details=details)

    def _show_load_error(self, error):
        from ...core.infra.db_results import DatabaseUnavailableError
        unreachable = isinstance(error, DatabaseUnavailableError) or "reach" in str(error).lower()
        if unreachable:
            title = "Can't reach the studio database"
            body = "Your work is safe - the library fills in when the connection is back."
        else:
            title = "Could not load the stock library"
            body = "Something went wrong while reading it. Try again - if it keeps happening, tell IT."
        self.gallery.show_error(title, body, retry=self.load_library_from_server,
                                details="" if unreachable else str(error))

    def _clear_load_error(self):
        self.gallery.hide_error()

    # ------------------------------------------------------------ showing
    def showEvent(self, event):
        super().showEvent(event)
        if not self._first_load_done:
            # Straight away, not half a second later (MED-078).
            self._first_load_done = True
            self.load_library_from_server()
            if self.can_ingest:
                self.purge_old_deletions()
        safe_single_shot(0, self, self._apply_responsive_layout)

    def toggle_sidebar(self):
        """Collapse/expand the filter sidebar for high-density browsing."""
        sizes = self.splitter.sizes()
        if not sizes or len(sizes) < 3:
            return
        current_sidebar = int(sizes[0])
        if current_sidebar <= 20:
            target = max(200, int(getattr(self, "_sidebar_expanded_width", 240)))
            self.splitter.setSizes([target, max(380, sizes[1] - target), sizes[2]])
            collapsed = False
        else:
            self._sidebar_expanded_width = max(200, current_sidebar)
            self.splitter.setSizes([0, sizes[1] + current_sidebar, sizes[2]])
            collapsed = True
        self._user_sized = True
        self.sidebar.set_collapsed_visual(collapsed)
        self.gallery.set_sidebar_collapsed(collapsed)
        self._apply_responsive_layout()

    def _on_splitter_moved(self, pos, index):
        del pos, index
        self._user_sized = True
        self._apply_responsive_layout()

    def proportional_sizes(self, width: int):
        """
        Side panels in proportion to the window until someone drags them
        (MED-061): at 1280 the gallery used to get two columns while the
        side panels kept their full width. Below 1400 px the sidebar folds
        away; the Filters button brings it back.
        """
        width = max(1, int(width))
        # Laptops down to a 1280 window (the tab is ~100 px narrower) keep their
        # categories, Favourites and Studio picks in view (MED2-035); only
        # narrower ones fold the sidebar away.
        if width < 1150:
            sidebar = 0
        else:
            # 230 at least: Export and Import side by side keep their words.
            sidebar = int(min(280, max(230, width * 0.16)))
        inspector = int(min(420, max(280, width * 0.24)))
        gallery = max(380, width - sidebar - inspector)
        return [sidebar, gallery, inspector]

    def _apply_responsive_layout(self):
        """Sync compact modes and restore affordances with live pane sizes."""
        if not self._user_sized and self.width() > 200:
            self.splitter.setSizes(self.proportional_sizes(self.width()))
        sizes = self.splitter.sizes()
        if len(sizes) != 3:
            return
        sidebar_w, _, inspector_w = sizes
        collapsed = sidebar_w <= 20
        self.gallery.set_sidebar_collapsed(collapsed)
        self.sidebar.set_collapsed_visual(collapsed)
        if not collapsed:
            self._sidebar_expanded_width = max(200, sidebar_w)
        if inspector_w > 40:
            self._inspector_expanded_width = max(280, inspector_w)
        self.sidebar.set_compact_mode(sidebar_w < 230)
        self.inspector.set_compact_mode(inspector_w < 320)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    # ------------------------------------------------------------ filters
    def on_category_changed(self, category):
        self.current_category = category or "All"
        self.gallery.set_category(self.current_category)
        self.apply_filters()

    def clear_all_filters(self):
        """The no-results page's button: search, visual, type and category back to All."""
        self.gallery.clear_filters()
        self.current_category = "All"
        self.sidebar.current_category = "All"
        self.sidebar._select_current()
        self.gallery.set_category("All")
        self.apply_filters()

    def update_ui_counts(self):
        """
        '350 assets' when nothing narrows the list - the rest of a long list
        loads as you scroll, it is not hidden (MED2-009) - and 'Showing 40 of
        350 assets' when a search, filter or category does.
        """
        visible = self.proxy_model.rowCount()
        loaded = self.model.rowCount()
        # What matches: rows hidden on screen since the last load come off it.
        matches = max(visible, int(getattr(self, "db_total", 0) or 0) - (loaded - visible))
        category = getattr(self, "current_category", "All") or "All"
        narrowed = self.gallery.filters_active() or category not in ("All", REMOVED)
        library = int(getattr(self.sidebar, "library_total", 0) or 0)
        self.gallery.update_count(max(library, matches) if narrowed else matches, matches)

    # ------------------------------------------------------------ selection
    def current_asset(self):
        index = self.gallery.current_index()
        if not index.isValid():
            return None
        return index.data(Qt.ItemDataRole.UserRole)

    def on_selection_changed(self, selected=None, deselected=None):
        """
        The inspector follows the current item (not an arbitrary one of the
        selected, MED-074), and clears when nothing is selected (MED-019).
        Selecting shows the first frame; it does not play (MED-073).
        """
        count = len(self.gallery.selected_rows())
        self.sidebar.set_selection_count(count)
        asset = self.current_asset()
        if not asset:
            self.inspector.clear()
            return
        if self.inspector.current_asset is not None and \
                asset_path(self.inspector.current_asset) == asset_path(asset):
            return
        self.inspector.update_asset(asset)

    def on_double_click(self, index):
        """Preview and play; a file with no preview gets a message, never a launched program (MED-060)."""
        asset = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if not asset:
            return
        self.inspector.update_asset(asset, autoplay=can_preview(asset))

    def play_current(self):
        asset = self.current_asset()
        if not asset:
            return
        if self.inspector.current_asset is None or \
                asset_path(self.inspector.current_asset) != asset_path(asset):
            self.inspector.update_asset(asset, autoplay=True)
        else:
            self.inspector.toggle_play()

    def open_quick_look(self):
        """Space: the current asset large, with Previous and Next through the list (MED-056)."""
        asset = self.current_asset()
        if not asset or not can_preview(asset):
            return
        if asset.get('_missing'):
            self._notify("That file is not on disk any more - the source may have moved.", "warning")
            return
        from ..widgets.quick_look import QuickLookDialog
        self.inspector.player.stop_media()
        target, options = preview_source(asset)
        dialog = QuickLookDialog(self, asset_name=asset.get("name") or "Preview",
                                 asset_path=target, load_options=options,
                                 navigator=self._quick_look_step)
        self._quick_look = dialog
        dialog.exec()
        self._quick_look = None
        # The inspector picks the asset that is current now.
        self.inspector.current_asset = None
        self.on_selection_changed()

    def _quick_look_step(self, step):
        """Move the selection and hand Quick Look what to show next (name, path, options) or None."""
        row = self.gallery.current_index().row()
        count = self.proxy_model.rowCount()
        for candidate in range(row + step, count if step > 0 else -1, step):
            asset = self.proxy_model.index(candidate, 0).data(Qt.ItemDataRole.UserRole)
            if asset and can_preview(asset) and not asset.get('_missing'):
                self.gallery.select_row(candidate)
                return (asset.get("name") or "",) + preview_source(asset)
        return None

    def _on_card_star(self, proxy_index):
        asset = proxy_index.data(Qt.ItemDataRole.UserRole) if proxy_index.isValid() else None
        if asset:
            self.toggle_favorite([asset], not asset.get("is_favorite"))

    def select_next_asset(self):
        self._navigate(1)

    def select_prev_asset(self):
        self._navigate(-1)

    def _navigate(self, step):
        count = self.proxy_model.rowCount()
        if count == 0:
            return
        index = self.gallery.current_index()
        new_row = index.row() + step if index.isValid() else 0
        if 0 <= new_row < count:
            self.gallery.select_row(new_row)

    # ------------------------------------------------------------ ingest
    def _resolve_ingest_permission(self, user_roles=None, user_role=None):
        """
        Whether this person may ingest into, and delete from, the library.

        Answered by access.json like every other permission. This used to be
        a literal role set here plus a fallback that made anybody on a machine
        named CAPINT a developer - neither of which a studio could see or
        change without editing code.
        """
        from slate.core.domain.access import can

        roles = []
        if isinstance(user_roles, list):
            roles.extend(r for r in user_roles if r is not None)
        elif isinstance(user_roles, str):
            roles.append(user_roles)
        if user_role:
            roles.append(user_role)
        return can(roles, "ingest_stock")

    def _fast_mode(self) -> bool:
        toggle = getattr(self.sidebar, "toggle_fast", None)
        return bool(toggle.isChecked()) if toggle is not None else False

    def start_ingest(self, fast_mode=False, folders=None):
        if not self.can_ingest:
            self._notify("Only leads, supervisors and admins can ingest stock.", "warning")
            return False
        return self.ingest_controller.start_ingest(folders, fast_mode=fast_mode)

    def rescan_library(self, fast_mode=False):
        if not self.can_ingest:
            return False
        return self.ingest_controller.rescan(fast_mode=fast_mode)

    def on_folders_dropped(self, folders):
        """Every dropped folder goes through the same start as the button (MED-022)."""
        if not self.can_ingest:
            self._notify("Only leads, supervisors and admins can ingest stock.", "warning")
            return False
        return self.ingest_controller.on_folders_dropped(folders, fast_mode=self._fast_mode())

    def _on_files_dropped(self, files):
        if self.can_ingest:
            self._notify("Drop folders to ingest them; single files are not taken on their own.",
                         "info")

    def _on_ingest_finished(self):
        if self._is_closing:
            return
        self.sidebar.set_ingest_running(False)
        self.sidebar.set_selection_count(len(self.gallery.selected_rows()))
        self._refresh_categories()
        # The new assets are in; reading the first page again puts them in
        # the order and filter being shown, with the right total.
        self.load_library_from_server()

    def _on_ingest_summary(self, summary):
        message, level = summary_sentence(summary)
        self.sidebar.set_ingest_state(message, True)
        self.sidebar.set_ingest_progress(100, "")
        parts = []
        if summary.get("failed_names"):
            parts.append("Could not be read:\n" + "\n".join(summary["failed_names"][:200]))
        if summary.get("not_taken"):
            parts.append("Left out, not a picture or movie:\n"
                         + "\n".join(summary["not_taken"][:200]))
        # The sidebar line and this toast; not a third copy in the status bar
        # (MED2-033).
        from ..components.feedback import raw_toast
        raw_toast(self, message, level, details="\n\n".join(parts))

    def toggle_ingest_pause(self):
        is_paused = self.ingest_controller.toggle_pause()
        self.sidebar.set_pause_btn_text("Resume" if is_paused else "Pause")
        self.sidebar.set_ingest_state("Paused" if is_paused else "Resuming…", True)

    def stop_ingest(self):
        if self.ingest_controller.stop_ingest():
            self.sidebar.set_ingest_state("Stopping after the files in hand…", True)

    # -------------------------------------------------- unsaved work / busy
    def busy_reason(self):
        reason = self.ingest_controller.busy_reason()
        if reason:
            return reason
        worker = getattr(self, "_import_worker", None)
        if worker is not None and worker.isRunning():
            return "The Stock Viewer is still importing a library export."
        return None

    def shutdown(self, timeout_ms=15000) -> bool:
        """Stop the ingest at a safe point (between files)."""
        self.ingest_controller.stop_ingest()
        worker = self.ingest_controller.worker
        if worker is not None and worker.isRunning():
            worker.wait(int(timeout_ms))
            return not worker.isRunning()
        return True

    # ------------------------------------------------------------ teardown
    def cleanup_resources(self):
        """Gracefully stop all background workers."""
        self._is_closing = True
        self.inspector.cleanup()
        self.ingest_controller.cleanup()
        self._cancel_loader_thread(timeout_ms=3000)
        pool = getattr(self, "_stock_pool", None)
        if pool is not None:
            pool.clear()
            pool.waitForDone(3000)
        self.model.cleanup()

    def closeEvent(self, event):
        self._is_closing = True
        self.cleanup_resources()
        super().closeEvent(event)
