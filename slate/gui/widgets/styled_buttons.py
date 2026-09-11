"""
Styled Buttons - Reusable button components with consistent styling

This module provides pre-styled button widgets that use the design tokens
to ensure visual consistency across the application.

Usage:
    from slate.gui.widgets.styled_buttons import PrimaryButton, DangerButton
    save_btn = PrimaryButton("Save")
    delete_btn = DangerButton("Delete")
"""

from PySide6.QtWidgets import QPushButton, QComboBox
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QPen, QPainter, QColor
from ...core.infra.design_tokens import ColorTokens as C, RadiusTokens as R, SpacingTokens as S, TypographyTokens as T


class AnimatedHoverButton(QPushButton):
    """
    Base class for buttons with smooth hover animations using QPropertyAnimation.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._hover_alpha = 0
        self._anim = QPropertyAnimation(self, b"hover_alpha", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        
        # Base colors (to be overridden by subclasses)
        self.bg_color = QColor(C.ACCENT_PRIMARY)
        self.hover_color = QColor(C.ACCENT_HOVER)
        self.text_color = QColor(C.TEXT_INVERSE)
        
        # Initial style to set font and padding (without hover/pressed logic in CSS)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @Property(int)
    def hover_alpha(self):
        return self._hover_alpha

    @hover_alpha.setter
    def hover_alpha(self, value):
        self._hover_alpha = value
        self.update()

    def enterEvent(self, event):
        self._anim.stop()
        self._anim.setEndValue(255)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._anim.stop()
        self._anim.setEndValue(0)
        self._anim.start()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Determine current background
            base = self.bg_color
            overlay = self.hover_color

            # Blend colors based on hover_alpha
            r = base.red() + (overlay.red() - base.red()) * self._hover_alpha / 255
            g = base.green() + (overlay.green() - base.green()) * self._hover_alpha / 255
            b = base.blue() + (overlay.blue() - base.blue()) * self._hover_alpha / 255

            final_bg = QColor(int(r), int(g), int(b))

            if not self.isEnabled():
                final_bg = QColor(C.BG_ELEVATED)
                painter.setPen(QColor(C.TEXT_DISABLED))
            else:
                painter.setPen(Qt.NoPen)

            if self.isDown():
                final_bg = final_bg.darker(110)

            radius = getattr(self, 'border_radius', R.RADIUS_BUTTON)

            if getattr(self, "outlined", False) and self.isEnabled():
                # Outlined rather than filled. A solid block of colour is the
                # loudest thing in a row and pulls the eye straight to it, which
                # is the opposite of what a destructive control should do.
                tint = QColor(final_bg)
                tint.setAlpha(int(28 + 54 * self._hover_alpha / 255))
                painter.setBrush(tint)
                pen = QPen(final_bg)
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), radius, radius)
            else:
                painter.setBrush(final_bg)
                painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), radius, radius)

            # Text
            painter.setPen(self.text_color if self.isEnabled() else QColor(C.TEXT_DISABLED))
            painter.setFont(self.font())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        finally:
            if painter.isActive():
                painter.end()


class PrimaryButton(AnimatedHoverButton):
    """
    Primary action button with cyan background and smooth hover.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.bg_color = QColor(C.ACCENT_PRIMARY)
        self.hover_color = QColor(C.ACCENT_HOVER)
        self.setStyleSheet(f"padding: {S.PADDING_BUTTON}; font-weight: {T.WEIGHT_BOLD}; border: none; background: transparent;")


class DangerButton(AnimatedHoverButton):
    """
    Destructive action. Outlined, not filled - see the painter above for why.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.outlined = True
        self.bg_color = QColor(C.ERROR)
        self.hover_color = QColor(C.ERROR_BRIGHT)
        self.text_color = QColor(C.ERROR)
        self.setStyleSheet(f"padding: {S.PADDING_BUTTON}; font-weight: {T.WEIGHT_BOLD}; border: none; background: transparent;")


class SuccessButton(AnimatedHoverButton):
    """
    Success/approve button with green background and smooth hover.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.bg_color = QColor(C.SUCCESS)
        self.hover_color = QColor(C.SUCCESS_HOVER)
        self.setStyleSheet(f"padding: {S.SM}px; border: none; background: transparent;")


