from PySide6.QtCore import QPoint, Qt, QMimeData, QSize, Signal
from PySide6.QtGui import QDrag, QPixmap, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class KanbanCard(QWidget):
    """
    Visual representation of a task in the Kanban board.
    Accepts user drops for assignment.
    """

    assign_requested = Signal(int, str)  # task_id, username

    def __init__(self, task_data, inherit_app_theme: bool = False, parent=None):
        super().__init__(parent)
        self.task_data = task_data
        self.task_id = task_data.get("id")
        self.inherit_app_theme = bool(inherit_app_theme)
        self.setAcceptDrops(True)
        self.setup_ui()

    def _set_card_style(self, highlight: bool = False):
        # We ignore inherit_app_theme here to ensure the premium look is preserved
        status = self.task_data.get("status", "").upper()
        accent = "#2C2C34"
        if status in ["DONE", "APPROVED", "FINAL"]: accent = "#5FBF8F"
        elif status in ["WIP", "IN PROGRESS", "IP"]: accent = "#D9A441"
        elif status in ["REVIEW", "SENT FOR REVIEW"]: accent = "#3EA8BF"
        elif status in ["RETAKE", "SI"]: accent = "#D9635F"
            
        if highlight:
            self.setStyleSheet(
                f"""
                QWidget {{
                    background-color: #26262D;
                    border-radius: 8px;
                    border: 1px solid {accent};
                }}
                QLabel {{ border: none; background: transparent; }}
                """
            )
        else:
            self.setStyleSheet(
                f"""
                QWidget {{
                    background-color: #26262D;
                    border-radius: 8px;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-left: 5px solid {accent};
                }}
                QWidget:hover {{
                    background-color: #26262D;
                    border: 1px solid rgba(255, 255, 255, 0.2);
                    border-left: 5px solid {accent};
                }}
                QLabel {{ border: none; background: transparent; }}
                """
            )
            
        # Add drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        self._set_card_style()

        header_layout = QHBoxLayout()
        shot_code = self.task_data.get("shot_code", "UNKNOWN")
        lbl_shot = QLabel(f"{shot_code}")
        lbl_shot.setStyleSheet("font-weight: 800; font-size: 11px; color: #E8E6E1;")
        header_layout.addWidget(lbl_shot)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        task_name = self.task_data.get("task_name", "Task")
        lbl_task = QLabel(task_name)
        lbl_task.setWordWrap(True)
        lbl_task.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 11px; line-height: 1.4;")
        layout.addWidget(lbl_task)

        layout.addSpacing(6)

        footer_layout = QHBoxLayout()
        self.assignee = self.task_data.get("assignee", "")

        if self.assignee:
            color_hash = sum(ord(c) for c in self.assignee)
            hue = color_hash % 360
            self.lbl_artist = QLabel(self.assignee[:2].upper())
            self.lbl_artist.setFixedSize(22, 22)
            self.lbl_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_artist.setToolTip(f"Assigned to: {self.assignee}")
            self.lbl_artist.setStyleSheet(
                f"""
                background-color: hsla({hue}, 60%, 40%, 1.0);
                color: white;
                border-radius: 11px;
                font-weight: bold;
                font-size: 9px;
                padding: 0;
                """
            )
            
            lbl_name = QLabel(self.assignee.split(" ")[0]) # First name only
            lbl_name.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 10px; font-weight: 600;")
            
            footer_layout.addWidget(self.lbl_artist)
            footer_layout.addWidget(lbl_name)
        else:
            self.lbl_artist = QLabel("U")
            self.lbl_artist.setFixedSize(22, 22)
            self.lbl_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_artist.setToolTip("Unassigned (Drag artist here)")
            self.lbl_artist.setStyleSheet(
                """
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.4);
                border-radius: 11px;
                border: 1px dashed rgba(255,255,255,0.2);
                font-size: 10px;
                """
            )
            footer_layout.addWidget(self.lbl_artist)

        footer_layout.addStretch()

        status = self.task_data.get("status", "").upper()
        
        # Style status as a nice pill
        status_accent = "#2C2C34"
        if status in ["DONE", "APPROVED", "FINAL"]: status_accent = "rgba(95, 191, 143, 0.2)"
        elif status in ["WIP", "IN PROGRESS", "IP"]: status_accent = "rgba(255, 214, 0, 0.2)"
        elif status in ["REVIEW", "SENT FOR REVIEW"]: status_accent = "rgba(0, 229, 255, 0.2)"
        elif status in ["RETAKE", "SI"]: status_accent = "rgba(255, 23, 68, 0.2)"
        
        text_accent = status_accent.replace("0.2", "1.0") if "0.2" in status_accent else "#E8E6E1"
            
        lbl_status = QLabel(status)
        lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_status.setStyleSheet(f"""
            background-color: {status_accent};
            color: {text_accent};
            border-radius: 10px;
            padding: 4px 10px;
            font-size: 10px;
            font-weight: 900;
        """)
        
        footer_layout.addWidget(lbl_status)
        layout.addLayout(footer_layout)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-slate-user"):
            event.accept()
            self._set_card_style(highlight=True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_card_style()
        event.accept()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-slate-user"):
            username_bytes = event.mimeData().data("application/x-slate-user")
            username = str(username_bytes, "utf-8")
            self.assign_requested.emit(self.task_id, username)
            event.accept()
            self._set_card_style()
        else:
            event.ignore()


class KanbanColumn(QListWidget):
    """
    A single column in the Kanban board (e.g., "To Do").
    Accepts drops from other columns.
    """

    task_dropped = Signal(int, str)  # task_id, new_status_key
    task_double_clicked = Signal(int)  # task_id
    task_assigned = Signal(int, str)  # task_id, username

    def __init__(self, title, status_key, inherit_app_theme: bool = False, parent=None):
        super().__init__(parent)
        self.title = title
        self.status_key = status_key
        self.inherit_app_theme = bool(inherit_app_theme)

        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSpacing(8)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)

        # Force QScrollBar styling to avoid the checkerboard
        self.setStyleSheet(
            """
            QListWidget {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                padding: 0px;
                margin-bottom: 12px;
            }
            QListWidget::item:selected {
                background: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: rgba(255, 255, 255, 0.02);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.1);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.2);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            """
        )

    def on_item_double_clicked(self, item):
        widget = self.itemWidget(item)
        if widget and hasattr(widget, "task_id"):
            self.task_double_clicked.emit(widget.task_id)

    def clear(self):
        """Properly delete attached widgets to prevent C++ memory leaks during rebuilds."""
        for i in range(self.count()):
            item = self.item(i)
            if item:
                widget = self.itemWidget(item)
                if widget:
                    widget.deleteLater()
        super().clear()

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if not item:
            return

        widget = self.itemWidget(item)
        if not widget:
            return

        mime = QMimeData()
        mime.setText(str(widget.task_id))

        drag = QDrag(self)
        drag.setMimeData(mime)

        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasText():
            try:
                task_id = int(event.mimeData().text())
                self.task_dropped.emit(task_id, self.status_key)
                event.accept()
            except ValueError:
                event.ignore()
        else:
            event.ignore()


