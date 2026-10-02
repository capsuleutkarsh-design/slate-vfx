from PySide6.QtWidgets import QStyledItemDelegate, QComboBox
from PySide6.QtCore import Qt


class ArtistDelegate(QStyledItemDelegate):
    """
    Inline editing of the shot's artist: a searchable list of the people work
    can be assigned to.

    When the people list cannot be read the list is just "Unassigned" - it
    used to offer a made-up "Artist", which was then saved as a real
    assignment.
    """

    UNASSIGNED = "Unassigned"

    def __init__(self, get_users_callback=None, parent=None):
        super().__init__(parent)
        self.get_users_callback = get_users_callback

    def _get_users(self):
        if callable(self.get_users_callback):
            try:
                users = self.get_users_callback() or []
            except Exception:
                users = []
            return sorted({str(u).strip() for u in users if str(u).strip()}, key=str.lower)
        return []

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem(self.UNASSIGNED, "")
        current = str(index.data(Qt.ItemDataRole.EditRole) or "").strip()
        users = self._get_users()
        for user in users:
            combo.addItem(user, user)
        if current and current.lower() not in {u.lower() for u in users}:
            # Somebody no longer on the list keeps their name until it is changed.
            combo.addItem(current, current)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        current_value = str(index.data(Qt.ItemDataRole.EditRole) or "").strip()
        if not current_value:
            editor.setCurrentIndex(0)
            return
        idx = editor.findData(current_value)
        if idx < 0:
            idx = editor.findText(current_value, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            editor.setCurrentIndex(idx)
        else:
            editor.setEditText(current_value)

    def setModelData(self, editor: QComboBox, model, index):
        selected_text = editor.currentText().strip()
        idx = editor.findText(selected_text, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            new_value = editor.itemData(idx) or ""
        elif selected_text in ("", self.UNASSIGNED, "-"):
            new_value = ""
        else:
            # Half a name typed and the editor closed: not a person, so the
            # cell keeps what it had rather than storing "Pri" as an artist.
            return
        current = str(index.data(Qt.ItemDataRole.EditRole) or "").strip()
        if new_value == current:
            return
        model.setData(index, new_value, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)
