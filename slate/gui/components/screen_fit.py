"""
Sizing windows and dialogs to the screen they open on.

Several dialogs asked for a fixed size - Help opened at 1180x800 - which is
taller than the usable area of a 1366x768 or 1280x720 laptop: the Close button
and the footer ended up below the taskbar. Ask for the size you would like;
this gives you that or 90% of the screen, whichever is smaller, and keeps the
minimum size within the screen too.

    from slate.gui.components.screen_fit import fit_to_screen
    self.setMinimumSize(720, 480)
    fit_to_screen(self, 1180, 800)
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

FRACTION = 0.9


def available_size(widget: QWidget = None) -> QSize:
    """The usable size (taskbar excluded) of the screen the widget is on."""
    screen = None
    try:
        if widget is not None:
            parent = widget.parentWidget()
            screen = (parent.screen() if parent is not None else None) or widget.screen()
    except RuntimeError:
        screen = None
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return QSize(1280, 720)
    return screen.availableGeometry().size()


def fitted_size(width: int, height: int, available: QSize, fraction: float = FRACTION) -> QSize:
    """min(wanted, fraction of the screen), per side."""
    return QSize(min(int(width), int(available.width() * fraction)),
                 min(int(height), int(available.height() * fraction)))


def fit_to_screen(widget: QWidget, width: int, height: int,
                  fraction: float = FRACTION, center: bool = True) -> QSize:
    """
    Resize to (width, height), or to `fraction` of the available screen when
    that is smaller. The minimum size is lowered to fit as well, so a dialog
    can never be forced taller than the screen. Returns the size used.
    """
    available = available_size(widget)
    size = fitted_size(width, height, available, fraction)
    minimum = widget.minimumSize()
    if minimum.width() > size.width() or minimum.height() > size.height():
        widget.setMinimumSize(min(minimum.width(), size.width()),
                              min(minimum.height(), size.height()))
    widget.resize(size)
    if center:
        try:
            parent = widget.parentWidget()
            anchor = parent.window().frameGeometry() if parent is not None else None
            if anchor is None:
                screen = widget.screen() or QGuiApplication.primaryScreen()
                anchor = screen.availableGeometry()
            geo = widget.frameGeometry()
            geo.moveCenter(anchor.center())
            widget.move(geo.topLeft())
        except RuntimeError:
            pass
    return size
