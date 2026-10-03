"""
The dashboard's board: one column per shot status, a card per shot.

It used to have three buckets (To Do / In Progress / Done) that SENT FOR
REVIEW, READY, YTS, OMIT and CBB all fell into as "To Do", and a drop wrote
statuses nothing else used ("Ready", "Final") or quietly turned a RETAKE into
WIP. Now each column is a real status in workflow order (plus any other status
the project has, and "No status"), a card sits in the column of its own status,
and dropping it writes exactly that column's status.

The column counts used to be wired in a loop that captured the loop variable,
so every column's changes updated the last column's label ("To Do 0" over a
full column). Each column now owns its label.
"""

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slate.core.infra.gate import Gate

USER_MIME = "application/x-slate-user"
TASK_MIME = "application/x-slate-task"


class KanbanCard(QFrame):
    """One shot on the board. Accepts a person dropped on it (assignment)."""

    assign_requested = Signal(int, str)  # task_id, username

    WIDTH = 220

    def __init__(self, task_data, inherit_app_theme: bool = False, parent=None, editable=True):
        super().__init__(parent)
        self.task_data = task_data
        self.task_id = task_data.get("id")
        self.inherit_app_theme = bool(inherit_app_theme)
        self.editable = bool(editable)
        self.setObjectName("kanbanCard")
        self.setAcceptDrops(self.editable)
        self.setup_ui()

    def _accent(self) -> str:
        return Gate.status_color(self.task_data.get("status", ""))

    def _set_card_style(self, highlight: bool = False):
        accent = self._accent()
        border = accent if highlight else Gate.LINE
        self.setStyleSheet(f"""
            QFrame#kanbanCard {{
                background-color: {Gate.RAISED};
                border-radius: {Gate.RADIUS_LG}px;
                border: 1px solid {border};
                border-left: 4px solid {accent};
            }}
            QFrame#kanbanCard QLabel {{ border: none; background: transparent; }}
        """)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self._set_card_style()
        inner = self.WIDTH - 40

        shot_code = str(self.task_data.get("shot_code", "") or "")
        reel = str(self.task_data.get("reel", "") or "")
        lbl_shot = QLabel()
        lbl_shot.setObjectName("cardShot")
        font = lbl_shot.font()
        font.setBold(True)
        lbl_shot.setFont(font)
        lbl_shot.setText(QFontMetrics(font).elidedText(shot_code, Qt.TextElideMode.ElideRight, inner))
        lbl_shot.setToolTip(f"{shot_code} ({reel})" if reel else shot_code)
        lbl_shot.setStyleSheet(f"color: {Gate.TEXT};")
        layout.addWidget(lbl_shot)
        self.lbl_shot = lbl_shot

        if reel:
            lbl_reel = QLabel(reel)
            lbl_reel.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_XS}px;")
            layout.addWidget(lbl_reel)

        task_name = str(self.task_data.get("task_name", "") or "").strip()
        if task_name:
            # Up to two whole lines, the second ending in an ellipsis - never a
            # line cut through the middle of its letters.
            lbl_task = QLabel(self.two_lines(task_name, QFontMetrics(self.font()), inner))
            lbl_task.setObjectName("cardTask")
            lbl_task.setToolTip(task_name)
            lbl_task.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: {Gate.SIZE_SM}px;")
            layout.addWidget(lbl_task)

        layout.addSpacing(4)
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(6)
        self.assignee = str(self.task_data.get("assignee", "") or "")

        self.lbl_artist = QLabel()
        self.lbl_artist.setFixedSize(22, 22)
        self.lbl_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self.assignee:
            initials = "".join(part[:1] for part in self.assignee.split()[:2]).upper() or self.assignee[:2].upper()
            self.lbl_artist.setText(initials)
            self.lbl_artist.setToolTip(f"Assigned to {self.assignee}")
            self.lbl_artist.setStyleSheet(
                f"background-color: {Gate.ACCENT_SURFACE}; color: {Gate.ACCENT}; border-radius: 11px;"
                f" font-weight: 700; font-size: 9px;")
            name = QLabel()
            name_font = name.font()
            name.setText(QFontMetrics(name_font).elidedText(self.assignee, Qt.TextElideMode.ElideRight, inner - 40))
            name.setToolTip(self.assignee)
            name.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: {Gate.SIZE_XS}px;")
            self.lbl_name = name
            footer_layout.addWidget(self.lbl_artist)
            footer_layout.addWidget(name, 1)
        else:
            self.lbl_artist.setText("–")
            self.lbl_artist.setToolTip("Nobody assigned" + (" - drag a person here" if self.editable else ""))
            self.lbl_artist.setStyleSheet(
                f"color: {Gate.TEXT_DIM}; border-radius: 11px; border: 1px dashed {Gate.LINE}; font-size: 10px;")
            self.lbl_name = QLabel("Unassigned")
            self.lbl_name.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_XS}px;")
            footer_layout.addWidget(self.lbl_artist)
            footer_layout.addWidget(self.lbl_name, 1)
        layout.addLayout(footer_layout)

        if self.task_data.get("modified"):
            pending = QLabel("Not saved yet")
            pending.setStyleSheet(f"color: {Gate.WARN}; font-size: {Gate.SIZE_XS}px; font-weight: 600;")
            layout.addWidget(pending)

    @staticmethod
    def two_lines(text: str, fm, width: int) -> str:
        """The text in at most two lines of `width`, the second elided."""
        words = " ".join(str(text or "").split()).split(" ")
        first = ""
        while words:
            trial = (first + " " + words[0]).strip()
            if fm.horizontalAdvance(trial) > width:
                break
            first = trial
            words.pop(0)
        if not first:            # one long word: elide it on the first line
            return fm.elidedText(" ".join(words), Qt.TextElideMode.ElideRight, width)
        if not words:
            return first
        rest = fm.elidedText(" ".join(words), Qt.TextElideMode.ElideRight, width)
        return first + "\n" + rest

    def dragEnterEvent(self, event):
        if self.editable and event.mimeData().hasFormat(USER_MIME):
            event.accept()
            self._set_card_style(highlight=True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_card_style()
        event.accept()

    def dropEvent(self, event):
        if self.editable and event.mimeData().hasFormat(USER_MIME):
            username = str(event.mimeData().data(USER_MIME), "utf-8")
            self.assign_requested.emit(self.task_id, username)
            event.accept()
            self._set_card_style()
        else:
            event.ignore()


class KanbanColumn(QListWidget):
    """The cards of one status. Accepts cards dropped from other columns."""

    task_dropped = Signal(int, str)  # task_id, new_status_key
    task_double_clicked = Signal(int)  # task_id
    task_assigned = Signal(int, str)  # task_id, username

    def __init__(self, title, status_key, inherit_app_theme: bool = False, parent=None):
        super().__init__(parent)
        self.title = title
        self.status_key = status_key
        self.inherit_app_theme = bool(inherit_app_theme)
        self.editable = True

        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSpacing(4)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.setStyleSheet(
            "QListWidget { background-color: transparent; border: none; outline: none; }"
            "QListWidget::item, QListWidget::item:selected { background: transparent; padding: 0px; }"
        )

    def set_editable(self, editable: bool):
        self.editable = bool(editable)
        self.setDragEnabled(self.editable)
        self.setAcceptDrops(self.editable)

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
        if not self.editable:
            return
        item = self.currentItem()
        if not item:
            return
        widget = self.itemWidget(item)
        if not widget:
            return
        mime = QMimeData()
        mime.setData(TASK_MIME, str(widget.task_id).encode("utf-8"))
        mime.setText(str(widget.task_id))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if self.editable and event.mimeData().hasFormat(TASK_MIME):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self.editable and event.mimeData().hasFormat(TASK_MIME):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if self.editable and event.mimeData().hasFormat(TASK_MIME):
            try:
                task_id = int(bytes(event.mimeData().data(TASK_MIME)).decode("utf-8"))
            except ValueError:
                event.ignore()
                return
            # The board is rebuilt from the data; never let Qt move the item.
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.accept()
            self.task_dropped.emit(task_id, self.status_key)
        else:
            event.ignore()

    def contextMenuEvent(self, event):
        """'Move to' another column - the board without a mouse drag."""
        item = self.itemAt(event.pos())
        widget = self.itemWidget(item) if item else None
        targets = getattr(self, "move_targets", None)
        if not self.editable or widget is None or targets is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        move = menu.addMenu("Move to")
        for key, title in targets():
            if key != self.status_key:
                move.addAction(title, lambda k=key, t=widget.task_id: self.task_dropped.emit(t, k))
        menu.exec(event.globalPos())


class _ColumnFrame(QFrame):
    """A column: its heading (name and count, click to fold) and its cards."""

    def __init__(self, key, title, colour, board, collapsed=False):
        super().__init__(board)
        self.key = key
        self.title = title
        self.colour = colour
        self.setObjectName("kanbanColumn")
        self.setStyleSheet(
            f"QFrame#kanbanColumn {{ background-color: {Gate.PANEL}; border-radius: {Gate.RADIUS_LG}px;"
            f" border: 1px solid {Gate.LINE_SOFT}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(8)
        self.header = QPushButton()
        self.header.setObjectName("kanbanColumnTitle")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setToolTip("Click to fold or unfold this column")
        self.header.setStyleSheet(
            f"QPushButton#kanbanColumnTitle {{ text-align: left; border: none; background: transparent;"
            f" color: {colour}; font-weight: 700; font-size: {Gate.SIZE_MD}px; padding: 2px 4px; }}"
            f"QPushButton#kanbanColumnTitle:hover {{ color: {Gate.TEXT}; }}")
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)
        self.list = KanbanColumn(title, key)
        layout.addWidget(self.list, 1)
        self.list.model().rowsInserted.connect(self.update_count)
        self.list.model().rowsRemoved.connect(self.update_count)
        self.list.model().modelReset.connect(self.update_count)
        self.collapsed = False
        self.set_collapsed(collapsed)
        # A folded column (OMIT starts folded) still takes a dropped card.
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self.list.editable and event.mimeData().hasFormat(TASK_MIME):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        self.list.dropEvent(event)

    def update_count(self, *args):
        count = self.list.count()
        arrow = "▸" if self.collapsed else "▾"
        self.header.setText(f"{arrow}  {self.title}   {count}")

    def toggle(self):
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool):
        self.collapsed = bool(collapsed)
        self.list.setVisible(not self.collapsed)
        width = 150 if self.collapsed else KanbanCard.WIDTH + 40
        self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.update_count()


