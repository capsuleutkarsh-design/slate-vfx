from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QColor, QBrush, QPainter

class ShotGridDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_size = QSize(280, 340)
        self.thumb_height = 200
        self.padding = 10
        
    def sizeHint(self, option, index):
        return self.card_size
        
    def paint(self, painter, option, index):
        if not index.isValid():
            return
            
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Data
        shot_data = index.data(Qt.ItemDataRole.UserRole)
        if not shot_data:
            painter.restore()
            return
            
        rect = option.rect
        
        # Background
        bg_color = QColor("#26262D")
        if option.state & QStyle.StateFlag.State_Selected:
            bg_color = QColor("#3EA8BF")
        elif option.state & QStyle.StateFlag.State_MouseOver:
            bg_color = QColor("#2C2C34")
            
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect.adjusted(5, 5, -5, -5), 5, 5)
        
        # Thumbnail
        thumb_rect = QRect(rect.left() + 5, rect.top() + 5, rect.width() - 10, self.thumb_height)
        thumb_path = shot_data.thumbnail_path
        
        # Draw thumbnail (placeholder logic for now if loading fails or not implemented fully)
        if thumb_path and hasattr(self, 'image_cache') and thumb_path in self.image_cache:
            self.image_cache[thumb_path]
        else:
            # Draw placeholder rect
            painter.setBrush(QBrush(QColor("#000000")))
            painter.drawRect(thumb_rect)
            painter.setPen(QColor("#E8E6E1"))
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, "NO SCAN")
            
        # Text Info
        text_rect = QRect(rect.left() + 15, rect.top() + self.thumb_height + 15, 
                          rect.width() - 30, rect.height() - self.thumb_height - 20)
        
        painter.setPen(QColor("#E8E6E1"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        
        # Title
        title = f"{shot_data.seq} / {shot_data.shot}"
        painter.drawText(text_rect.left(), text_rect.top(), title)
        
        # Details
        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        
        y_offset = 20
        painter.drawText(text_rect.left(), text_rect.top() + y_offset, f"Frames: {shot_data.frames}-{shot_data.exr_frames}")
        
        y_offset += 20
        # Status Badges (simplified as text for now in delegate)
        status_color = self.get_status_color(shot_data.cmm_status)
        painter.setPen(QColor(status_color))
        painter.drawText(text_rect.left(), text_rect.top() + y_offset, f"● {shot_data.cmm_status}")
        
        painter.restore()
        
    def get_status_color(self, status):
        colors = {
            'APPROVED': '#5FBF8F',
            'WIP': '#D9A441',
            'Kickback': '#D9635F',
            'YTS': '#3EA8BF',
            'Awaiting approval': '#D9635F',
            'FEEDBACK_WIP': '#D9A441'
        }
        return colors.get(status, '#B4B1AA')
