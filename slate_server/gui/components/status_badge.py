from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor

from ..design_system import C, T

class StatusBadge(QWidget):
    """
    Glowing status indicator.
    """
    def __init__(self, text="Offline", status="error", parent=None):
        super().__init__(parent)
        self.status = status  # 'ok', 'error', 'warning'
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 12, 4)
        layout.setSpacing(8)
        
        self.indicator = QWidget()
        self.indicator.setFixedSize(10, 10)
        
        self.lbl_text = QLabel(text)
        self.lbl_text.setStyleSheet(f"font-size: 13px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_PRIMARY};")
        
        layout.addWidget(self.indicator)
        layout.addWidget(self.lbl_text)
        
        self.update_style()

    def set_status(self, text, status):
        self.lbl_text.setText(text)
        self.status = status
        self.update_style()
        self.indicator.update()

    def update_style(self):
        color = C.STATUS_ERROR
        if self.status == 'ok':
            color = C.STATUS_OK
        elif self.status == 'warning':
            color = C.STATUS_WARNING
            
        self.setStyleSheet(f"""
            StatusBadge {{
                background-color: {color}1A; /* 10% opacity */
                border: 1px solid {color}33; /* 20% opacity */
                border-radius: 12px;
            }}
        """)
        
    def paintEvent(self, event):
        super().paintEvent(event)
        
        # We also need to paint the glowing dot manually 
        # (It's easier to paint it on the indicator widget)
        pass

# We will override the indicator's paint event dynamically
def patch_indicator_paint(indicator, badge):
    def custom_paint(event):
        p = QPainter(indicator)
        p.setRenderHint(QPainter.Antialiasing)
        
        color = C.STATUS_ERROR
        if badge.status == 'ok': color = C.STATUS_OK
        elif badge.status == 'warning': color = C.STATUS_WARNING
        
        # Draw Glow
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color + "40")) # 25% opacity
        p.drawEllipse(0, 0, 10, 10)
        
        # Draw Core
        p.setBrush(QColor(color))
        p.drawEllipse(2, 2, 6, 6)
        p.end()
        
    indicator.paintEvent = custom_paint

class StatusBadgeWidget(StatusBadge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        patch_indicator_paint(self.indicator, self)
