import os

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QMessageBox, QSplitter
)
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices

# Internal Module Imports
from ...core.domain.asset_api import create_asset_api
from ...core.infra.design_tokens import ColorTokens as C
from ...utils.media_capabilities import is_image, is_video
from ..stock_model import StockModel
from .stock_browser.widgets import AssetSortFilterProxyModel
from ..components.qt_safety import safe_single_shot

# NEW UI COMPONENTS
from .stock_browser.ui.inspector import StockInspectorPanel
from .stock_browser.ui.sidebar import StockSidebar
from .stock_browser.ui.gallery import StockGallery

# CONTROLLERS & MIXINS
from .stock_browser.controllers.ingest_controller import StockIngestController
from .stock_browser.controllers.library_action_mixin import LibraryActionMixin
from .stock_browser.controllers.pagination_loader_mixin import PaginationLoaderMixin
from .stock_browser.controllers.metadata_analysis_mixin import MetadataAnalysisMixin

class StockBrowserTab(
    QWidget, 
    LibraryActionMixin, 
    PaginationLoaderMixin, 
    MetadataAnalysisMixin
):
    # Signal for thread-safe background analysis results
    _analysis_done = Signal(str, str, str)  # asset_id, meta_json, asset_name
    
    """
    Controller for the Stock Browser.
    Orchestrates:
    - Sidebar (Navigation/Ingest)
    - Gallery (Grid View/Filters)
    - Inspector (Preview/Metadata)
    - Background Workers (Ingest, Analysis)
    """
    def __init__(self, library_manager, user_roles=None, user_role=None):
        super().__init__()
        self.lib_manager = create_asset_api(library_manager=library_manager)
        self.can_ingest = self._resolve_ingest_permission(user_roles, user_role)
        
        # 1. Models
        self.model = StockModel()
        self.proxy_model = AssetSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        
        # 2. State
        self.current_category = "All"
        self.loader_thread = None
        self._is_closing = False
        
        # 3. Controllers
        self.ingest_controller = StockIngestController(self, self.model, self.proxy_model, self.lib_manager)
        
        # Thread-safe analysis result handler
        self._analysis_done.connect(self._on_analysis_result)
        
        # 4. UI Setup
        self.setup_ui()
        self.setup_connections()
        
        # 5. Initial Load
        safe_single_shot(500, self, self.load_library_from_server)

    def setup_ui(self):
        self.setObjectName("StockBrowserRoot")
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("StockMainSplitter")
        
        # --- COMPONENTS ---
        self.sidebar = StockSidebar(can_ingest=self.can_ingest)
        self.sidebar.setObjectName("StockSidebarPanel")
        self.gallery = StockGallery(
            self.model,
            self.proxy_model,
            can_manage_assets=self.can_ingest
        )
        self.gallery.setObjectName("StockGalleryPanel")
        self.inspector = StockInspectorPanel()
        self.inspector.setObjectName("StockInspectorPanel")
        self.sidebar.setMinimumWidth(220)
        self.gallery.setMinimumWidth(420)
        self.inspector.setMinimumWidth(280)
        
        # Add to Splitter
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.gallery)
        self.splitter.addWidget(self.inspector)
        
        # Initial Sizes (Sidebar, Gallery, Inspector)
        self.splitter.setStretchFactor(0, 0) # Sidebar fixed
        self.splitter.setStretchFactor(1, 1) # Gallery grows
        self.splitter.setStretchFactor(2, 0) # Inspector fixed
        self.splitter.setSizes([260, 800, 400])
        self._sidebar_expanded_width = 260
        self._inspector_expanded_width = 400
        self.splitter.setCollapsible(0, True)
        self.splitter.setCollapsible(2, True)

        main_layout.addWidget(self.splitter)

        # Let the panels actually paint their own ground and edges.
        #
        # The rules below have always been here, naming these three by object
        # name - and none of them ever drew. A QWidget subclass does not honour
        # background or border from a stylesheet unless it is told to style its
        # own background, so the three columns rendered on one flat sheet with
        # nothing dividing the filters from the grid from the player.
        for panel in (self.sidebar, self.gallery, self.inspector):
            panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setStyleSheet(
            f"""
            QWidget#StockBrowserRoot {{
                background-color: {C.BG_PRIMARY};
            }}
            QSplitter#StockMainSplitter::handle {{
                background-color: {C.BORDER_DEFAULT};
                width: 2px;
            }}
            QWidget#StockSidebarPanel {{
                border-right: 1px solid {C.BORDER_DEFAULT};
                background-color: {C.BG_SIDEBAR};
            }}
            QWidget#StockGalleryPanel {{
                border-right: 1px solid {C.BORDER_DEFAULT};
                background-color: {C.BG_PRIMARY};
            }}
            QWidget#StockInspectorPanel {{
                background-color: {C.BG_PRIMARY};
            }}
            """
        )
        self._apply_responsive_layout()

    def _notify(self, message: str, level: str = "info", details: str = ""):
        """Use host window feedback system when available."""
        host = self.window()
        if host and hasattr(host, "show_feedback"):
            try:
                host.show_feedback(message=message, level=level, duration=4500, details=details)
                return
            except Exception:
                pass
        if level == "error" and details:
            QMessageBox.critical(self, "Error", details)
        elif level == "warning":
            QMessageBox.warning(self, "Warning", message)
        else:
            QMessageBox.information(self, "Info", message)

    def setup_connections(self):
        # --- SIDEBAR SIGNALS ---
        self.sidebar.category_selected.connect(self.on_category_changed)
        self.sidebar.ingest_requested.connect(self.start_ingest)
        self.sidebar.refresh_requested.connect(self.load_library_from_server)
        self.sidebar.delete_selected_requested.connect(self.delete_selected_assets)
        self.sidebar.clear_library_requested.connect(self.clear_entire_library)
        self.sidebar.import_library_requested.connect(self.import_library_file)
        self.sidebar.export_library_requested.connect(self.export_library)
        self.sidebar.pause_requested.connect(self.toggle_ingest_pause)
        self.sidebar.stop_requested.connect(self.stop_ingest)
        self.sidebar.sidebar_toggle_requested.connect(self.toggle_sidebar)
        
        # --- GALLERY SIGNALS ---
        self.gallery.filter_changed.connect(self.apply_filters)
        self.gallery.selection_changed.connect(self.on_selection_changed)
        self.gallery.asset_double_clicked.connect(self.on_double_click)
        self.gallery.folders_dropped.connect(self.on_folders_dropped)
        self.gallery.scroll_bottom_reached.connect(self.load_more_assets)
        self.gallery.delete_requested.connect(self.delete_selected_assets)
        self.gallery.sidebar_expand_requested.connect(self.toggle_sidebar)
        
        # --- INSPECTOR SIGNALS ---
        self.inspector.next_requested.connect(self.select_next_asset)
        self.inspector.prev_requested.connect(self.select_prev_asset)
        self.inspector.analysis_requested.connect(self.trigger_background_analysis)
        
        # --- INGEST CONTROLLER ---
        self.ingest_controller.status_updated.connect(self.sidebar.set_ingest_state)
        self.ingest_controller.progress_updated.connect(self.sidebar.set_ingest_progress)
        self.ingest_controller.ingest_finished.connect(self._on_ingest_finished)
        
        # --- MODEL SIGNALS ---
        self.proxy_model.layoutChanged.connect(self.update_ui_counts)
        self.proxy_model.rowsInserted.connect(lambda p,f,l: self.update_ui_counts())
        self.proxy_model.rowsRemoved.connect(lambda p,f,l: self.update_ui_counts())
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

    def _on_ingest_finished(self):
        if self._is_closing:
            return
        self.sidebar.set_controls_enabled(True)

    def toggle_sidebar(self):
        """Collapse/expand the filter sidebar for high-density browsing."""
        sizes = self.splitter.sizes()
        if not sizes or len(sizes) < 3:
            return
        current_sidebar = int(sizes[0])
        is_collapsed = current_sidebar <= 20

        if is_collapsed:
            target_sidebar = max(220, int(getattr(self, "_sidebar_expanded_width", 280)))
            gallery_and_inspector = max(1, sizes[1] + sizes[2])
            gallery_target = max(200, gallery_and_inspector - sizes[2])
            self.splitter.setSizes([target_sidebar, gallery_target, sizes[2]])
            self.sidebar.set_collapsed_visual(False)
            self.gallery.set_sidebar_collapsed(False)
            self._apply_responsive_layout()
            return

        self._sidebar_expanded_width = max(220, current_sidebar)
        self.splitter.setSizes([0, sizes[1] + current_sidebar, sizes[2]])
        self.sidebar.set_collapsed_visual(True)
        self.gallery.set_sidebar_collapsed(True)
        self._apply_responsive_layout()

    def _on_splitter_moved(self, pos, index):
        del pos, index
        self._apply_responsive_layout()

    def _apply_responsive_layout(self):
        """Sync compact modes and restore affordances with live pane sizes."""
        sizes = self.splitter.sizes()
        if len(sizes) != 3:
            return

        sidebar_w, _, inspector_w = sizes
        sidebar_collapsed = sidebar_w <= 20
        self.gallery.set_sidebar_collapsed(sidebar_collapsed)
        self.sidebar.set_collapsed_visual(sidebar_collapsed)

        if not sidebar_collapsed:
            self._sidebar_expanded_width = max(220, sidebar_w)
        if inspector_w > 40:
            self._inspector_expanded_width = max(280, inspector_w)

        # Keep side panels from getting unusably thin during manual drag.
        self.sidebar.set_compact_mode(sidebar_w < 265)
        self.inspector.set_compact_mode(inspector_w < 320)

    def on_category_changed(self, category):
        self.current_category = category
        self.apply_filters()

    def update_ui_counts(self):
        visible = self.proxy_model.rowCount()
        db_total = getattr(self, 'db_total', 0) or self.model.rowCount()
        self.gallery.update_count(db_total, visible)

    def on_selection_changed(self, selected, deselected):
        indexes = self.gallery.asset_view.selectionModel().selectedIndexes()
        if not indexes: return
        
        proxy_idx = indexes[0]
        source_idx = self.proxy_model.mapToSource(proxy_idx)
        asset = self.model.data(source_idx, Qt.ItemDataRole.UserRole)
        
        if asset:
            # Preview as soon as it is picked, not only on a double click.
            # Loading is debounced by 200ms in the player, so arrowing through a
            # long list does not start a decoder for every item on the way past.
            path = asset.get("path") or asset.get("file_path") or ""
            suffix = Path(path).suffix.lower() if path else ""
            if suffix and (is_video(suffix) or is_image(suffix)):
                self.inspector.player._pending_autoplay = True
            self.inspector.update_asset(asset)

    def on_double_click(self, index):
        asset = self.model.data(self.proxy_model.mapToSource(index), Qt.ItemDataRole.UserRole)
        if not asset: return

        path = asset.get('path') or asset.get('file_path')
        if not path or not os.path.exists(path): return

        suffix = Path(path).suffix.lower()
        if is_video(suffix) or is_image(suffix):
             self.inspector.player._pending_autoplay = True
             self.inspector.update_asset(asset)
        else:
             QDesktopServices.openUrl(QUrl.fromLocalFile(path))

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

    def start_ingest(self, fast_mode):
        if not self.can_ingest:
            return
        self.sidebar.set_controls_enabled(False)
        self.ingest_controller.start_ingest(fast_mode=fast_mode)

    def on_folders_dropped(self, folders):
        if not self.can_ingest:
             self._notify("Ingest is restricted to Developer Mode.", "warning")
             return
        self.ingest_controller.on_folders_dropped(folders, fast_mode=self.sidebar.toggle_fast.isChecked())

    def toggle_ingest_pause(self):
        is_paused = self.ingest_controller.toggle_pause()
        self.sidebar.set_pause_btn_text("\u25b6 Resume" if is_paused else "\u23f8 Pause")

    def stop_ingest(self):
        self.ingest_controller.stop_ingest()
        self.sidebar.set_ingest_state("Stopping...", True)

    def select_next_asset(self): self._navigate(1)
    def select_prev_asset(self): self._navigate(-1)

    def _navigate(self, step):
        indexes = self.gallery.asset_view.selectionModel().selectedIndexes()
        count = self.proxy_model.rowCount()
        if count == 0: return

        new_row = 0
        if indexes:
            current_row = indexes[0].row()
            new_row = current_row + step
        
        if 0 <= new_row < count:
            new_idx = self.proxy_model.index(new_row, 0)
            self.gallery.asset_view.setCurrentIndex(new_idx)
            self.gallery.asset_view.scrollTo(new_idx)

    def cleanup_resources(self):
        """Gracefully stop all background workers."""
        self._is_closing = True
        self.inspector.cleanup()
        self.ingest_controller.cleanup()
        self._cancel_loader_thread(timeout_ms=3000)
        self.model.cleanup()

    def closeEvent(self, event):
        self._is_closing = True
        self.cleanup_resources()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()
