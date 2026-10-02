"""
The shot grid, with Reel and Shot Name frozen at the left.

With ten department columns the grid is ~2,200 px wide; scrolling right to see
Comp, Slap or AI scrolled the shot's identity away, and rows could only be told
apart by counting. This is Qt's frozen-column pattern: a second view laid over
the left edge of the grid, sharing its model and its selection, showing only
the frozen columns and scrolling up and down with it.
"""

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from .header_filter_view import FilterHeaderView
from .shot_table_model import COLUMN_KEY_ROLE


class FrozenColumnTable(QTableView):
    """A QTableView whose first columns (by key) stay put when it scrolls sideways."""

    def __init__(self, parent=None, frozen_keys=("reel", "shot_name")):
        super().__init__(parent)
        self.frozen_keys = tuple(frozen_keys)
        self.frozen = QTableView(self)
        self.frozen.setObjectName("frozenColumns")
        self.frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frozen.verticalHeader().hide()
        self.frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.frozen.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.frozen.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.frozen.setMouseTracking(True)
        self.viewport().stackUnder(self.frozen)

        self.frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.verticalScrollBar().valueChanged.connect(self.frozen.verticalScrollBar().setValue)
        self.frozen.clicked.connect(self.clicked)
        self.frozen.doubleClicked.connect(self.doubleClicked)
        self.frozen.customContextMenuRequested.connect(self._frozen_context_menu)

    # ------------------------------------------------------------ setup
    def frozen_columns(self):
        model = self.model()
        if model is None:
            return []
        return [c for c in range(model.columnCount())
                if model.headerData(c, Qt.Orientation.Horizontal, COLUMN_KEY_ROLE) in self.frozen_keys]

    def setModel(self, model):
        super().setModel(model)
        self.frozen.setModel(model)
        self.frozen.setSelectionModel(self.selectionModel())
        self.frozen.setSelectionBehavior(self.selectionBehavior())
        self.frozen.setSelectionMode(self.selectionMode())
        self.sync_columns()

    def set_frozen_header(self, header: FilterHeaderView):
        """The frozen columns' heading; shares the main heading's filters."""
        self.frozen.setHorizontalHeader(header)
        header.setSectionsMovable(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.sectionResized.connect(self._frozen_resized)
        self.sync_columns()

    def sync_columns(self):
        """Show only the frozen columns in the overlay, at the grid's widths."""
        model = self.model()
        if model is None:
            return
        frozen = set(self.frozen_columns())
        for column in range(model.columnCount()):
            hidden = column not in frozen
            self.frozen.setColumnHidden(column, hidden or self.isColumnHidden(column))
            if not hidden:
                self.frozen.setColumnWidth(column, self.columnWidth(column))
        self.frozen.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        self.frozen.setAlternatingRowColors(self.alternatingRowColors())
        self.frozen.setShowGrid(self.showGrid())
        self.frozen.setWordWrap(self.wordWrap())
        self.frozen.setTextElideMode(self.textElideMode())
        self.update_frozen_geometry()

    def copy_spans(self):
        self.frozen.clearSpans()
        model = self.model()
        if model is None:
            return
        count = model.columnCount()
        for row in getattr(model, "get_header_rows", lambda: [])():
            self.frozen.setSpan(row, 0, 1, count)

    def frozen_width(self) -> int:
        return sum(self.columnWidth(c) for c in self.frozen_columns() if not self.isColumnHidden(c))

    def setHorizontalHeader(self, header):
        super().setHorizontalHeader(header)
        if hasattr(self, "frozen"):
            # A new heading is stacked on top; the frozen columns must stay above it.
            self.frozen.raise_()

    def update_frozen_geometry(self):
        width = self.frozen_width()
        self.frozen.setVisible(width > 0)
        self.frozen.raise_()
        x = (self.verticalHeader().width() if self.verticalHeader().isVisible() else 0) + self.frameWidth()
        self.frozen.setGeometry(x, self.frameWidth(), width,
                                self.viewport().height() + self.horizontalHeader().height())

    # ------------------------------------------------------------ keeping in step
    def _frozen_resized(self, logical, old, new):
        # Only the frozen columns' own widths travel back; the overlay's
        # hidden columns resizing to nothing must not shrink the grid's.
        if new > 0 and logical in self.frozen_columns() and self.columnWidth(logical) != new:
            self.setColumnWidth(logical, new)
        self.update_frozen_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_frozen_geometry()

    def updateGeometries(self):
        super().updateGeometries()
        if hasattr(self, "frozen"):
            self.update_frozen_geometry()

    def on_main_section_resized(self, logical, old, new):
        if logical in self.frozen_columns():
            # Hiding a column resizes it to 0; showing it gives its width back.
            self.frozen.setColumnHidden(logical, new == 0)
            if new:
                self.frozen.setColumnWidth(logical, new)
            self.update_frozen_geometry()

    def setColumnHidden(self, column, hide):
        super().setColumnHidden(column, hide)
        if hasattr(self, "frozen") and column in self.frozen_columns():
            self.frozen.setColumnHidden(column, hide)
            self.update_frozen_geometry()

    def clearSpans(self):
        super().clearSpans()
        if hasattr(self, "frozen"):
            self.frozen.clearSpans()

    def moveCursor(self, action, modifiers):
        current = super().moveCursor(action, modifiers)
        if not current.isValid():
            return current
        frozen = self.frozen_columns()
        if action == QAbstractItemView.CursorAction.MoveLeft and current.column() not in frozen:
            left = self.visualRect(current).topLeft().x()
            if left < self.frozen_width():
                bar = self.horizontalScrollBar()
                bar.setValue(bar.value() + left - self.frozen_width())
        return current

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        # Scrolling a frozen cell into view would only move the rest about.
        if index.isValid() and index.column() in self.frozen_columns():
            index = self.model().index(index.row(), self._first_unfrozen_visible()) \
                if self.model() is not None else index
        super().scrollTo(index, hint)

    def _first_unfrozen_visible(self) -> int:
        frozen = set(self.frozen_columns())
        header = self.horizontalHeader()
        for visual in range(header.count()):
            logical = header.logicalIndex(visual)
            if logical not in frozen and not self.isColumnHidden(logical):
                return logical
        return 0

    def _frozen_context_menu(self, pos):
        # Same row, in the grid's own coordinates: the menu is about the row.
        global_pos = self.frozen.viewport().mapToGlobal(pos)
        self.customContextMenuRequested.emit(self.viewport().mapFromGlobal(global_pos))

    def index_at_frozen(self, pos) -> QModelIndex:
        return self.frozen.indexAt(pos)
