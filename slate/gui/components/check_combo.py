"""A dropdown whose entries are tick boxes: pick one or more, see them listed when closed."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox


class CheckComboBox(QComboBox):
    """
    Looks like any other dropdown. Clicking an entry ticks or unticks it and
    leaves the list open, so several can be chosen; closed, it shows the
    ticked entries joined with commas.
    """

    def __init__(self, parent=None, placeholder="Choose…"):
        super().__init__(parent)
        self._placeholder = placeholder
        self.setModel(QStandardItemModel(self))
        # Editable only so the closed box can show our own text; typing is off.
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setStyleSheet("background: transparent; border: none;")
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)
        self.model().dataChanged.connect(self._update_text)
        self._update_text()

    # ----------------------------------------------------------------- items
    def add_items(self, texts):
        for text in texts:
            item = QStandardItem(str(text))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            self.model().appendRow(item)
        self._update_text()

    def _items(self):
        return [self.model().item(row) for row in range(self.model().rowCount())]

    def checked(self):
        """The ticked entries, in list order."""
        return [item.text() for item in self._items()
                if item.checkState() == Qt.CheckState.Checked]

    def set_checked(self, names):
        """Tick exactly these (compared case-insensitively)."""
        wanted = {str(n).strip().lower() for n in (names or [])}
        for item in self._items():
            state = Qt.CheckState.Checked if item.text().strip().lower() in wanted else Qt.CheckState.Unchecked
            item.setCheckState(state)
        self._update_text()

    # ------------------------------------------------------------- behaviour
    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease:
            self.showPopup()
            return True
        if obj is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            item = self.model().itemFromIndex(self.view().indexAt(event.position().toPoint()))
            if item is not None:
                ticked = item.checkState() == Qt.CheckState.Checked
                item.setCheckState(Qt.CheckState.Unchecked if ticked else Qt.CheckState.Checked)
            return True            # keep the list open for the next tick
        return super().eventFilter(obj, event)

    def hidePopup(self):
        super().hidePopup()
        self._update_text()

    def _update_text(self, *_args):
        chosen = self.checked()
        text = ", ".join(chosen) if chosen else self._placeholder
        if self.lineEdit() is not None:
            self.lineEdit().setText(text)
            self.lineEdit().setCursorPosition(0)
        self.setToolTip(text)