class KanbanBoard(QWidget):
    """Hosts one column per status, side by side, scrolling sideways when needed."""

    status_changed = Signal(int, str)  # task_id, new status (the column's key)
    task_double_clicked = Signal(int)  # task_id
    task_assigned = Signal(int, str)  # task_id, username

    def __init__(self, inherit_app_theme: bool = False, parent=None):
        super().__init__(parent)
        self.columns = {}
        self._frames = {}
        self._column_keys = []
        self._editable = True
        self._collapsed = {"OMIT"}
        self.inherit_app_theme = bool(inherit_app_theme)
        self.setup_ui()

    def setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(
            f"color: {Gate.TEXT_2}; background: {Gate.RAISED}; padding: 6px 10px;"
            f" border-bottom: 1px solid {Gate.LINE};")
        self.notice.hide()
        outer.addWidget(self.notice)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.strip = QWidget()
        self.main_layout = QHBoxLayout(self.strip)
        self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.main_layout.setSpacing(10)
        self.main_layout.addStretch(1)
        self.scroll.setWidget(self.strip)
        outer.addWidget(self.scroll, 1)

    def set_columns(self, columns):
        """[(status key, title, colour)] - rebuilt only when they change."""
        keys = [c[0] for c in columns]
        if keys == self._column_keys:
            return
        for frame in self._frames.values():
            self._collapsed.discard(frame.key)
            if frame.collapsed:
                self._collapsed.add(frame.key)
            frame.list.clear()
            frame.setParent(None)
            frame.deleteLater()
        self._frames = {}
        self.columns = {}
        while self.main_layout.count():
            self.main_layout.takeAt(0)
        for key, title, colour in columns:
            frame = _ColumnFrame(key, title, colour, self.strip, collapsed=key in self._collapsed)
            frame.list.task_dropped.connect(self.handle_drop)
            frame.list.task_double_clicked.connect(self.task_double_clicked.emit)
            frame.list.set_editable(self._editable)
            frame.list.move_targets = lambda: [(k, f.title) for k, f in self._frames.items()]
            self.main_layout.addWidget(frame)
            self._frames[key] = frame
            self.columns[key] = frame.list
        self.main_layout.addStretch(1)
        self._column_keys = keys

    def set_editable(self, editable: bool, reason: str = ""):
        """Turn drag-to-move and drop-to-assign off (with the reason shown) or on."""
        self._editable = bool(editable)
        for column in self.columns.values():
            column.set_editable(self._editable)
        self.notice.setText(reason)
        self.notice.setVisible(bool(reason) and not self._editable)

    def handle_drop(self, task_id, new_status):
        self.status_changed.emit(task_id, new_status)

    def clear(self):
        for col in self.columns.values():
            col.clear()

    def column_title(self, key) -> str:
        frame = self._frames.get(key)
        return frame.header.text() if frame else ""

    def add_task(self, task_data):
        key = task_data.get("column", "")
        target_col = self.columns.get(key)
        if target_col is None:
            return
        card = KanbanCard(task_data, inherit_app_theme=self.inherit_app_theme, editable=self._editable)
        card.assign_requested.connect(self.task_assigned.emit)
        item = QListWidgetItem()
        item.setSizeHint(QSize(KanbanCard.WIDTH, card.sizeHint().height() + 4))
        target_col.addItem(item)
        target_col.setItemWidget(item, card)
