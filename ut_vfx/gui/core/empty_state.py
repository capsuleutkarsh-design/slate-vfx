"""
What a table says when it has nothing in it.

Hardware, Licenses, Ticketing, Deployment and every log panel rendered as
several hundred pixels of black. On a fresh install there is no way to tell a
working empty table from a broken one, and nothing to tell you how to fill it.

An empty state says what is missing, why, and carries the action that fixes it.

    empty = EmptyState("No machines registered yet",
                       "Add a workstation, or pull the fleet from Live Ops.",
                       primary=("Add PC", self.add_pc))
    layout.addWidget(empty)
    empty.attach_to(self.table)     # shows itself only while the table is empty
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QWidget

from ut_vfx.core.infra.gate import Gate
from .controls import make_button
from .icons import icon as draw_icon


class EmptyState(QWidget):
    def __init__(self, title, body="", primary=None, secondary=None,
                 glyph="info", parent=None):
        """
        primary / secondary are (label, callback) pairs, or None.
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 32, 24, 32)
        outer.setSpacing(0)
        outer.addStretch(1)

        if glyph:
            mark = QLabel()
            mark.setPixmap(draw_icon(glyph, Gate.LINE, 34).pixmap(34, 34))
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addWidget(mark)
            outer.addSpacing(Gate.SPACE_3)

        self._heading = heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setWordWrap(True)
        heading.setStyleSheet(
            f"color: {Gate.TEXT_2}; font-family: {Gate.FONT_UI}; "
            f"font-size: {Gate.SIZE_LG}px; font-weight: 600; background: transparent;")
        outer.addWidget(heading)

        self._detail = None
        if body:
            self._detail = detail = QLabel(body)
            detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail.setWordWrap(True)
            detail.setStyleSheet(
                f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_UI}; "
                f"font-size: {Gate.SIZE_MD}px; background: transparent;")
            outer.addSpacing(Gate.SPACE_1)
            outer.addWidget(detail)

        if primary or secondary:
            row = QHBoxLayout()
            row.setSpacing(Gate.SPACE_2)
            row.addStretch(1)
            if primary:
                row.addWidget(make_button(primary[0], "primary", on_click=primary[1]))
            if secondary:
                row.addWidget(make_button(secondary[0], "secondary", on_click=secondary[1]))
            row.addStretch(1)
            outer.addSpacing(Gate.SPACE_4)
            outer.addLayout(row)

        outer.addStretch(1)
        self._table = None

    # ------------------------------------------------------------------ wiring
    def attach_to(self, view):
        """
        Follow a table: visible only while that table has no rows.

        Watches the model rather than being toggled by hand at each call site,
        so a table that empties itself later still says so.
        """
        self._table = view
        model = view.model() if hasattr(view, "model") else None
        if model is not None:
            for signal in ("rowsInserted", "rowsRemoved", "modelReset", "layoutChanged"):
                try:
                    getattr(model, signal).connect(self.refresh)
                except Exception:
                    pass
        self.refresh()
        return self

    def set_message(self, title, body=""):
        """
        Change what this says.

        An empty table means different things - nothing has been created yet,
        a filter excluded everything, or the database is unreachable - and a
        screen that says the same thing in all three cases is not helping.
        """
        try:
            self._heading.setText(str(title))
            if self._detail is not None:
                self._detail.setText(str(body))
                self._detail.setVisible(bool(body))
        except RuntimeError:
            # The C++ object is gone; the tab closed while this was in flight.
            pass

    def refresh(self, *args):
        if self._table is None:
            return

        # The model outlives the widget during teardown, so this can be called
        # after Qt has already destroyed the table underneath us. Touching it
        # then raises from C++, not Python, which is why it is caught by type.
        try:
            model = self._table.model() if hasattr(self._table, "model") else None
            if model is None:
                rows = self._table.rowCount() if hasattr(self._table, "rowCount") else 0
            else:
                rows = model.rowCount()
        except RuntimeError:
            self._table = None
            return

        empty = rows == 0
        try:
            self.setVisible(empty)
            # The table is hidden while empty; a grid of headers with nothing
            # under them reads as a failure rather than as "nothing yet".
            self._table.setVisible(not empty)
        except RuntimeError:
            self._table = None
