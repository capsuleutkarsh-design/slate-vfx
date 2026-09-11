from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor, QPainter, QBrush, QPen
from .components.qt_safety import safe_single_shot

class NotificationOverlay(QWidget):
    """
    Mac-style toast notification that slides in from bottom-right.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        self.setFixedSize(300, 80)
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        
        self.lbl_title = QLabel("Notification")
        self.lbl_title.setStyleSheet("color: #3EA8BF; font-weight: bold; font-size: 14px;")
        
        self.lbl_msg = QLabel("Message content goes here...")
        self.lbl_msg.setStyleSheet("color: white; font-size: 12px;")
        self.lbl_msg.setWordWrap(True)
        
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_msg)
        layout.addStretch()
        
        # Animation
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setDuration(400)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.hide_notification)
        self.timer.setSingleShot(True)
        
        # Click handler
        self.callback = None

    def show_message(self, title, message, duration=4000, on_click=None):
        self.lbl_title.setText(title)
        self.lbl_msg.setText(message)
        self.callback = on_click
        
        # Position
        if self.parent():
            parent_geo = self.parent().geometry()
            # Bottom Right
            target_x = parent_geo.width() - self.width() - 20
            target_y = parent_geo.height() - self.height() - 20
            
            # Start below screen
            self.move(target_x, parent_geo.height() + 10)
            self.show()
            self.raise_()
            
            # Animate In
            self.anim.setStartValue(QPoint(target_x, parent_geo.height() + 10))
            self.anim.setEndValue(QPoint(target_x, target_y))
            self.anim.start()
            
            self.timer.start(duration)

    def hide_notification(self):
        if self.parent():
            parent_geo = self.parent().geometry()
            target_x = self.x()
            target_y = parent_geo.height() + 10
            
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(target_x, target_y))
            self.anim.start()
            
            safe_single_shot(400, self, self.hide) # Actually hide after anim

    def mousePressEvent(self, event):
        if self.callback:
            self.callback()
        self.hide_notification()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Semi-transparent dark bg
        painter.setBrush(QBrush(QColor(30, 30, 30, 240)))
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        
        painter.drawRoundedRect(self.rect(), 10, 10)
