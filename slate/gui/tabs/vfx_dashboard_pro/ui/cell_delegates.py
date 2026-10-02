"""
Editors for the grid's typed columns.

Each opens on the value that is stored (the model's EditRole), and writes
nothing back if it is closed without a change - a stray double-click used to
wipe a cell. Numbers and dates get an editor that cannot hold anything else:
'High' typed into Priority became 0 (Urgent), '1,200' frames became 0, and
'next friday' sat among the target dates.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox, QStyledItemDelegate

from slate.core.domain import shot_status
from .date_fields import OptionalDateEdit


class TextDelegate(QStyledItemDelegate):
    """Plain text: the stored text in, and only a real change out."""

    def createEditor(self, parent, option, index):
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        editor.setText(str(index.data(Qt.ItemDataRole.EditRole) or ""))

    def setModelData(self, editor, model, index):
        new_value = editor.text()
        old_value = str(index.data(Qt.ItemDataRole.EditRole) or "")
        if " ".join(new_value.split()) == " ".join(old_value.split()):
            return
        model.setData(index, new_value.strip(), Qt.ItemDataRole.EditRole)


class PriorityDelegate(QStyledItemDelegate):
    """Urgent / High / Normal / Low, stored as 0-3."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        for value, label in shot_status.priorities():
            combo.addItem(label, value)
        return combo

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findData(value)
        editor.setCurrentIndex(idx if idx >= 0 else max(0, editor.count() - 1))

    def setModelData(self, editor, model, index):
        new_value = editor.currentData()
        if new_value is None or new_value == index.data(Qt.ItemDataRole.EditRole):
            return
        model.setData(index, int(new_value), Qt.ItemDataRole.EditRole)


class FramesDelegate(QStyledItemDelegate):
    """A whole number of frames, never negative."""

    def createEditor(self, parent, option, index):
        spin = QSpinBox(parent)
        spin.setRange(0, 1_000_000)
        spin.setGroupSeparatorShown(True)
        spin.setSpecialValueText("-")
        return spin

    def setEditorData(self, editor, index):
        try:
            editor.setValue(int(index.data(Qt.ItemDataRole.EditRole) or 0))
        except (TypeError, ValueError):
            editor.setValue(0)

    def setModelData(self, editor, model, index):
        editor.interpretText()
        new_value = editor.value()
        try:
            old_value = int(index.data(Qt.ItemDataRole.EditRole) or 0)
        except (TypeError, ValueError):
            old_value = None
        if new_value == old_value:
            return
        model.setData(index, new_value, Qt.ItemDataRole.EditRole)


class TargetDateDelegate(QStyledItemDelegate):
    """A date or no date; text that is not a date stays until one is picked."""

    def createEditor(self, parent, option, index):
        return OptionalDateEdit(parent=parent)

    def setEditorData(self, editor, index):
        editor.set_value(index.data(Qt.ItemDataRole.EditRole))

    def setModelData(self, editor, model, index):
        if not editor.is_changed():
            return
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class ShotTypeDelegate(QStyledItemDelegate):
    """The studio's shot types; a stored type that is not on the list stays offered."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        current = str(index.data(Qt.ItemDataRole.EditRole) or "")
        for value in shot_status.shot_types_with(current, include_blank=True):
            combo.addItem(value if value else "(none)", value)
        return combo

    def setEditorData(self, editor, index):
        current = str(index.data(Qt.ItemDataRole.EditRole) or "")
        idx = editor.findData(current)
        if idx < 0:
            idx = editor.findText(current, Qt.MatchFlag.MatchFixedString)
        editor.setCurrentIndex(max(0, idx))

    def setModelData(self, editor, model, index):
        new_value = editor.currentData()
        if new_value is None:
            new_value = editor.currentText()
        if str(new_value) == str(index.data(Qt.ItemDataRole.EditRole) or ""):
            return
        model.setData(index, new_value, Qt.ItemDataRole.EditRole)
