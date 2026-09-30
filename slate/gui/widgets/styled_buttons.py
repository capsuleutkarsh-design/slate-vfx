"""
Styled Buttons - the button kinds as classes.

These used to be a second button kit: each class painted itself (a solid dark
block for Ghost, a 16 px bold label, an animated fill), so Stock Viewer and the
dashboard toolbar had buttons that matched nothing made with
slate.gui.core.controls.make_button - three looks and two heights in one row.

They are now thin names for the four kinds in controls.py, so both ways of
making a button give the same button:

    from slate.gui.widgets.styled_buttons import PrimaryButton, DangerButton
    save_btn = PrimaryButton("Save")        # == make_button("Save", "primary")
    delete_btn = DangerButton("Delete")     # == make_button("Delete", "danger")

Prefer make_button in new code. Button text is shown as written - an "&" is
not a keyboard shortcut here either.
"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QComboBox, QPushButton

from ...core.infra.gate import Gate
from ..core.controls import plain, style_button


class AnimatedHoverButton(QPushButton):
    """
    Base class for the named kinds. (The hover animation it was named after
    painted over the stylesheet, which is why these buttons never matched the
    others; the name is kept for anything that subclasses it.)
    """

    KIND = "secondary"

    def __init__(self, text="", parent=None):
        super().__init__(plain(text), parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        style_button(self, self.KIND)


class PrimaryButton(AnimatedHoverButton):
    """The one action a screen is for."""
    KIND = "primary"


class SecondaryButton(AnimatedHoverButton):
    """An ordinary action."""
    KIND = "secondary"


class DangerButton(AnimatedHoverButton):
    """Destroys something: outlined in red, filled only on hover."""
    KIND = "danger"


class GhostButton(AnimatedHoverButton):
    """
    Present, but not asking for attention: no fill until hovered, and the same
    size and weight as every other button (it was 16 px bold on a black block,
    heavier than the primary button beside it).
    """
    KIND = "ghost"


class SuccessButton(AnimatedHoverButton):
    """Approve / confirm. There is no green kind: the action a screen is for is primary."""
    KIND = "primary"


class RejectButton(AnimatedHoverButton):
    """Reject: a destructive decision, so it looks like one."""
    KIND = "danger"


class DropdownButton(AnimatedHoverButton):
    """
    A button that opens a menu, with a chevron on the right.

    The chevron has its own reserved space. It used to be painted at a fixed
    offset over the label, so "Manage Project" ran into it.
    """

    KIND = "secondary"
    ARROW_ROOM = 32

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(self.styleSheet() + (
            "QPushButton { padding-right: %dpx; } "
            "QPushButton::menu-indicator { image: none; width: 0px; }" % self.ARROW_ROOM))

    def paintEvent(self, event):
        super().paintEvent(event)
        from PySide6.QtGui import QPainter
        from ..core.icons import icon

        size = 12
        colour = Gate.TEXT_2 if self.isEnabled() else Gate.IDLE
        # 8 px clear of where the label can end, 12 px in from the edge.
        target = QRect(self.width() - self.ARROW_ROOM + 8, (self.height() - size) // 2, size, size)
        painter = QPainter(self)
        try:
            icon("chevron-down", colour, size).paint(painter, target)
        finally:
            painter.end()


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
    A combo box that looks like every other combo box.

    It carried its own stylesheet, which styled the drop-down but not its
    arrow - so the arrow was drawn as an accent-coloured block - and its
    padding made it 43 px tall in a row of 32 px controls. main.qss now draws
    every combo (height, chevron, pop-up) so this adds only the hand cursor.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


__all__.append('StyledComboBox')
