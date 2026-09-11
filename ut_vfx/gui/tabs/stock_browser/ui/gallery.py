from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget, 
    QLineEdit, QButtonGroup, QSlider, 
    QProgressBar, QListView, QAbstractItemView, QFrame, QGridLayout, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer
from pathlib import Path

# Internal
from ..widgets import DraggableListView
# ....stock_model -> gui/stock_model
from ....stock_model import StockDelegate, StockListDelegate
from ....components.qt_safety import safe_single_shot
from .....core.infra.design_tokens import ColorTokens as C, TypographyTokens as T
from ....widgets.styled_buttons import SecondaryButton, GhostButton, StyledComboBox

class EmptyStateWidget(QWidget):
    """Displayed when the library is empty."""
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)
        
        # Icon / Illustration
        lbl_icon = QLabel("\U0001F4C2")
        lbl_icon.setStyleSheet("font-size: 64px;")
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Text
        lbl_text = QLabel("Library is Empty")
        lbl_text.setStyleSheet(f"font-size: 24px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_PRIMARY};")
        lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_sub = QLabel("Drag and drop folders here to start ingesting assets.")
        lbl_sub.setStyleSheet(f"font-size: 14px; color: {C.TEXT_SECONDARY};")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(lbl_icon)
        layout.addWidget(lbl_text)
        layout.addWidget(lbl_sub)


class NoResultsWidget(QWidget):
    """Displayed when filters match no assets."""
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        lbl_icon = QLabel("\U0001F50D")
        lbl_icon.setStyleSheet("font-size: 48px;")
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_text = QLabel("No Matching Assets")
        lbl_text.setStyleSheet(f"font-size: 18px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_PRIMARY};")
        lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_sub = QLabel("Try adjusting your search or filter criteria.")
        lbl_sub.setStyleSheet(f"font-size: 13px; color: {C.TEXT_SECONDARY};")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(lbl_icon)
        layout.addWidget(lbl_text)
        layout.addWidget(lbl_sub)


