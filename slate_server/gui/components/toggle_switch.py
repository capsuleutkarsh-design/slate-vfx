from PySide6.QtWidgets import QWidget, QCheckBox
from PySide6.QtCore import Qt, QPropertyAnimation, QRect, QPoint, QEasingCurve, Property
from PySide6.QtGui import QPainter, QColor, QPen

from ..design_system import C

class ToggleSwitch(QCheckBox):
    """
    Animated iOS-style toggle switch for the Server Control Panel.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(48, 26)
        self.setCursor(Qt.PointingHandCursor)
        self._position = 3
        
        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setEasingCurve(QEasingCurve.InOutCirc)
        self.animation.setDuration(250)
        
        self.stateChanged.connect(self.start_animation)

    @Property(float)
    def position(self):
        return self._position

    @position.setter
    def position(self, pos):
        self._position = pos
        self.update()

    def start_animation(self, value):
        self.animation.stop()
        if value:
            self.animation.setEndValue(23)
        else:
            self.animation.setEndValue(3)
        self.animation.start()

    def hitButton(self, pos: QPoint):
        return self.contentsRect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        # Draw background track
        track_rect = QRect(0, 0, self.width(), self.height())
        if self.isChecked():
            p.setBrush(QColor(C.STATUS_OK))
        else:
            p.setBrush(QColor(C.BG_SURFACE_HOVER))
            
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(track_rect, 13, 13)
        
        # Draw inner shadow / border for un-toggled state
        if not self.isChecked():
            p.setPen(QPen(QColor(C.BORDER_FOCUS), 1))
            p.drawRoundedRect(track_rect.adjusted(1,1,-1,-1), 12, 12)
        
        # Draw thumb (the circle)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C.TEXT_PRIMARY))
        
        thumb_rect = QRect(int(self._position), 3, 20, 20)
        p.drawEllipse(thumb_rect)
        
        p.end()
