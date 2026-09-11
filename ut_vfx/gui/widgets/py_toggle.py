"""
PyToggle - Animated Toggle Switch Widget

A custom toggle switch widget with smooth animations.
Extracted from admin_panel.py to create a reusable component.

Usage:
    from ut_vfx.gui.widgets.py_toggle import PyToggle
    toggle = PyToggle()
    toggle.setChecked(True)
"""

from PySide6.QtWidgets import QCheckBox
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, Property
from PySide6.QtGui import QPainter, QColor

# Import design tokens for theming
from ...core.infra.design_tokens import ColorTokens as C


class PyToggle(QCheckBox):
    """
    Animated toggle switch widget.
    
    Features:
    - Smooth animation with bounce effect
    - Theme-aware colors using design tokens
    - Customizable appearance
    
    Properties:
        circle_position: Position of the toggle circle (animated)
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Widget setup
        self.setFixedSize(50, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Colors - now using design tokens for theme consistency
        self._bg_color = C.BG_ELEVATED      # "#26262D" → token
        self._circle_color = "#E8E6E1"       # Circle color (light gray)
        self._active_color = C.ACCENT_PRIMARY  # "#3EA8BF" → token
        
        # Animation setup
        self._circle_position = 3
        self.animation = QPropertyAnimation(self, b"circle_position", self)
        self.animation.setEasingCurve(QEasingCurve.OutBounce)
        self.animation.setDuration(300)
        
        # Connect state changes to animation
        self.stateChanged.connect(self.start_transition)
    
    def start_transition(self, value):
        """Start the toggle animation when state changes"""
        self.animation.stop()
        if value:
            # Move circle to the right (checked state)
            self.animation.setEndValue(self.width() - 26)
        else:
            # Move circle to the left (unchecked state)
            self.animation.setEndValue(3)
        self.animation.start()
    
    def hitButton(self, pos: QPoint):
        """Define the clickable area (entire widget)"""
        return self.contentsRect().contains(pos)
    
    def paintEvent(self, e):
        """Custom paint event to draw the toggle switch"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw background (rounded rectangle)
        bg_color = self._active_color if self.isChecked() else self._bg_color
        painter.setBrush(QColor(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 14, 14)
        
        # Draw circle
        painter.setBrush(QColor(self._circle_color))
        painter.drawEllipse(self._circle_position, 3, 22, 22)
        
        painter.end()
    
    # Property for animation
    def get_circle_position(self):
        """Get current circle position"""
        return self._circle_position
    
    def set_circle_position(self, pos):
        """Set circle position and trigger repaint"""
        self._circle_position = pos
        self.update()
    
    circle_position = Property(int, get_circle_position, set_circle_position)


__all__ = ['PyToggle']
