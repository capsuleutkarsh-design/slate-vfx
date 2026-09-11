from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QHBoxLayout, QProgressBar, QFrame, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor
from ...core.infra.task_registry import task_registry, TaskInfo
from ...core.infra.design_tokens import ColorTokens as C, RadiusTokens as R, TypographyTokens as T

class TaskItemWidget(QFrame):
    def __init__(self, task_info: TaskInfo, parent=None):
        super().__init__(parent)
        self.task_info = task_info
        
        # Premium Card Styling
        self.setObjectName("TaskCard")
        self.setStyleSheet(f"""
            #TaskCard {{
                background-color: {C.BG_ELEVATED};
                border-radius: {R.MD}px;
                border: 1px solid {C.BORDER_SUBTLE};
            }}
            #TaskCard:hover {{
                border: 1px solid {C.BORDER_HOVER};
                background-color: {C.BG_HOVER};
            }}
            QLabel {{ background: transparent; }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        self.setFixedWidth(320)
        
        # Header
        header_layout = QHBoxLayout()
        self.name_label = QLabel(task_info.name)
        self.name_label.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-weight: {T.WEIGHT_BOLD}; font-size: {T.SIZE_MD}px;")
        
        self.status_label = QLabel(task_info.status.upper())
        self.status_label.setStyleSheet(f"color: {C.ACCENT_PRIMARY}; font-weight: {T.WEIGHT_SEMIBOLD}; font-size: {T.SIZE_XS}px; letter-spacing: 1px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        header_layout.addWidget(self.name_label)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        layout.addLayout(header_layout)
        
        # Progress Bar (Sleek)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(task_info.progress)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {C.BG_DARKER};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 {C.ACCENT_DARK}, stop:1 {C.ACCENT_PRIMARY});
                border-radius: 3px;
            }}
        """)
        layout.addWidget(self.progress_bar)
        
        # Controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 4, 0, 0)
        
        btn_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {C.TEXT_SECONDARY};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: {R.SM}px;
                padding: 4px 12px;
                font-size: {T.SIZE_XS}px;
                font-weight: {T.WEIGHT_SEMIBOLD};
            }}
            QPushButton:hover {{
                background-color: {C.BG_DARK};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.TEXT_SECONDARY};
            }}
            QPushButton:disabled {{
                color: {C.TEXT_DISABLED};
                border: 1px solid {C.BORDER_SUBTLE};
            }}
        """
        
        self.pause_btn = QPushButton("RESUME" if task_info.is_paused else "PAUSE")
        self.pause_btn.setStyleSheet(btn_style)
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setEnabled(task_info.pause_hook is not None)
        self.pause_btn.clicked.connect(self._on_pause)
        
        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setStyleSheet(btn_style.replace(C.TEXT_SECONDARY, C.ERROR_LIGHT).replace(C.TEXT_PRIMARY, C.ERROR_BRIGHT))
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setEnabled(task_info.cancel_hook is not None)
        self.cancel_btn.clicked.connect(self._on_cancel)
        
        controls_layout.addStretch()
        controls_layout.addWidget(self.pause_btn)
        controls_layout.addWidget(self.cancel_btn)
        layout.addLayout(controls_layout)

    def update_ui(self):
        self.status_label.setText(self.task_info.status.upper())
        self.progress_bar.setValue(self.task_info.progress)
        self.pause_btn.setText("RESUME" if self.task_info.is_paused else "PAUSE")
        if self.task_info.status.lower() in ["completed", "failed", "error", "cancelled"]:
            self.pause_btn.setEnabled(False)
            self.cancel_btn.setEnabled(False)

    def _on_pause(self):
        task_registry.toggle_pause_task(self.task_info.task_id)

    def _on_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        task_registry.cancel_task(self.task_info.task_id)

class TaskManagerDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Global Task Manager", parent)
        self.setObjectName("TaskManagerDock")
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)
        
        # Premium Dock Styling
        self.setStyleSheet(f"""
            QDockWidget {{
                color: {C.TEXT_PRIMARY};
                font-weight: {T.WEIGHT_BOLD};
                titlebar-close-icon: url(close.png);
                titlebar-normal-icon: url(undock.png);
            }}
            QDockWidget::title {{
                background: {C.BG_SURFACE};
                padding: 10px;
                border-bottom: 1px solid {C.BORDER_DEFAULT};
            }}
        """)
        
        self.container = QWidget()
        self.container.setStyleSheet(f"background-color: {C.BG_PRIMARY};")
        self.main_layout = QVBoxLayout(self.container)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(10)
        
        self.task_list = QListWidget()
        self.task_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        from PySide6.QtWidgets import QListView
        self.task_list.setFlow(QListView.Flow.LeftToRight)
        self.task_list.setWrapping(True)
        self.task_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.task_list.setSpacing(10)
        
        self.task_list.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                border: none;
                outline: none;
            }}
            QListWidget::item {{
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                border: none;
                background: {C.BG_DARKER};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_DEFAULT};
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {C.TEXT_SECONDARY};
            }}
            QScrollBar:horizontal {{
                border: none;
                background: {C.BG_DARKER};
                height: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal {{
                background: {C.BORDER_DEFAULT};
                min-width: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {C.TEXT_SECONDARY};
            }}
        """)
        self.main_layout.addWidget(self.task_list)
        
        self.setWidget(self.container)
        
        self._item_map = {}  # task_id -> QListWidgetItem
        
        # Connect signals
        task_registry.task_added.connect(self.add_task)
        task_registry.task_removed.connect(self.remove_task)
        task_registry.task_updated.connect(self.update_task)
        
        # Load existing
        for task in task_registry.get_all_tasks():
            self.add_task(task)

    def add_task(self, task_info: TaskInfo):
        item = QListWidgetItem(self.task_list)
        widget = TaskItemWidget(task_info)
        
        # Add a tiny bit of extra height margin for spacing between cards
        size = widget.sizeHint()
        item.setSizeHint(QSize(size.width(), size.height() + 10))
        
        self.task_list.addItem(item)
        self.task_list.setItemWidget(item, widget)
        self._item_map[task_info.task_id] = item

    def remove_task(self, task_id: str):
        if task_id in self._item_map:
            item = self._item_map.pop(task_id)
            row = self.task_list.row(item)
            self.task_list.takeItem(row)

    def update_task(self, task_id: str):
        if task_id in self._item_map:
            item = self._item_map[task_id]
            widget: TaskItemWidget = self.task_list.itemWidget(item)
            if widget:
                widget.update_ui()