class SkeletonStateWidget(QWidget):
    """Displayed while assets are loading to avoid blank/frozen UI."""
    def __init__(self):
        super().__init__()
        self._pulse_state = False
        self._tiles = []
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(150)
        self._pulse_timer.timeout.connect(self._pulse)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        lbl_text = QLabel("Loading assets...")
        lbl_text.setStyleSheet(f"font-size: 16px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_PRIMARY};")
        lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_text)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)

        for idx in range(8):
            tile = QFrame()
            tile.setFixedSize(160, 100)
            tile.setStyleSheet("border-radius: 8px; background-color: #26262D;")
            self._tiles.append(tile)
            grid.addWidget(tile, idx // 4, idx % 4)
        layout.addWidget(grid_host, 0, Qt.AlignmentFlag.AlignCenter)

    def start(self):
        self._pulse_timer.start()
        self._pulse()

    def stop(self):
        self._pulse_timer.stop()
        self._apply_pulse(False)

    def _pulse(self):
        self._pulse_state = not self._pulse_state
        self._apply_pulse(self._pulse_state)

    def _apply_pulse(self, bright: bool):
        color = "#2C2C34" if bright else "#26262D"
        for tile in self._tiles:
            tile.setStyleSheet(f"border-radius: 8px; background-color: {color};")


class StockGallery(QWidget):
    """
    Gallery View for Stock Browser.
    """
    filter_changed = Signal() 
    asset_double_clicked = Signal(object)
    folders_dropped = Signal(list)
    selection_changed = Signal(object, object)
    zoom_changed = Signal(int)
    scroll_bottom_reached = Signal()
    delete_requested = Signal()
    sidebar_expand_requested = Signal()
    
    def __init__(self, model, proxy_model, can_manage_assets=False, parent=None):
        super().__init__(parent)
        self.model = model
        self.proxy_model = proxy_model
        self.can_manage_assets = bool(can_manage_assets)
        self.is_loading_state = False
        self._last_total_count = 0
        self._last_visible_count = 0
        
        self.setup_ui()
        self.setup_connections()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # --- TOOLBAR ---
        top_bar_host = QWidget(self)
        top_bar_host.setObjectName("StockTopBar")
        top_bar_root = QVBoxLayout(top_bar_host)
        top_bar_root.setContentsMargins(10, 10, 10, 8)
        top_bar_root.setSpacing(8)

        # Row 1: View toggle, Search, Count
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.btn_show_filters = GhostButton("\u25B8 Filters", self)
        self.btn_show_filters.setToolTip("Show filter sidebar")
        self.btn_show_filters.setMinimumHeight(34)
        self.btn_show_filters.setVisible(False)
        self.btn_show_filters.clicked.connect(self.sidebar_expand_requested.emit)
        top_bar.addWidget(self.btn_show_filters)

        # View Toggle
        self.btn_view_toggle = SecondaryButton("Grid")
        self.btn_view_toggle.border_radius = 16
        self.btn_view_toggle.setFixedSize(80, 32)
        self.btn_view_toggle.clicked.connect(self.toggle_view_mode)
        top_bar.addWidget(self.btn_view_toggle)

        # Search Bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("\U0001F50D  Search assets...")
        self.search_bar.setMinimumWidth(190)
        self.search_bar.setFixedHeight(32)
        self.search_bar.setMaximumWidth(320)
        self.search_bar.setStyleSheet(f"""
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_PRIMARY};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px;
                padding: 4px 14px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.ACCENT_PRIMARY};
                background-color: rgba(0, 204, 255, 0.05);
            }}
        """)
        top_bar.addWidget(self.search_bar)

        top_bar.addStretch(1)

        # Count Label
        self.lbl_count = QLabel("0 Assets")
        self.lbl_count.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHT}; font-weight: bold; font-size: 12px;")
        self.lbl_count.setMinimumWidth(90)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top_bar.addWidget(self.lbl_count)
        top_bar_root.addLayout(top_bar)

        # Row 2: Visual, Sort, Media type toggles
        # Media Type Filter
        self.btn_group = QButtonGroup(self)
        self.btn_all = SecondaryButton("All"); self.btn_all.setCheckable(True); self.btn_all.setChecked(True)
        self.btn_img = SecondaryButton("Img"); self.btn_img.setCheckable(True)
        self.btn_vid = SecondaryButton("Vid"); self.btn_vid.setCheckable(True)
        for btn in (self.btn_all, self.btn_img, self.btn_vid):
            btn.border_radius = 16
            btn.setFixedSize(50, 32)

        self.btn_group.addButton(self.btn_all)
        self.btn_group.addButton(self.btn_img)
        self.btn_group.addButton(self.btn_vid)

        # Sort Combo
        self.sort_combo = StyledComboBox()
        self.sort_combo.addItems(["Sort: Newest", "Sort: Oldest", "Sort: Name"])
        self.sort_combo.setMinimumWidth(110)
        self.sort_combo.setFixedHeight(32)
        self.sort_combo.setMaximumWidth(160)

        # Visual Filter
        self.combo_visual = StyledComboBox()
        self.combo_visual.addItems(["Visual: Any", "Dark", "Bright", "Warm", "Cold", "Green Screen", "Blue Screen"])
        self.combo_visual.setToolTip("Filter by Visual Characteristics")
        self.combo_visual.setMinimumWidth(110)
        self.combo_visual.setFixedHeight(32)
        self.combo_visual.setMaximumWidth(190)

        media_row = QHBoxLayout()
        media_row.setSpacing(8)
        media_row.addWidget(self.combo_visual)
        media_row.addWidget(self.sort_combo)
        media_row.addWidget(self.btn_all)
        media_row.addWidget(self.btn_img)
        media_row.addWidget(self.btn_vid)
        media_row.addStretch(1)
        top_bar_root.addLayout(media_row)
        
        top_bar_host.setStyleSheet(
            f"""
            QWidget#StockTopBar {{
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                background-color: transparent;
            }}
            """
        )
        layout.addWidget(top_bar_host)
        
        # --- STACKED WIDGET ---
        self.stack = QStackedWidget()
        
        # 1. Main Asset View
        self.asset_view = DraggableListView()
        self.asset_view.setViewMode(QListView.IconMode)
        self.asset_view.setResizeMode(QListView.Adjust)
        self.asset_view.setSpacing(10)
        self.asset_view.setModel(self.proxy_model)
        self.asset_view.setItemDelegate(StockDelegate())
        self.asset_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.asset_view.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # Optimization
        self.asset_view.setUniformItemSizes(True)
        self.asset_view.setLayoutMode(QListView.Batched)
        self.asset_view.setBatchSize(50)
        self.asset_view.setAutoScroll(False)
        self.asset_view.setMovement(QListView.Static)
        self.asset_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.asset_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        
        self.stack.addWidget(self.asset_view)
        
        # 2. Empty State
        self.empty_state = EmptyStateWidget()
        self.stack.addWidget(self.empty_state)

        # 3. Skeleton Loading State
        self.skeleton_state = SkeletonStateWidget()
        self.stack.addWidget(self.skeleton_state)

        # 4. No Results State
        self.no_results = NoResultsWidget()
        self.stack.addWidget(self.no_results)
        
        layout.addWidget(self.stack)
        
        # --- BOTTOM TOOLBAR (Zoom) ---
        zoom_layout = QHBoxLayout()
        zoom_layout.setContentsMargins(8, 4, 8, 6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        zoom_layout.addWidget(self.progress_bar)
        
        zoom_layout.addStretch()
        zoom_layout.addWidget(QLabel("Zoom:"))
        
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 500)
        self.zoom_slider.setValue(160)
        self.zoom_slider.setMinimumWidth(90)
        self.zoom_slider.setMaximumWidth(220)
        self.zoom_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        zoom_layout.addWidget(self.zoom_slider)
        
        layout.addLayout(zoom_layout)
        
        self.change_thumbnail_size(160)
        self._apply_responsive_toolbar()

    def setup_connections(self):
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._emit_filter)
        
        self.search_bar.textChanged.connect(lambda: self.search_timer.start())
        self.combo_visual.currentIndexChanged.connect(self._emit_filter)
        self.btn_group.buttonClicked.connect(self._emit_filter)
        self.sort_combo.currentIndexChanged.connect(self._emit_filter)
        
        self.asset_view.selectionModel().selectionChanged.connect(self.selection_changed.emit)
        self.asset_view.doubleClicked.connect(self.asset_double_clicked.emit)
        self.asset_view.folders_dropped.connect(self.folders_dropped.emit)
        
        self.zoom_debounce = QTimer()
        self.zoom_debounce.setSingleShot(True)
        self.zoom_debounce.setInterval(100)
        self.zoom_debounce.timeout.connect(lambda: self.change_thumbnail_size(self.zoom_slider.value()))
        self.zoom_slider.valueChanged.connect(lambda: self.zoom_debounce.start())
        
        # Infinite Scroll
        self.asset_view.verticalScrollBar().valueChanged.connect(self._check_scroll)
        
        # Context Menu
        self.asset_view.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, pos):
        index = self.asset_view.indexAt(pos)
        if not index.isValid(): return
        sel_model = self.asset_view.selectionModel()
        if sel_model and not sel_model.isSelected(index):
            self.asset_view.setCurrentIndex(index)
        
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background-color: {C.BG_SURFACE}; color: {C.TEXT_PRIMARY}; border: 1px solid {C.BORDER_LIGHT}; }} QMenu::item:selected {{ background-color: {C.ACCENT_PRIMARY}; }}")
        
        action_copy = menu.addAction("\U0001F4C4 Copy Path")
        action_copy.triggered.connect(lambda: self._copy_path_to_clipboard(index))
        
        action_reveal = menu.addAction("\U0001F4C2 Reveal")
        action_reveal.triggered.connect(lambda: self._reveal_in_explorer(index))
        
        menu.addSeparator()
        
        if self.can_manage_assets:
            menu.addSeparator()
            action_delete = menu.addAction("Delete Selected from Library")
            action_delete.triggered.connect(self.delete_requested.emit)

        menu.exec(self.asset_view.viewport().mapToGlobal(pos))
        
    def _copy_path_to_clipboard(self, index):
        asset = index.data(Qt.ItemDataRole.UserRole)
        path = asset.get('path') or asset.get('file_path') if isinstance(asset, dict) else getattr(asset, 'path', None)
        
        if path:
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(str(path))

    def _reveal_in_explorer(self, index):
        asset = index.data(Qt.ItemDataRole.UserRole)
        path = asset.get('path') or asset.get('file_path') if isinstance(asset, dict) else getattr(asset, 'path', None)
        
        if path:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            import os
            
            # Use explorer /select for Windows to select the file
            if os.name == 'nt':
                import subprocess
                subprocess.Popen(['explorer', '/select,', str(Path(path))])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _check_scroll(self, value):
        maximum = self.asset_view.verticalScrollBar().maximum()
        if maximum > 0 and value >= (maximum - 200): # Threshold
             self.scroll_bottom_reached.emit()
        
    def _emit_filter(self):
        self.filter_changed.emit()

    def get_filter_state(self):
        media_type = "All"
        if self.btn_img.isChecked(): media_type = "Images"
        elif self.btn_vid.isChecked(): media_type = "Videos"
            
        return {
            "search": self.search_bar.text().strip(),
            "visual": self.combo_visual.currentText().replace("Visual: ", ""),
            "media_type": media_type,
            "sort_index": self.sort_combo.currentIndex()
        }

    def update_count(self, total, visible):
        self._last_total_count = int(total or 0)
        self._last_visible_count = int(visible or 0)
        self.lbl_count.setText(f"Showing {visible} of {total} Assets")
        self._apply_responsive_toolbar()
        if self.is_loading_state:
            self.stack.setCurrentWidget(self.skeleton_state)
            return
        
        if visible == 0 and total == 0:
             self.stack.setCurrentWidget(self.empty_state)
        elif visible == 0:
             self.stack.setCurrentWidget(self.no_results)
        else:
             self.stack.setCurrentWidget(self.asset_view)

    def set_loading_state(self, is_loading: bool):
        """Toggle skeleton loading state for fetch/refresh operations."""
        self.is_loading_state = bool(is_loading)
        if self.is_loading_state:
            self.stack.setCurrentWidget(self.skeleton_state)
            self.skeleton_state.start()
            return

        self.skeleton_state.stop()
        total = self.model.rowCount()
        visible = self.proxy_model.rowCount()
        self.update_count(total, visible)

    def toggle_view_mode(self):
        current_delegate = self.asset_view.itemDelegate()
        current_zoom = 160
        if hasattr(current_delegate, 'thumb_width'):
             current_zoom = current_delegate.thumb_width
        
        if self.asset_view.viewMode() == QListView.IconMode:
            self.asset_view.setViewMode(QListView.ListMode)
            self.asset_view.setItemDelegate(StockListDelegate())
            self.asset_view.setSpacing(2)
            self.btn_view_toggle.setText("View: List")
        else:
            self.change_thumbnail_size(current_zoom)
            self.asset_view.setViewMode(QListView.IconMode)
            self.asset_view.setSpacing(10)
            self.btn_view_toggle.setText("View: Grid")
        self._apply_responsive_toolbar()

    def change_thumbnail_size(self, val):
        delegate = self.asset_view.itemDelegate()
        if not isinstance(delegate, StockDelegate):
             delegate = StockDelegate()
             self.asset_view.setItemDelegate(delegate)
        
        scrollbar = self.asset_view.verticalScrollBar()
        scroll_ratio = scrollbar.value() / max(scrollbar.maximum(), 1) if scrollbar.maximum() > 0 else 0
        
        delegate.thumb_width = val
        delegate.thumb_height = int(val * 0.56)
        
        self.model.layoutAboutToBeChanged.emit()
        self.model.layoutChanged.emit()
        
        safe_single_shot(
            50,
            self.asset_view,
            lambda: scrollbar.setValue(int(scroll_ratio * scrollbar.maximum())),
            skip_when_closing_attr=None,
        )

    def set_sidebar_collapsed(self, collapsed: bool):
        """Keep a persistent restore affordance when sidebar is hidden."""
        self.btn_show_filters.setVisible(bool(collapsed))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_toolbar()

    def _apply_responsive_toolbar(self):
        """Adapt toolbar controls to pane width to prevent overlap."""
        width = max(1, self.width())
        compact = width < 1120
        ultra_compact = width < 900

        self.search_bar.setMaximumWidth(140 if ultra_compact else (180 if compact else 240))
        self.combo_visual.setMaximumWidth(130 if ultra_compact else (160 if compact else 190))
        self.sort_combo.setMaximumWidth(120 if ultra_compact else (140 if compact else 160))
        self.zoom_slider.setMaximumWidth(120 if ultra_compact else (170 if compact else 220))

        if self.asset_view.viewMode() == QListView.IconMode:
            self.btn_view_toggle.setText("Grid" if compact else "View: Grid")
        else:
            self.btn_view_toggle.setText("List" if compact else "View: List")

        self.btn_show_filters.setText("\u25B8" if ultra_compact else "\u25B8 Filters")

        self.btn_img.setText("Img" if ultra_compact else "Images")
        self.btn_vid.setText("Vid" if ultra_compact else "Videos")

        if ultra_compact:
            self.lbl_count.setVisible(False)
        else:
            self.lbl_count.setVisible(True)
            if compact:
                self.lbl_count.setText(f"{self._last_visible_count}/{self._last_total_count}")
            else:
                self.lbl_count.setText(f"Showing {self._last_visible_count} of {self._last_total_count} Assets")
