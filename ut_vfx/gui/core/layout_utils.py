"""
Small helpers for rebuilding a layout's contents.

Clearing a layout is done in half a dozen places, and every one of them had the
same fault: deleteLater() schedules a widget for deletion on the next trip round
the event loop, but taking the item out of the layout does not take the widget
off the screen. Until that trip happens the old widgets carry on painting where
they were, and whatever is rebuilt in their place is drawn on top of them.

On the dashboard that showed up as two generations of shot counts stacked in one
another. Unparenting removes the widget from the display straight away.
"""

from PySide6.QtWidgets import QLayout


def clear_layout(layout: QLayout, delete: bool = True) -> int:
    """
    Empty a layout, taking its widgets off screen immediately.

    Nested layouts are cleared too, so a row of rows leaves nothing behind.
    Returns how many widgets were removed.
    """
    if layout is None:
        return 0

    removed = 0
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break

        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            if delete:
                widget.deleteLater()
            removed += 1
            continue

        child = item.layout()
        if child is not None:
            removed += clear_layout(child, delete=delete)
            child.setParent(None)
            if delete:
                child.deleteLater()

    return removed
