from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QColor, QBrush, QPainter
from slate.core.infra.gate import Gate

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
        bg_color = QColor(Gate.RAISED_HI)
        if option.state & QStyle.StateFlag.State_Selected:
            bg_color = QColor(Gate.ACCENT)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            bg_color = QColor(Gate.LINE)
            
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
            painter.setPen(QColor(Gate.TEXT))
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, "NO SCAN")
            
        # Text Info
        text_rect = QRect(rect.left() + 15, rect.top() + self.thumb_height + 15, 
                          rect.width() - 30, rect.height() - self.thumb_height - 20)
        
        painter.setPen(QColor(Gate.TEXT))
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
            'APPROVED': Gate.OK,
            'WIP': Gate.WARN,
            'Kickback': Gate.BAD,
            'YTS': Gate.ACCENT,
            'Awaiting approval': Gate.BAD,
            'FEEDBACK_WIP': Gate.WARN
        }
        return colors.get(status, Gate.TEXT_2)
