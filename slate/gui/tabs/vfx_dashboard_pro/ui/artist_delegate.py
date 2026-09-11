from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QComboBox
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QFontMetrics

class ArtistDelegate(QStyledItemDelegate):
    """
    Delegate for inline editing of Assigned Artist in Shot Table.
    Provides searchable dropdown of all active users.
    """

    def __init__(self, get_users_callback=None, parent=None):
        super().__init__(parent)
        self.get_users_callback = get_users_callback

    def _get_users(self):
        if callable(self.get_users_callback):
            users = self.get_users_callback()
            if users:
                return sorted(list(set(str(u).strip() for u in users if str(u).strip())))
        return ["Artist"]

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem("Unassigned", "")
        for user in self._get_users():
            combo.addItem(user, user)

        combo.setStyleSheet("""
            QComboBox {
                background-color: #16323A;
                color: #E8E6E1;
                border: 1px solid #3EA8BF;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #16323A;
                color: #E8E6E1;
                selection-background-color: #3EA8BF;
            }
        """)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        current_value = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        if current_value in ["-", ""]:
            editor.setCurrentIndex(0)
        else:
            idx = editor.findText(current_value, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                editor.setCurrentIndex(idx)
            else:
                editor.setEditText(current_value)

    def setModelData(self, editor: QComboBox, model, index):
        selected_text = editor.currentText().strip()
        new_value = "" if selected_text in ["Unassigned", "-"] else selected_text
        model.setData(index, new_value, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)
