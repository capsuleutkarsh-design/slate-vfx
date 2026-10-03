"""Dialogs of the Stock Viewer: the typed Clear Library confirmation."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from ....core.controls import make_button, set_default_button
from slate.core.infra.gate import Gate

CONFIRM_WORD = "CLEAR"


class ClearLibraryDialog(QDialog):
    """
    Emptying the library is for everybody and cannot be undone, so it asks
    for the word CLEAR to be typed, says how many assets it removes, and
    leaves Cancel as what Enter does until the word is there (MED-014).
    """

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clear the stock library")
        self.setModal(True)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        heading = QLabel("Remove the only asset from the stock library?" if count == 1 else
                         f"Remove all {count:,} assets from the stock library?")
        heading.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {Gate.TEXT};")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        body = QLabel(
            "This empties the library for everybody in the studio, with the thumbnails and "
            "proxies it made. Favourites, studio picks and the folders Rescan looks in go "
            "too. The source files stay where they are.\n\nThis cannot be undone.")
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(body)

        prompt = QLabel(f"Type {CONFIRM_WORD} to confirm:")
        layout.addWidget(prompt)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(CONFIRM_WORD)
        self.edit.textChanged.connect(self._update)
        layout.addWidget(self.edit)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.btn_cancel = make_button("Cancel", "secondary", on_click=self.reject)
        self.btn_clear = make_button("Clear library", "danger", on_click=self.accept)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_clear)
        layout.addLayout(buttons)
        set_default_button(self, self.btn_cancel)
        self._update()

    def confirmed(self) -> bool:
        # The word, however it is typed: "clear" left the button grey with
        # no hint why (MED2-034).
        return self.edit.text().strip().upper() == CONFIRM_WORD

    def _update(self):
        self.btn_clear.setEnabled(self.confirmed())

    def accept(self):
        if self.confirmed():
            super().accept()
