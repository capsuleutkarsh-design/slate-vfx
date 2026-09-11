from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QComboBox
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QFontMetrics

class StatusDelegate(QStyledItemDelegate):
    """
    Status cells, drawn as pills and edited with a dropdown.

    ``allowed`` is a callable returning the statuses this person may pick.
    An artist gets the states of their own work; Approved, Retake and Omit
    are verdicts and are not offered - and are refused by the database too.
    """

    def __init__(self, parent=None, allowed=None):
        super().__init__(parent)
        self._allowed = allowed

    STATUS_COLORS = {
        "APPROVED": QColor("#5FBF8F"),
        "DONE": QColor("#5FBF8F"),
        "WIP": QColor("#3EA8BF"),
        "RETAKE": QColor("#D9635F"),
        "SENT FOR REVIEW": QColor("#3EA8BF"),
        "REVIEW": QColor("#3EA8BF"),
        "YTS": QColor("#D9A441"),
        "READY": QColor("#3EA8BF"),
        "OMIT": QColor("#87857F")
    }

    STATUS_CHOICES = [
        "WIP",
        "APPROVED",
        "RETAKE",
        "SENT FOR REVIEW",
        "YTS",
        "READY",
        "OMIT"
    ]

    def paint(self, painter: QPainter, option, index):
        status_text = index.data(Qt.ItemDataRole.DisplayRole)
        if not status_text:
            super().paint(painter, option, index)
            return

        painter.save()
        
        # Determine status colors: translucent background, subtle border, crisp accent text
        base_color = self.STATUS_COLORS.get(status_text.upper(), QColor("#87857F"))
        bg_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 38)
        border_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 90)
        text_color = base_color
        
        # Draw cell selection background if selected
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        
        # Create bold font for status
        font = option.font
        font.setBold(True)
        
        # Calculate pill rect using bold font metrics
        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(status_text)
        
        pill_width = text_rect.width() + 16
        pill_height = min(fm.height() + 8, option.rect.height() - 6)
        
        # Clamp pill width to cell width
        if pill_width > option.rect.width() - 6:
            pill_width = max(option.rect.width() - 6, 20)
            
        # Center pill in the cell
        x_pos = option.rect.left() + max(3, (option.rect.width() - pill_width) // 2)
        y_pos = option.rect.top() + max(2, (option.rect.height() - pill_height) // 2)
        
        pill_rect = QRect(x_pos, y_pos, pill_width, pill_height)
        
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Prevent drawing outside the cell
        painter.setClipRect(option.rect)
        
        # Draw sleek rounded pill chip
        painter.setPen(border_color)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(pill_rect, 5, 5)
        
        # Elide text if it's too long
        elided_text = fm.elidedText(status_text, Qt.TextElideMode.ElideRight, pill_width - 8)
        
        # Draw text inside pill
        painter.setPen(text_color)
        painter.setFont(font)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, elided_text)
        
        painter.restore()

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        choices = list(self.STATUS_CHOICES)
        if self._allowed is not None:
            try:
                choices = list(self._allowed()) or choices
            except Exception:
                pass
        combo.addItems(choices)
        combo.setStyleSheet("""
            QComboBox {
                background-color: #16161A;
                color: #E8E6E1;
                border: 1px solid #3EA8BF;
                border-radius: 4px;
                padding: 2px 8px;
                font-weight: bold;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #16161A;
                color: #E8E6E1;
                border: 1px solid #26262D;
                selection-background-color: #1D1D22;
                selection-color: #3EA8BF;
                padding: 4px;
            }
        """)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        current_value = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        idx = editor.findText(current_value, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            editor.setCurrentIndex(idx)
        else:
            editor.setCurrentText(current_value)

    def setModelData(self, editor: QComboBox, model, index):
        new_value = editor.currentText()
        model.setData(index, new_value, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)
