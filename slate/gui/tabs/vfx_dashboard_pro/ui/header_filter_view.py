"""
The grid's column headings: click to sort, and a filter on every column.

The column filter used to be hidden behind a right-click with nothing on the
heading to hint at it, took one value at a time, listed only values still on
screen (after filtering Type = AI Shot, the Type list offered only AI Shot),
sorted '1200' before '240', and drew its marker as a triangle that looked
exactly like the sort arrow. It also remembered filters by column number, so a
filter set on one project quietly applied to whatever column sat there in the
next.

Now every heading shows a funnel when the pointer is over it (click it, or
right-click anywhere on the heading). The list has every value in the project,
in natural order, with a search box and a tick per value. A filtered heading is
tinted and carries a solid funnel. Filters are kept by column key.

Department headings show the department's name ("Matchmove") where the column
is wide enough and the short label ("MMV") where it is not; the tooltip always
has the full name.
"""

from typing import Callable, Dict, List, Optional, Set

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QStyle, QStyleOptionHeader, QVBoxLayout, QWidget, QWidgetAction,
)

from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.icons import icon as draw_icon
from .shot_table_model import COLUMN_KEY_ROLE, HEADER_SHORT_ROLE

BLANK_VALUE = "(blank)"
FUNNEL = 14


class FilterHeaderView(QHeaderView):
    # The set of active filters changed.
    filter_changed = Signal()
    # A heading was right-clicked or its funnel clicked: (logical index).
    filter_menu_requested = Signal(int)

    def __init__(self, parent=None, pinned_keys=("reel", "shot_name")):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setStretchLastSection(True)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setMouseTracking(True)
        # Columns can be dragged into another order; the shot's identity
        # stays first.
        self.setSectionsMovable(True)
        self._pinned_keys = tuple(pinned_keys)
        self.sectionMoved.connect(self._keep_pinned_first)
        # {column key: set of accepted values}
        self.active_filters: Dict[str, Set[str]] = {}
        self._values_for: Optional[Callable[[str], List[str]]] = None
        self._hover = -1

    # ------------------------------------------------------------ filters
    def set_value_provider(self, provider: Callable[[str], List[str]]):
        """provider(column key) -> every value of that column in the project, sorted."""
        self._values_for = provider

    def column_key(self, logical: int) -> str:
        model = self.model()
        if model is None:
            return ""
        return str(model.headerData(logical, Qt.Orientation.Horizontal, COLUMN_KEY_ROLE) or "")

    def logical_for_key(self, key: str) -> int:
        for logical in range(self.count()):
            if self.column_key(logical) == key:
                return logical
        return -1

    def apply_filter(self, col_key: str, values):
        """Keep only these values in this column; None or an empty set clears it."""
        if values:
            self.active_filters[col_key] = set(values)
        else:
            self.active_filters.pop(col_key, None)
        self.viewport().update()
        self.filter_changed.emit()

    def clear_filters(self, emit: bool = True):
        had = bool(self.active_filters)
        self.active_filters = {}
        self.viewport().update()
        if emit and had:
            self.filter_changed.emit()

    def show_filter_menu(self, logical_index: int, at: QPoint = None):
        key = self.column_key(logical_index)
        if not key or self._values_for is None:
            return
        values = list(self._values_for(key) or [])
        if not values:
            return

        menu = QMenu(self)
        panel = _FilterPanel(values, self.active_filters.get(key), menu)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)

        def apply(selected):
            menu.close()
            # Everything ticked is the same as no filter.
            self.apply_filter(key, None if (selected is None or len(selected) == len(values)) else selected)

        panel.applied.connect(apply)
        if at is None:
            at = self.mapToGlobal(QPoint(self.sectionViewportPosition(logical_index), self.height()))
        menu.exec(at)

    # ------------------------------------------------------------ painting
    def _funnel_rect(self, rect: QRect) -> QRect:
        right = rect.right() - 6
        if self.isSortIndicatorShown():
            right -= 16          # leave the sort arrow its own place
        return QRect(right - FUNNEL, rect.center().y() - FUNNEL // 2, FUNNEL, FUNNEL)

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        model = self.model()
        key = self.column_key(logical_index)
        filtered = key in self.active_filters

        text = ""
        if model is not None:
            full = str(model.headerData(logical_index, Qt.Orientation.Horizontal,
                                        Qt.ItemDataRole.DisplayRole) or "")
            short = model.headerData(logical_index, Qt.Orientation.Horizontal, HEADER_SHORT_ROLE)
            text = full
            room = rect.width() - 20 - (FUNNEL + 8) - (16 if self.isSortIndicatorShown() else 0)
            if short and self.fontMetrics().horizontalAdvance(full) > room:
                text = str(short)

        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logical_index
        opt.text = text
        opt.textAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logical_index:
            opt.sortIndicator = (QStyleOptionHeader.SortIndicator.SortDown
                                 if self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
                                 else QStyleOptionHeader.SortIndicator.SortUp)
        else:
            opt.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        if self._hover == logical_index:
            opt.state |= QStyle.StateFlag.State_MouseOver
        painter.save()
        self.style().drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)
        painter.restore()

        if filtered:
            painter.save()
            painter.fillRect(rect.adjusted(0, 0, -1, -1), Gate.qcolor(Gate.ACCENT, 0.14))
            painter.restore()
        if filtered or self._hover == logical_index:
            colour = Gate.ACCENT if filtered else Gate.TEXT_DIM
            funnel = draw_icon("filter", colour, FUNNEL)
            funnel.paint(painter, self._funnel_rect(rect))

    # ------------------------------------------------------------ mouse
    def mouseMoveEvent(self, event):
        logical = self.logicalIndexAt(event.position().toPoint())
        if logical != self._hover:
            self._hover = logical
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = -1
        self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        logical = self.logicalIndexAt(pos)
        if logical >= 0:
            if event.button() == Qt.MouseButton.RightButton:
                self.filter_menu_requested.emit(logical)
                self.show_filter_menu(logical, self.mapToGlobal(pos))
                return
            if event.button() == Qt.MouseButton.LeftButton:
                section = QRect(self.sectionViewportPosition(logical), 0,
                                self.sectionSize(logical), self.height())
                if self._funnel_rect(section).adjusted(-3, -3, 3, 3).contains(pos):
                    self.filter_menu_requested.emit(logical)
                    self.show_filter_menu(logical)
                    return
        super().mousePressEvent(event)

    def _keep_pinned_first(self, logical, old_visual, new_visual):
        """Reel and Shot Name stay the first two columns whatever is dragged."""
        self.blockSignals(True)
        try:
            for wanted, key in enumerate(self._pinned_keys):
                pinned = self.logical_for_key(key)
                if pinned >= 0 and self.visualIndex(pinned) != wanted:
                    self.moveSection(self.visualIndex(pinned), wanted)
        finally:
            self.blockSignals(False)

    # Kept for older callers: values now come from the provider.
    def update_filters(self, shots=None):
        pass