class RejectButton(AnimatedHoverButton):
    """
    Reject button with dim red background and smooth hover.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.bg_color = QColor(C.ERROR_DIM)
        self.hover_color = QColor(C.ERROR)
        self.setStyleSheet(f"padding: {S.SM}px; border: none; background: transparent;")


class SecondaryButton(AnimatedHoverButton):
    """
    Secondary action button with transparent background and border.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.bg_color = QColor(0, 0, 0, 0)
        self.hover_color = QColor(C.ACCENT_PRIMARY)
        self.text_color = QColor(C.ACCENT_PRIMARY)
        self.setStyleSheet(f"padding: {S.PADDING_BUTTON}; font-weight: {T.WEIGHT_SEMIBOLD}; border: 1px solid {C.ACCENT_PRIMARY}; background: transparent;")

    def paintEvent(self, event):
        # Override text color transition for secondary button
        if self.isEnabled() and self._hover_alpha > 0:
            # Gradually change text color to inverse
            inv = QColor(C.TEXT_INVERSE)
            base_txt = QColor(C.ACCENT_PRIMARY)
            r = base_txt.red() + (inv.red() - base_txt.red()) * self._hover_alpha / 255
            g = base_txt.green() + (inv.green() - base_txt.green()) * self._hover_alpha / 255
            b = base_txt.blue() + (inv.blue() - base_txt.blue()) * self._hover_alpha / 255
            self.text_color = QColor(int(r), int(g), int(b))
        else:
            self.text_color = QColor(C.ACCENT_PRIMARY)
        super().paintEvent(event)


class DropdownButton(AnimatedHoverButton):
    """
    Button with a dropdown arrow on the right.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.bg_color = QColor(C.BG_HOVER)
        self.hover_color = QColor(C.BORDER_DEFAULT)
        self.text_color = QColor(C.TEXT_PRIMARY)
        # Add some right padding for the arrow
        self.setStyleSheet(f"padding: {S.SM}px {S.XL}px {S.SM}px {S.MD}px; font-weight: {T.WEIGHT_BOLD}; border: none; background: transparent;")

    def paintEvent(self, event):
        super().paintEvent(event)

        # Draw the arrow
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(self.text_color)

            # Calculate arrow pos (right side)
            rect = self.rect()
            arrow_size = 6
            x = rect.right() - S.LG - arrow_size
            y = rect.center().y() - 2
            mid_x = x + (arrow_size // 2)

            # Use drawLine calls for strict PySide6 signature safety.
            painter.drawLine(x, y, mid_x, y + 4)
            painter.drawLine(mid_x, y + 4, x + arrow_size, y)
        finally:
            if painter.isActive():
                painter.end()


class GhostButton(AnimatedHoverButton):
    """
    Transparent button that only shows background on hover.
    Ideal for icon buttons or subtle actions.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.bg_color = QColor(0, 0, 0, 0)
        self.hover_color = QColor(C.BG_HOVER)
        self.text_color = QColor(C.TEXT_PRIMARY)
        self.setStyleSheet("border: none; background: transparent; font-size: 16px;")


__all__ = [
    'PrimaryButton',
    'DangerButton',
    'SuccessButton',
    'RejectButton',
    'SecondaryButton',
    'DropdownButton',
    'GhostButton',
    'AnimatedHoverButton',
]

class StyledComboBox(QComboBox):
    """
    A design-token compliant QComboBox.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QComboBox {{
                background-color: {C.BG_ELEVATED};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: {R.SM}px;
                padding: {S.SM}px {S.MD}px;
                font-weight: {T.WEIGHT_SEMIBOLD};
            }}
            QComboBox:hover {{
                border: 1px solid {C.ACCENT_PRIMARY};
                background-color: {C.BG_HOVER};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {C.BG_SURFACE};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.BORDER_LIGHT};
                selection-background-color: {C.ACCENT_PRIMARY};
            }}
        """)

__all__.append('StyledComboBox')