class KanbanBoard(QWidget):
    """
    Main widget hosting multiple KanbanColumn widgets.
    """

    status_changed = Signal(int, str)  # task_id, new_status
    task_double_clicked = Signal(int)  # task_id
    task_assigned = Signal(int, str)  # task_id, username

    def __init__(self, inherit_app_theme: bool = False, parent=None):
        super().__init__(parent)
        self.columns = {}
        self.inherit_app_theme = bool(inherit_app_theme)
        self.setup_ui()

    def setup_ui(self):
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(15)

        self.column_defs = [
            ("To Do", "Not Started"),
            ("In Progress", "In Progress"),
            ("Done", "Final"),
        ]

        for title, key in self.column_defs:
            col_container = QWidget()
            # Force background styling
            col_container.setStyleSheet("""
                QWidget {
                    background-color: #16161A;
                    border-radius: 12px;
                    border: 1px solid rgba(255, 255, 255, 0.05);
                }
            """)
            col_layout = QVBoxLayout(col_container)
            col_layout.setContentsMargins(12, 16, 12, 12)
            col_layout.setSpacing(12)

            lbl_title = QLabel(f"{title}")
            lbl_title.setAlignment(Qt.AlignmentFlag.AlignLeft)
            lbl_title.setStyleSheet("font-weight: 900; font-size: 14px; color: rgba(255,255,255,0.9); padding-left: 4px; background: transparent; border: none; letter-spacing: 0.5px;")
            col_layout.addWidget(lbl_title)

            kanban_list = KanbanColumn(title, key, inherit_app_theme=self.inherit_app_theme)
            
            # Helper to update count
            def update_count(count_label, title_text, list_widget):
                def _update():
                    count = list_widget.count()
                    count_label.setText(f"{title_text}  <span style='color: rgba(255,255,255,0.4); font-size: 12px;'>{count}</span>")
                return _update
                
            update_func = update_count(lbl_title, title, kanban_list)
            # Connect model changes to count update
            kanban_list.model().rowsInserted.connect(lambda *args: update_func())
            kanban_list.model().rowsRemoved.connect(lambda *args: update_func())
            # Initial call
            update_func()
            
            kanban_list.task_dropped.connect(self.handle_drop)
            kanban_list.task_double_clicked.connect(self.task_double_clicked.emit)
            kanban_list.task_assigned.connect(self.task_assigned.emit)
            col_layout.addWidget(kanban_list)

            self.columns[key] = kanban_list
            self.main_layout.addWidget(col_container)

    def handle_drop(self, task_id, new_status):
        self.status_changed.emit(task_id, new_status)

    def clear(self):
        for col in self.columns.values():
            col.clear()

    def add_task(self, task_data):
        status = task_data.get("status", "Not Started")

        if status in ["Not Started", "Pending", "Ready"]:
            target_col = self.columns.get("Not Started")
        elif status in ["In Progress", "IP", "Review"]:
            target_col = self.columns.get("In Progress")
        elif status in ["Final", "Approved", "Done", "CBB"]:
            target_col = self.columns.get("Final")
        else:
            target_col = self.columns.get("Not Started")

        if not target_col:
            return

        item = QListWidgetItem()
        item.setSizeHint(QSize(200, 100))
        card = KanbanCard(task_data, inherit_app_theme=self.inherit_app_theme)
        card.assign_requested.connect(self.task_assigned.emit)

        target_col.addItem(item)
        target_col.setItemWidget(item, card)
