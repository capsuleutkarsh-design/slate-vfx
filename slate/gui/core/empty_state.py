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

Or, with no layout plumbing at all, laid over the table's own viewport (the
headers stay visible, so the table still reads as a table):

    EmptyState.over(self.table, "Nothing to rename yet",
                    "Load files or drop them here to preview new names.")

An empty table after a search is not the same as an empty table. Tell the
empty state when a search or filter is narrowing the rows, and it says so and
offers to clear it instead of claiming there is nothing at all:

    self.empty.set_filtered(bool(search_text or status_filter),
                            on_clear=self.clear_filters, noun="tickets")

For a page that is a grid of cards rather than a table, put the content and
the empty state in a stack so the message is centred in the page rather than
squeezed into the grid's first cell:

    stack = empty_stack(cards_widget, EmptyState("No workstations have reported yet"))
    stack.show_empty(len(cards) == 0)
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from slate.core.infra.gate import Gate
from .controls import make_button
from .icons import icon as draw_icon


class EmptyState(QWidget):
    def __init__(self, title, body="", primary=None, secondary=None,
                 glyph="info", parent=None):
        """
        primary / secondary are (label, callback) pairs, or None.
        """
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 32, 24, 32)
        outer.setSpacing(0)
        outer.addStretch(1)

        self._mark = None
        self._glyph = glyph
        if glyph:
            self._mark = mark = QLabel()
            # Dim text, not the divider colour: drawn in Gate.LINE the mark was
            # all but invisible on the ground it sits on.
            mark.setPixmap(draw_icon(glyph, Gate.TEXT_DIM, 34).pixmap(34, 34))
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

        # Always built, so set_message and the no-match variant can add a body
        # to an empty state that started without one.
        self._detail = detail = QLabel(body)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        detail.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_UI}; "
            f"font-size: {Gate.SIZE_MD}px; background: transparent;")
        outer.addSpacing(Gate.SPACE_1)
        outer.addWidget(detail)
        detail.setVisible(bool(body))

        row = QHBoxLayout()
        row.setSpacing(Gate.SPACE_2)
        row.addStretch(1)
        self._actions = []
        if primary:
            button = make_button(primary[0], "primary", on_click=primary[1])
            row.addWidget(button)
            self._actions.append(button)
        if secondary:
            button = make_button(secondary[0], "secondary", on_click=secondary[1])
            row.addWidget(button)
            self._actions.append(button)
        # The "clear the search" button of the no-match variant.
        self._clear_button = make_button("Clear search and filters", "secondary")
        self._clear_button.hide()
        row.addWidget(self._clear_button)
        row.addStretch(1)
        outer.addSpacing(Gate.SPACE_4)
        outer.addLayout(row)

        outer.addStretch(1)
        self._table = None
        self._overlay = False
        self._fit = None
        self._normal = (str(title), str(body or ""))
        self._filtered = False
        self._on_clear = None
        self._clear_button.clicked.connect(self._clear_clicked)

    # --------------------------------------------------------------- factory
    @classmethod
    def over(cls, view, title, body="", primary=None, secondary=None, glyph="info"):
        """
        An empty state laid over a table's viewport: visible while the table
        has no rows, with the table (and its headers) left in place.
        """
        state = cls(title, body, primary=primary, secondary=secondary, glyph=glyph,
                    parent=view.viewport())
        state.attach_to(view, overlay=True)
        return state

    # ------------------------------------------------------------------ wiring
    def attach_to(self, view, overlay: bool = False):
        """
        Follow a table: visible only while that table has no rows.

        Watches the model rather than being toggled by hand at each call site,
        so a table that empties itself later still says so. Without overlay
        the table itself is hidden while empty and this widget (placed in the
        layout beside it) takes its place.
        """
        self._table = view
        self._overlay = bool(overlay)
        model = view.model() if hasattr(view, "model") else None
        if model is not None:
            for signal in ("rowsInserted", "rowsRemoved", "modelReset", "layoutChanged"):
                try:
                    getattr(model, signal).connect(self.refresh)
                except Exception:
                    pass
        if self._overlay:
            if self.parent() is not view.viewport():
                self.setParent(view.viewport())
            self._fit = _FitToParent(self)
            view.viewport().installEventFilter(self._fit)
            self.setGeometry(view.viewport().rect())
            self._update_mouse()
        self.refresh()
        return self

    def _update_mouse(self):
        # Laid over a table, an empty state with nothing to press lets clicks
        # and dropped files through to the table underneath.
        if not self._overlay:
            return
        interactive = bool(self._actions) and not self._filtered
        interactive = interactive or (self._filtered and self._on_clear is not None)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)

    def set_message(self, title, body=""):
        """
        Change what this says.

        An empty table means different things - nothing has been created yet,
        a filter excluded everything, or the database is unreachable - and a
        screen that says the same thing in all three cases is not helping.
        """
        self._normal = (str(title), str(body or ""))
        if not self._filtered:
            self._show_text(*self._normal)

    def _show_text(self, title, body):
        try:
            self._heading.setText(str(title))
            self._detail.setText(str(body))
            self._detail.setVisible(bool(body))
        except RuntimeError:
            # The C++ object is gone; the tab closed while this was in flight.
            pass

    def set_filtered(self, active: bool, on_clear=None, noun: str = "items",
                     title: str = None, body: str = None):
        """
        Say "no match" rather than "nothing here" while a search or filter is
        narrowing the rows, with a button that clears them (when on_clear is
        given). set_filtered(False) goes back to the ordinary message.
        """
        self._filtered = bool(active)
        self._on_clear = on_clear
        try:
            if self._filtered:
                self._show_text(title or f"No {noun} match",
                                body if body is not None else
                                "Nothing matches the current search or filters.")
                self._clear_button.setVisible(on_clear is not None)
                for button in self._actions:
                    button.hide()
                if self._mark is not None:
                    self._mark.setPixmap(draw_icon("search", Gate.TEXT_DIM, 34).pixmap(34, 34))
            else:
                self._show_text(*self._normal)
                self._clear_button.hide()
                for button in self._actions:
                    button.show()
                if self._mark is not None and self._glyph:
                    self._mark.setPixmap(draw_icon(self._glyph, Gate.TEXT_DIM, 34).pixmap(34, 34))
            self._update_mouse()
        except RuntimeError:
            pass

    def is_filtered(self) -> bool:
        return self._filtered

    def _clear_clicked(self):
        if callable(self._on_clear):
            self._on_clear()

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
            if self._overlay:
                if empty:
                    self.raise_()
            else:
                # The table is hidden while empty; a grid of headers with
                # nothing under them reads as a failure rather than as
                # "nothing yet".
                self._table.setVisible(not empty)
        except RuntimeError:
            self._table = None


class _FitToParent(QObject):
    """Keeps an overlay the size of the viewport it covers."""

    def __init__(self, widget):
        super().__init__(widget)
        self._widget = widget

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            try:
                self._widget.setGeometry(watched.rect())
            except RuntimeError:
                pass
        return False


class EmptyStack(QStackedWidget):
    """Content, or its empty state, centred in the same space."""

    def __init__(self, content: QWidget, empty: EmptyState, parent=None):
        super().__init__(parent)
        self.content = content
        self.empty = empty
        self.addWidget(content)
        self.addWidget(empty)

    def show_empty(self, empty: bool = True):
        self.setCurrentWidget(self.empty if empty else self.content)


def empty_stack(content: QWidget, empty: EmptyState, parent=None) -> EmptyStack:
    return EmptyStack(content, empty, parent)
