from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, 
    QLabel, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal

# Internal
from ..widgets import PyToggle
from .....core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R
from ....widgets.styled_buttons import (
    PrimaryButton, SecondaryButton, DangerButton, GhostButton
)

class StockSidebar(QWidget):
    """
    Sidebar Panel for Stock Browser.
    """
    category_selected = Signal(str)
    ingest_requested = Signal(bool) # fast_mode
    pause_requested = Signal()
    stop_requested = Signal()
    refresh_requested = Signal()
    delete_selected_requested = Signal()
    clear_library_requested = Signal()
    import_library_requested = Signal()
    export_library_requested = Signal()
    sidebar_toggle_requested = Signal()

    def __init__(self, parent=None, can_ingest=False):
        super().__init__(parent)
        self.can_ingest = can_ingest
        self.toggle_fast = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        self.setMinimumWidth(220)
        self.setMaximumWidth(440)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        header_row = QHBoxLayout()
        header_lbl = QLabel("Asset Filters")
        header_lbl.setStyleSheet(f"font-size: 12px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_SECONDARY};")
        self.btn_collapse = GhostButton("\u25C2")
        self.btn_collapse.setFixedWidth(28)
        self.btn_collapse.setToolTip("Collapse sidebar")
        self.btn_collapse.clicked.connect(self.sidebar_toggle_requested.emit)
        header_row.addWidget(header_lbl)
        header_row.addStretch()
        header_row.addWidget(self.btn_collapse)
        layout.addLayout(header_row)

        # 1. Navigation Group
        
        # Categories List
        cat_widget = QWidget(); cat_layout = QVBoxLayout(cat_widget); cat_layout.setContentsMargins(0,0,0,0)
        lbl_cat = QLabel("Smart Categories:")
        lbl_cat.setStyleSheet(f"font-size: 11px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_SECONDARY};")
        cat_layout.addWidget(lbl_cat)
        
        self.category_list = QListWidget()
        self.category_list.setStyleSheet(f"""
            QListWidget {{ background-color: {C.BG_HOVER}; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.SM}px; }}
            QListWidget::item {{ padding: 6px 8px; border-radius: 4px; color: {C.TEXT_PRIMARY}; }}
            QListWidget::item:selected {{ background-color: {C.ACCENT_PRIMARY}; color: white; font-weight: bold; }}
            QListWidget::item:hover {{ background-color: {C.BORDER_DEFAULT}; }}
        """)
        self.category_list.itemClicked.connect(self._on_category_clicked)
        
        cat_layout.addWidget(self.category_list)
        # Sized to its contents, not stretched.
        #
        # This carried a stretch factor, so four categories expanded to fill the
        # whole column and shoved the ingest controls and the action buttons to
        # the very bottom - leaving roughly 300px of empty rail in between. The
        # stretch now sits after the actions instead, so the rail reads top-down.
        self.category_list.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Maximum)
        self.category_list.setMaximumHeight(190)
        layout.addWidget(cat_widget, 0)


        # 2. Ingest Controls (Developer Only)
        if self.can_ingest:
            self.ingest_controls = QFrame()
            self.ingest_controls.setStyleSheet(f"background-color: {C.BG_HOVER}; border-radius: {R.SM}px; padding: {S.XS}px;")
            ic_layout = QVBoxLayout(self.ingest_controls)
            
            # Fast Mode Toggle
            toggle_layout = QHBoxLayout()
            self.lbl_fast = QLabel("Fast Mode")
            self.lbl_fast.setStyleSheet(f"font-size: 11px; color: {C.TEXT_SECONDARY};")
            self.toggle_fast = PyToggle()
            toggle_layout.addWidget(self.lbl_fast)
            toggle_layout.addWidget(self.toggle_fast)
            ic_layout.addLayout(toggle_layout)
            
            # Progress Area
            self.ingest_progress_area = QWidget()
            self.ingest_progress_area.setVisible(False)
            ipa_layout = QVBoxLayout(self.ingest_progress_area)
            
            self.lbl_ingest_status = QLabel("Ingesting...")
            self.lbl_ingest_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ipa_layout.addWidget(self.lbl_ingest_status)
            
            from PySide6.QtWidgets import QProgressBar
            self.progress_bar_ingest = QProgressBar()
            self.progress_bar_ingest.setRange(0, 100)
            self.progress_bar_ingest.setValue(0)
            self.progress_bar_ingest.setFixedHeight(12)
            self.progress_bar_ingest.setTextVisible(False)
            ipa_layout.addWidget(self.progress_bar_ingest)
            
            btn_row = QHBoxLayout()
            self.btn_pause = SecondaryButton("\u23F8 Pause")
            self.btn_pause.clicked.connect(self.pause_requested.emit)
            
            self.btn_stop = DangerButton("\u23F9 Stop")
            self.btn_stop.clicked.connect(self.stop_requested.emit)
            
            btn_row.addWidget(self.btn_pause)
            btn_row.addWidget(self.btn_stop)
            ipa_layout.addLayout(btn_row)
            
            ic_layout.addWidget(self.ingest_progress_area)
            layout.addWidget(self.ingest_controls)

        # 3. Action Buttons — Vertical stack for clarity
        actions_frame = QFrame()
        actions_frame.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
                padding: {S.SM}px;
            }}
        """)
        actions_layout = QVBoxLayout(actions_frame)
        actions_layout.setContentsMargins(8, 10, 8, 10)
        actions_layout.setSpacing(12)

        self.btn_refresh = GhostButton("Refresh")
        self.btn_refresh.border_radius = R.SM
        self.btn_refresh.setMinimumHeight(28)
        self.btn_refresh.setToolTip("Refresh Library")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)

        if self.can_ingest:
            self.btn_ingest = PrimaryButton("Ingest")
            self.btn_ingest.border_radius = R.SM
            self.btn_ingest.setMinimumHeight(32)
            self.btn_ingest.clicked.connect(lambda: self.ingest_requested.emit(self.toggle_fast.isChecked()))
            actions_layout.addWidget(self.btn_ingest)

            # Secondary row: Refresh + Delete
            row1 = QHBoxLayout()
            row1.setSpacing(8)
            row1.addWidget(self.btn_refresh)

            self.btn_delete_selected = DangerButton("Delete")
            self.btn_delete_selected.border_radius = R.SM
            self.btn_delete_selected.setMinimumHeight(28)
            self.btn_delete_selected.setToolTip("Delete selected assets from database and proxy cache")
            self.btn_delete_selected.clicked.connect(self.delete_selected_requested.emit)
            row1.addWidget(self.btn_delete_selected)
            actions_layout.addLayout(row1)

            # Tertiary row: Export + Import
            row2 = QHBoxLayout()
            row2.setSpacing(8)

            self.btn_export = SecondaryButton("Export")
            self.btn_export.border_radius = R.SM
            self.btn_export.setMinimumHeight(28)
            self.btn_export.clicked.connect(self.export_library_requested.emit)

            self.btn_import = SecondaryButton("Import")
            self.btn_import.border_radius = R.SM
            self.btn_import.setMinimumHeight(28)
            self.btn_import.clicked.connect(self.import_library_requested.emit)

            row2.addWidget(self.btn_export)
            row2.addWidget(self.btn_import)
            actions_layout.addLayout(row2)

            # Clear Library — subtle, at the bottom
            self.btn_clear = GhostButton("Clear Library")
            self.btn_clear.border_radius = R.SM
            self.btn_clear.setMinimumHeight(28)
            self.btn_clear.setToolTip("Clear entire library")
            self.btn_clear.clicked.connect(self.clear_library_requested.emit)
            actions_layout.addWidget(self.btn_clear)
        else:
            # Client Mode: Minimal UI
            actions_layout.addWidget(self.btn_refresh)

        layout.addWidget(actions_frame)
        layout.addStretch(1)
        self.set_compact_mode(False)

    def _on_category_clicked(self, item):
        if item:
            self.category_selected.emit(item.text())

    def update_categories(self, categories_list):
        self.category_list.clear()
        self.category_list.addItem("All")
        self.category_list.addItem("Favorites")
# Internal
from ..widgets import PyToggle
from .....core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R
from ....widgets.styled_buttons import (
    PrimaryButton, SecondaryButton, DangerButton, GhostButton
)

class StockSidebar(QWidget):
    """
    Sidebar Panel for Stock Browser.
    """
    category_selected = Signal(str)
    ingest_requested = Signal(bool) # fast_mode
    pause_requested = Signal()
    stop_requested = Signal()
    refresh_requested = Signal()
    delete_selected_requested = Signal()
    clear_library_requested = Signal()
    import_library_requested = Signal()
    export_library_requested = Signal()
    sidebar_toggle_requested = Signal()

    def __init__(self, parent=None, can_ingest=False):
        super().__init__(parent)
        self.can_ingest = can_ingest
        self.toggle_fast = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        self.setMinimumWidth(220)
        self.setMaximumWidth(440)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        header_row = QHBoxLayout()
        header_lbl = QLabel("Asset Filters")
        header_lbl.setStyleSheet(f"font-size: 12px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_SECONDARY};")
        self.btn_collapse = GhostButton("\u25C2")
        self.btn_collapse.setFixedWidth(28)
        self.btn_collapse.setToolTip("Collapse sidebar")
        self.btn_collapse.clicked.connect(self.sidebar_toggle_requested.emit)
        header_row.addWidget(header_lbl)
        header_row.addStretch()
        header_row.addWidget(self.btn_collapse)
        layout.addLayout(header_row)

        # 1. Navigation Group
        
        # Categories List
        cat_widget = QWidget(); cat_layout = QVBoxLayout(cat_widget); cat_layout.setContentsMargins(0,0,0,0)
        lbl_cat = QLabel("Smart Categories:")
        lbl_cat.setStyleSheet(f"font-size: 11px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_SECONDARY};")
        cat_layout.addWidget(lbl_cat)
        
        self.category_list = QListWidget()
        self.category_list.setStyleSheet(f"""
            QListWidget {{ background-color: transparent; border: none; outline: none; }}
            QListWidget::item {{ padding: 8px 12px; border-radius: 12px; color: {C.TEXT_PRIMARY}; margin-bottom: 4px; }}
            QListWidget::item:selected {{ background-color: {C.ACCENT_PRIMARY}; color: white; font-weight: bold; }}
            QListWidget::item:hover:!selected {{ background-color: rgba(255, 255, 255, 0.05); }}
        """)
        self.category_list.itemClicked.connect(self._on_category_clicked)
        
        cat_layout.addWidget(self.category_list)
        # Sized to its contents, not stretched.
        #
        # This carried a stretch factor, so four categories expanded to fill the
        # whole column and shoved the ingest controls and the action buttons to
        # the very bottom - leaving roughly 300px of empty rail in between. The
        # stretch now sits after the actions instead, so the rail reads top-down.
        self.category_list.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Maximum)
        self.category_list.setMaximumHeight(190)
        layout.addWidget(cat_widget, 0)


        # 2. Ingest Controls (Developer Only)
        if self.can_ingest:
            self.ingest_controls = QFrame()
            self.ingest_controls.setStyleSheet(f"background-color: {C.BG_HOVER}; border-radius: {R.SM}px; padding: {S.XS}px;")
            ic_layout = QVBoxLayout(self.ingest_controls)
            
            # Fast Mode Toggle
            toggle_layout = QHBoxLayout()
            self.lbl_fast = QLabel("Fast Mode")
            self.lbl_fast.setStyleSheet(f"font-size: 11px; color: {C.TEXT_SECONDARY};")
            self.toggle_fast = PyToggle()
            toggle_layout.addWidget(self.lbl_fast)
            toggle_layout.addWidget(self.toggle_fast)
            ic_layout.addLayout(toggle_layout)
            
            # Progress Area
            self.ingest_progress_area = QWidget()
            self.ingest_progress_area.setVisible(False)
            ipa_layout = QVBoxLayout(self.ingest_progress_area)
            
            self.lbl_ingest_status = QLabel("Ingesting...")
            self.lbl_ingest_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ipa_layout.addWidget(self.lbl_ingest_status)
            
            from PySide6.QtWidgets import QProgressBar
            self.progress_bar_ingest = QProgressBar()
            self.progress_bar_ingest.setRange(0, 100)
            self.progress_bar_ingest.setValue(0)
            self.progress_bar_ingest.setFixedHeight(12)
            self.progress_bar_ingest.setTextVisible(False)
            ipa_layout.addWidget(self.progress_bar_ingest)
            
            btn_row = QHBoxLayout()
            self.btn_pause = SecondaryButton("\u23F8 Pause")
            self.btn_pause.clicked.connect(self.pause_requested.emit)
            
            self.btn_stop = DangerButton("\u23F9 Stop")
            self.btn_stop.clicked.connect(self.stop_requested.emit)
            
            btn_row.addWidget(self.btn_pause)
            btn_row.addWidget(self.btn_stop)
            ipa_layout.addLayout(btn_row)
            
            ic_layout.addWidget(self.ingest_progress_area)
            layout.addWidget(self.ingest_controls)

        # 3. Action Buttons — Vertical stack for clarity
        actions_frame = QFrame()
        actions_frame.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
                padding: {S.SM}px;
            }}
        """)
        actions_layout = QVBoxLayout(actions_frame)
        actions_layout.setContentsMargins(8, 10, 8, 10)
        actions_layout.setSpacing(12)

        self.btn_refresh = GhostButton("Refresh")
        self.btn_refresh.border_radius = R.SM
        self.btn_refresh.setMinimumHeight(28)
        self.btn_refresh.setToolTip("Refresh Library")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)

        if self.can_ingest:
            self.btn_ingest = PrimaryButton("Ingest")
            self.btn_ingest.border_radius = R.SM
            self.btn_ingest.setMinimumHeight(32)
            self.btn_ingest.clicked.connect(lambda: self.ingest_requested.emit(self.toggle_fast.isChecked()))
            actions_layout.addWidget(self.btn_ingest)

            # Secondary row: Refresh + Delete
            row1 = QHBoxLayout()
            row1.setSpacing(8)
            row1.addWidget(self.btn_refresh)

            self.btn_delete_selected = DangerButton("Delete")
            self.btn_delete_selected.border_radius = R.SM
            self.btn_delete_selected.setMinimumHeight(28)
            self.btn_delete_selected.setToolTip("Delete selected assets from database and proxy cache")
            self.btn_delete_selected.clicked.connect(self.delete_selected_requested.emit)
            row1.addWidget(self.btn_delete_selected)
            actions_layout.addLayout(row1)

            # Tertiary row: Export + Import
            row2 = QHBoxLayout()
            row2.setSpacing(8)

            self.btn_export = SecondaryButton("Export")
            self.btn_export.border_radius = R.SM
            self.btn_export.setMinimumHeight(28)
            self.btn_export.clicked.connect(self.export_library_requested.emit)

            self.btn_import = SecondaryButton("Import")
            self.btn_import.border_radius = R.SM
            self.btn_import.setMinimumHeight(28)
            self.btn_import.clicked.connect(self.import_library_requested.emit)

            row2.addWidget(self.btn_export)
            row2.addWidget(self.btn_import)
            actions_layout.addLayout(row2)

            # Clear Library — subtle, at the bottom
            self.btn_clear = GhostButton("Clear Library")
            self.btn_clear.border_radius = R.SM
            self.btn_clear.setMinimumHeight(28)
            self.btn_clear.setToolTip("Clear entire library")
            self.btn_clear.clicked.connect(self.clear_library_requested.emit)
            actions_layout.addWidget(self.btn_clear)
        else:
            # Client Mode: Minimal UI
            actions_layout.addWidget(self.btn_refresh)

        layout.addWidget(actions_frame)
        layout.addStretch(1)
        self.set_compact_mode(False)

    def _on_category_clicked(self, item):
        if item:
            self.category_selected.emit(item.text())

    def update_categories(self, categories_list):
        self.category_list.clear()
        self.category_list.addItem("All")
        self.category_list.addItem("Favorites")
        
        for c in sorted(list(categories_list)):
            self.category_list.addItem(c)
        self.category_list.setCurrentRow(0)

    def set_ingest_state(self, status_text="", visible=True):
        if not hasattr(self, 'ingest_progress_area'): return
        self.ingest_progress_area.setVisible(visible)
        if visible:
            self.lbl_ingest_status.setText(status_text)
            if hasattr(self, 'progress_bar_ingest'):
                self.progress_bar_ingest.setValue(0)
            self.btn_pause.setEnabled(True)
            self.btn_pause.setText("Pause")

    def set_ingest_progress(self, percent, status_text):
        if not hasattr(self, 'ingest_progress_area'): return
        if hasattr(self, 'progress_bar_ingest'):
            self.progress_bar_ingest.setValue(percent)
        if status_text:
            self.lbl_ingest_status.setText(status_text)

    def set_pause_btn_text(self, text):
        self.btn_pause.setText(text)

    def set_controls_enabled(self, enabled):
        if hasattr(self, 'ingest_controls'):
            self.ingest_controls.setEnabled(enabled)
        if hasattr(self, "btn_delete_selected"):
            self.btn_delete_selected.setEnabled(enabled)
        self.btn_refresh.setEnabled(enabled)

    def set_collapsed_visual(self, collapsed: bool):
        """Update toggle icon based on sidebar visibility state."""
        if hasattr(self, "btn_collapse"):
            self.btn_collapse.setText(">>" if collapsed else "<<")
            self.btn_collapse.setToolTip("Expand sidebar" if collapsed else "Collapse sidebar")

    def set_compact_mode(self, compact: bool):
        """Adjust labels when sidebar is narrow while preserving readability."""
        if not self.can_ingest:
            return

        self.btn_ingest.setText("Ingest")
        self.btn_delete_selected.setText("Delete")
        self.btn_refresh.setText("Refresh")
        self.btn_export.setText("Export")
        self.btn_import.setText("Import")
        if hasattr(self, 'btn_clear'):
            self.btn_clear.setText("Clear" if compact else "Clear Library")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_compact_mode(self.width() < 265)