class _FilterPanel(QWidget):
    """A search box, one tick per value, Select all / Clear / Apply."""

    applied = Signal(object)    # set of values, or None for "no filter"

    def __init__(self, values, selected, parent=None):
        super().__init__(parent)
        self._values = list(values)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search values…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_list)
        layout.addWidget(self.search)

        self.list = QListWidget(self)
        self.list.setMinimumWidth(220)
        self.list.setMaximumHeight(280)
        for value in self._values:
            item = QListWidgetItem(value, self.list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ticked = selected is None or value in selected
            item.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
        self.list.itemClicked.connect(self._toggle)
        layout.addWidget(self.list)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(make_button("All", "ghost", on_click=lambda: self._set_all(True)))
        row.addWidget(make_button("None", "ghost", on_click=lambda: self._set_all(False)))
        row.addStretch(1)
        row.addWidget(make_button("Clear filter", "secondary", on_click=lambda: self.applied.emit(None)))
        row.addWidget(make_button("Apply", "primary", on_click=self._apply))
        layout.addLayout(row)

    def _toggle(self, item):
        # A click anywhere on the row ticks it, not just on the box.
        pass

    def _filter_list(self, text):
        needle = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _set_all(self, ticked: bool):
        state = Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked
        for i in range(self.list.count()):
            item = self.list.item(i)
            if not item.isHidden():
                item.setCheckState(state)

    def selected(self) -> Set[str]:
        return {self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked}

    def _apply(self):
        chosen = self.selected()
        if not chosen:
            # Nothing ticked would hide every shot; treat it as "clear".
            self.applied.emit(None)
            return
        self.applied.emit(chosen)
