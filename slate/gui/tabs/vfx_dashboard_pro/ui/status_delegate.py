from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QComboBox
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QFontMetrics
from slate.core.domain import shot_status
from slate.core.infra.gate import Gate

from .shot_table_model import MODIFIED_ROLE


def status_qcolor(status: str) -> QColor:
    """The one status colour (Gate.status_color), as a QColor."""
    return QColor(Gate.status_color(status))


class StatusDelegate(QStyledItemDelegate):
    """
    Status cells, drawn as pills and edited with a dropdown.

    ``allowed`` is a callable returning the statuses this person may pick.
    An artist gets the states of their own work; Approved, Retake and Omit
    are verdicts and are not offered - and are refused by the database too.

    A blank cell draws no pill (a wall of grey "-" pills drowned the real
    statuses), and its editor opens on "(no status)": the dropdown used to
    open on WIP for an empty cell, so clicking into one and away wrote WIP.
    """

    NO_STATUS_TEXT = "(no status)"

    # The workflow, for anybody still reading these names from here.
    STATUS_CHOICES = list(shot_status.WORKFLOW)

    def __init__(self, parent=None, allowed=None):
        super().__init__(parent)
        self._allowed = allowed

    def paint(self, painter: QPainter, option, index):
        status_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        background = index.data(Qt.ItemDataRole.BackgroundRole)
        modified = bool(index.data(MODIFIED_ROLE))

        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif background is not None:
            painter.fillRect(option.rect, background)
        painter.restore()

        if not status_text:
            if modified:
                self._paint_modified_mark(painter, option)
            return

        painter.save()
        base_color = status_qcolor(status_text)
        bg_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 38)
        border_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 110)

        font = option.font
        font.setBold(True)
        fm = QFontMetrics(font)
        pill_width = fm.horizontalAdvance(status_text) + 16
        pill_height = min(fm.height() + 8, option.rect.height() - 6)
        if pill_width > option.rect.width() - 6:
            pill_width = max(option.rect.width() - 6, 20)

        x_pos = option.rect.left() + max(3, (option.rect.width() - pill_width) // 2)
        y_pos = option.rect.top() + max(2, (option.rect.height() - pill_height) // 2)
        pill_rect = QRect(x_pos, y_pos, pill_width, pill_height)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.rect)
        painter.setPen(border_color)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(pill_rect, 5, 5)

        elided_text = fm.elidedText(status_text, Qt.TextElideMode.ElideRight, pill_width - 8)
        painter.setPen(base_color)
        painter.setFont(font)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, elided_text)
        painter.restore()

        if modified:
            self._paint_modified_mark(painter, option)

    @staticmethod
    def _paint_modified_mark(painter, option):
        """A small corner mark: this cell has an edit that is not saved yet."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(Gate.WARN))
        r = option.rect
        painter.drawEllipse(r.left() + 3, r.top() + 3, 6, 6)
        painter.restore()

    @staticmethod
    def pill_width_for(text: str, font) -> int:
        """How wide a column must be to show this status without cutting it."""
        bold = type(font)(font)
        bold.setBold(True)
        return QFontMetrics(bold).horizontalAdvance(str(text)) + 16 + 14

    def _choices(self, current):
        allowed = None
        if self._allowed is not None:
            try:
                allowed = list(self._allowed()) or None
            except Exception:
                allowed = None
        return shot_status.choices_with(current, include_blank=True, allowed=allowed)

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        current = str(index.data(Qt.ItemDataRole.EditRole) or "")
        for value in self._choices(current):
            combo.addItem(value if value else self.NO_STATUS_TEXT, value)
            if value:
                combo.setItemData(combo.count() - 1, shot_status.describe(value),
                                  Qt.ItemDataRole.ToolTipRole)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        current = str(index.data(Qt.ItemDataRole.EditRole) or "")
        values = [editor.itemData(i) for i in range(editor.count())]
        idx = shot_status.match_index(values, current)
        editor.setCurrentIndex(idx if idx >= 0 else 0)

    def setModelData(self, editor: QComboBox, model, index):
        new_value = editor.currentData()
        if new_value is None:
            new_value = editor.currentText()
            if new_value == self.NO_STATUS_TEXT:
                new_value = ""
        current = str(index.data(Qt.ItemDataRole.EditRole) or "")
        if str(new_value).strip().upper() == current.strip().upper():
            return            # opened and closed without a change
        model.setData(index, new_value, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)
