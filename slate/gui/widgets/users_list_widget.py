"""
The people beside the board: drag one onto a card to give them the shot.

It used to list supervisors, producers and leads by first name only (two
"Pr…" users nobody could tell apart), with no counts and no search. It now
lists the people work can be assigned to (the "assignable" ability), by full
name, with how many open shots each already has, and a search box.
"""

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt
from PySide6.QtGui import QDrag, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate

USER_MIME = "application/x-slate-user"


class UserItemWidget(QWidget):
    """One person: initials, full name, and their open-shot count."""

    def __init__(self, username, display_name=None, open_shots=None, parent=None):
        super().__init__(parent)
        self.username = username
        self.display_name = display_name or username
        self.open_shots = open_shots
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        initials = "".join(p[:1] for p in self.display_name.split()[:2]).upper() or self.display_name[:2].upper()
        self.avatar = QLabel(initials)
        self.avatar.setFixedSize(28, 28)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet(
            f"QLabel {{ background-color: {Gate.ACCENT_SURFACE}; color: {Gate.ACCENT};"
            f" border-radius: 14px; font-weight: 700; font-size: 10px; }}")
        layout.addWidget(self.avatar)

        self.name_label = QLabel()
        self.name_label.setText(QFontMetrics(self.name_label.font()).elidedText(
            self.display_name, Qt.TextElideMode.ElideRight, 150))
        self.name_label.setToolTip(self.display_name)
        self.name_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        layout.addWidget(self.name_label, 1)

        if self.open_shots is not None:
            self.count_label = QLabel(str(self.open_shots))
            self.count_label.setToolTip(f"{self.open_shots} open shot(s) on this project")
            self.count_label.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_XS}px;")
            layout.addWidget(self.count_label)


class UsersListWidget(QWidget):
    """Sidebar of people who can be dragged onto a board card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._people = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QLabel("Assign by dragging")
        header.setStyleSheet(f"font-weight: 700; color: {Gate.TEXT};")
        layout.addWidget(header)
        hint = QLabel("Drag a person onto a card. Numbers are their open shots.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_XS}px;")
        layout.addWidget(hint)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a person…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.setDragEnabled(True)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.list_widget, 1)
        self.list_widget.startDrag = self.start_drag_custom

    def start_drag_custom(self, supportedActions):
        item = self.list_widget.currentItem()
        if not item:
            return
        widget = self.list_widget.itemWidget(item)
        if not widget:
            return
        mime = QMimeData()
        mime.setData(USER_MIME, widget.username.encode('utf-8'))
        drag = QDrag(self.list_widget)
        drag.setMimeData(mime)
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)

    def populate(self, users, counts=None):
        """
        users: names (or {'username', 'display_name'} dicts); counts: {name: open shots}.
        """
        counts = counts or {}
        self._people = []
        for u in users or []:
            if isinstance(u, dict):
                username = u.get('username') or u.get('display_name') or ""
                display = u.get('display_name') or username
            else:
                username = display = str(u)
            if username:
                self._people.append((username, display, counts.get(display, counts.get(username))))
        self._rebuild()

    def _rebuild(self):
        self.list_widget.clear()
        needle = self.search.text().strip().lower()
        for username, display, count in self._people:
            if needle and needle not in display.lower() and needle not in username.lower():
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(180, 40))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, UserItemWidget(username, display, count))

    def _filter(self, *_):
        self._rebuild()

    def names(self):
        return [self.list_widget.itemWidget(self.list_widget.item(i)).display_name
                for i in range(self.list_widget.count())]
