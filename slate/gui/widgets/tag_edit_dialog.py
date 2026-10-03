"""
Editing one stock asset's tags (MED-031): add with completion from the tags
already in use, remove, save. Enter in the field adds the tag; Enter
elsewhere saves.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCompleter, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QVBoxLayout,
)

from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button, set_default_button


class TagEditDialog(QDialog):
    def __init__(self, parent=None, current_tags=None, available_tags=None, asset_name=""):
        super().__init__(parent)
        # Which asset: the window said only "Edit tags" (MED2-039).
        self.setWindowTitle(f"Edit tags - {asset_name}" if asset_name else "Edit tags")
        self.setMinimumWidth(400)
        from slate.core.domain.stock_search import normalise_tags
        self.tags = normalise_tags(list(current_tags or []))
        self.available_tags = list(available_tags or [])
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        caption = QLabel("Tags")
        caption.setStyleSheet(f"color: {Gate.TEXT_2}; font-weight: 600;")
        layout.addWidget(caption)
        self.list_tags = QListWidget()
        self.list_tags.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_tags.itemSelectionChanged.connect(self._update_buttons)
        # Delete removes the selected tags, like the button.
        self.list_tags.installEventFilter(self)
        layout.addWidget(self.list_tags)

        self.btn_remove = make_button("Remove selected", "secondary", icon="minus",
                                      on_click=self.remove_tag, tooltip="Delete")
        layout.addWidget(self.btn_remove, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(6)
        layout.addWidget(QLabel("Add a tag"))
        input_layout = QHBoxLayout()
        self.txt_input = QLineEdit()
        self.txt_input.setPlaceholderText("Type a tag and press Enter…")
        # Enter with text in the field adds it; it must not also save and close.
        self.txt_input.installEventFilter(self)
        completer = QCompleter(self.available_tags, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.txt_input.setCompleter(completer)
        input_layout.addWidget(self.txt_input, 1)
        self.btn_add = make_button("Add", "secondary", icon="plus", on_click=self.add_tag)
        input_layout.addWidget(self.btn_add)
        layout.addLayout(input_layout)

        layout.addSpacing(8)
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = make_button("Cancel", "secondary", on_click=self.reject)
        self.btn_save = make_button("Save & close", "primary", on_click=self.accept)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_save)
        layout.addLayout(btn_box)
        set_default_button(self, self.btn_save)
        self.update_list()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if (obj is getattr(self, "txt_input", None) and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and self.txt_input.text().strip()):
            self.add_tag()
            return True
        if (obj is self.list_tags and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Delete and self.list_tags.selectedItems()):
            self.remove_tag()
            return True
        return super().eventFilter(obj, event)

    def _update_buttons(self):
        self.btn_remove.setEnabled(bool(self.list_tags.selectedItems()))

    def update_list(self):
        self.list_tags.clear()
        for t in self.tags:
            self.list_tags.addItem(t)
        self._update_buttons()

    def add_tag(self):
        from slate.core.domain.stock_search import normalise_tags
        for tag in normalise_tags(self.txt_input.text()):
            if tag.lower() not in {t.lower() for t in self.tags}:
                self.tags.append(tag)
        self.update_list()
        self.txt_input.clear()

    def remove_tag(self):
        rows = sorted({self.list_tags.row(i) for i in self.list_tags.selectedItems()}, reverse=True)
        for row in rows:
            self.tags.pop(row)
        self.update_list()

    def get_tags(self):
        return list(self.tags)
