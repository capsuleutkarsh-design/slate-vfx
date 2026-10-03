"""
Tables that behave the same everywhere.

The audit found the same five faults on table after table:

* cells could be typed into (Qt's default edit triggers) and the typing was
  never saved - "2099-01-01" looked re-planned until the next reload;
* after a reload the selection stayed on the same row *numbers*, which now
  held other records - the next "Mark Failed" hit the wrong deployment;
* headers could not be clicked to sort, and where sorting existed numbers and
  dates sorted as text ("10" before "9");
* there was no search box, and no Refresh;
* an action read the row index, so sorting would have sent it to the wrong
  record.

One set of helpers fixes all of it. A typical tab:

    from slate.gui.components.table_tools import (
        setup_table, make_item, KeepSelection, TableToolbar, selected_keys)

    setup_table(self.table)                       # read-only, rows, sortable
    self.toolbar = TableToolbar(self.table, placeholder="Search machines…",
                                on_refresh=self.load_data)
    self.toolbar.add_filter("Status", [("All", ""), ("In use", "In use")], column=3)
    layout.addWidget(self.toolbar)

    def load_data(self):
        rows = self.repo.all()
        with KeepSelection(self.table):           # remembers ids, not row numbers
            self.table.setRowCount(len(rows))
            for r, rec in enumerate(rows):
                self.table.setItem(r, 0, make_item(rec["name"], key=rec["id"]))
                self.table.setItem(r, 1, make_item(f"{rec['days']} d", sort_value=rec["days"]))
                self.table.setItem(r, 2, make_item(rec["due"].strftime(...), sort_value=rec["due"]))

    def mark_failed(self):
        for deployment_id in selected_keys(self.table):   # never the row index
            ...

A table that really edits in place opts in and saves every edit, or puts the
cell back and says why:

    enable_inline_edits(self.table, save=self.save_cell, columns={2, 3})
    def save_cell(self, key, column, text):   # -> Result / bool / (ok, message)
"""

from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal
from typing import Callable, Iterable, List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem, QWidget,
)

logger = logging.getLogger(__name__)

# The value a cell sorts by (a number, a date) when it is not its text. The
# same role as slate.gui.core.data_display, so its date_item / money_item cells
# and make_item cells read each other's sort values in one table.
SORT_ROLE = Qt.ItemDataRole.UserRole + 101
# The record a row stands for (an id or a name), kept on the key column.
KEY_ROLE = Qt.ItemDataRole.UserRole + 51

KEY_COLUMN = 0


def _sortable(value):
    """A value that compares sensibly with others of its kind."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    # Dates and date-times on one scale, so a column mixing them still sorts.
    if isinstance(value, _dt.datetime):
        return value.timestamp()
    if isinstance(value, _dt.date):
        return _dt.datetime.combine(value, _dt.time()).timestamp()
    return str(value).casefold()


class SortItem(QTableWidgetItem):
    """
    A cell that sorts by its value, not its text: 9 before 10, 31 Jan before
    1 Feb, "₹1,20,000" by the amount. Cells without a sort value compare by
    text, ignoring case. Empty values always go last, whichever way round.
    """

    def __lt__(self, other):
        mine = self.data(SORT_ROLE)
        theirs = other.data(SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if mine is None and theirs is None:
            return self.text().casefold() < (other.text().casefold() if other else "")
        a, b = _sortable(mine), _sortable(theirs)
        if a is None or b is None:
            # Empty cells last in either direction. A descending sort asks
            # "other < self", so the answer for an empty cell flips with it.
            descending = False
            table = self.tableWidget()
            if table is not None:
                descending = (table.horizontalHeader().sortIndicatorOrder()
                              == Qt.SortOrder.DescendingOrder)
            if a is None and b is None:
                return False
            return (a is None) == descending
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


def make_item(text, sort_value=None, key=None, editable: bool = False,
              align=None, tooltip: str = None, foreground=None) -> SortItem:
    """
    A read-only table cell. `sort_value` is what it sorts by; `key` is the id
    of the record the row stands for (put it on the key column, normally 0).
    """
    item = SortItem("" if text is None else str(text))
    if sort_value is not None:
        item.setData(SORT_ROLE, sort_value)
    if key is not None:
        item.setData(KEY_ROLE, key)
    flags = item.flags()
    if editable:
        item.setFlags(flags | Qt.ItemFlag.ItemIsEditable)
    else:
        item.setFlags(flags & ~Qt.ItemFlag.ItemIsEditable)
    if align is not None:
        item.setTextAlignment(align)
    elif isinstance(sort_value, (int, float, Decimal)) and not isinstance(sort_value, bool):
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if tooltip:
        item.setToolTip(tooltip)
    if foreground is not None:
        from PySide6.QtGui import QColor
        item.setForeground(QColor(foreground))
    return item


def setup_table(table: QAbstractItemView, *, sortable: bool = True, editable: bool = False,
                select_rows: bool = True, multi_select: bool = True) -> QAbstractItemView:
    """
    The behaviour every data table shares (the look is the theme's).

    Read-only unless `editable` - and a table that edits must save what is
    typed (enable_inline_edits). Whole rows are selected. Headers sort when
    `sortable`.
    """
    if not editable:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    if select_rows:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection if multi_select
        else QAbstractItemView.SelectionMode.SingleSelection)
    if hasattr(table, "setSortingEnabled"):
        # No column chosen yet: rows stay in the order the screen put them in
        # (worst ticket first, newest first...) until somebody clicks a
        # header. Left alone, Qt sorts by the first column, descending, the
        # moment sorting is switched on.
        header = table.horizontalHeader() if hasattr(table, "horizontalHeader") else None
        if header is not None and sortable:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        table.setSortingEnabled(bool(sortable))
    table.setProperty("slateSortable", bool(sortable))
    return table


def clear_sort(table: QTableWidget) -> None:
    """
    Forget the header sort, so the next refill keeps the screen's own order
    (a "Worst first" / "Newest first" reset). Call the screen's reload after.
    """
    header = table.horizontalHeader()
    was = table.isSortingEnabled()
    table.setSortingEnabled(False)
    header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(was)


# ------------------------------------------------------------------ records
def key_of_row(table: QTableWidget, row: int, key_column: int = KEY_COLUMN):
    """The record a row stands for: its KEY_ROLE, or the key column's text."""
    item = table.item(row, key_column)
    if item is None:
        return None
    key = item.data(KEY_ROLE)
    return key if key is not None else item.text()


def selected_rows(table: QTableWidget) -> List[int]:
    rows = set()
    model = table.selectionModel()
    if model is not None:
        rows = {index.row() for index in model.selectedRows()}
        if not rows:
            rows = {index.row() for index in model.selectedIndexes()}
    return sorted(rows)


def selected_keys(table: QTableWidget, key_column: int = KEY_COLUMN) -> list:
    """The records selected, in on-screen order - never row numbers."""
    return [k for k in (key_of_row(table, r, key_column) for r in selected_rows(table))
            if k is not None]


def row_for_key(table: QTableWidget, key, key_column: int = KEY_COLUMN) -> int:
    for row in range(table.rowCount()):
        if key_of_row(table, row, key_column) == key:
            return row
    return -1


def select_keys(table: QTableWidget, keys: Iterable, key_column: int = KEY_COLUMN,
                current=None) -> int:
    """Select the rows holding these records. Returns how many were found."""
    from PySide6.QtCore import QItemSelection, QItemSelectionModel
    wanted = list(keys or [])
    model = table.selectionModel()
    if model is None:
        return 0
    selection = QItemSelection()
    found = 0
    current_row = -1
    last_col = max(0, table.columnCount() - 1)
    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue            # a record filtered out of sight is not acted on
        key = key_of_row(table, row, key_column)
        if key in wanted:
            selection.select(table.model().index(row, 0), table.model().index(row, last_col))
            found += 1
            if key == current or current_row < 0:
                current_row = row
    model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    if current_row >= 0:
        model.setCurrentIndex(table.model().index(current_row, 0),
                              QItemSelectionModel.SelectionFlag.NoUpdate)
    return found


class KeepSelection:
    """
    Refill a table without losing your place.

        with KeepSelection(self.table):
            self.table.setRowCount(0)
            ... fill ...

    Remembers the selected records (by key, see key_of_row), the current one
    and the scroll position; turns sorting off while rows go in (Qt re-sorts
    after every setItem otherwise, scattering a half-filled row) and back on
    after, in the order the person had chosen; then selects the same records
    again - or nothing, if they are gone. Selection signals are held back
    while it works, so a refill does not look like the person clicked.
    """

    def __init__(self, table: QTableWidget, key_column: int = KEY_COLUMN):
        self.table = table
        self.key_column = key_column

    def __enter__(self):
        t = self.table
        self.keys = selected_keys(t, self.key_column)
        current = t.currentRow()
        self.current = key_of_row(t, current, self.key_column) if current >= 0 else None
        self.vscroll = t.verticalScrollBar().value()
        self.hscroll = t.horizontalScrollBar().value()
        self.sorting = t.isSortingEnabled()
        header = t.horizontalHeader()
        self.sort_column = header.sortIndicatorSection()
        self.sort_order = header.sortIndicatorOrder()
        self._blocked = t.selectionModel().blockSignals(True) if t.selectionModel() else False
        t.setSortingEnabled(False)
        t.setUpdatesEnabled(False)
        return self

    def __exit__(self, exc_type, exc, tb):
        t = self.table
        try:
            if self.sorting:
                t.setSortingEnabled(True)
                if 0 <= self.sort_column < t.columnCount():
                    t.sortItems(self.sort_column, self.sort_order)
            t.clearSelection()
            if self.keys:
                select_keys(t, self.keys, self.key_column, self.current)
        finally:
            if t.selectionModel() is not None:
                t.selectionModel().blockSignals(self._blocked)
            t.setUpdatesEnabled(True)
            t.verticalScrollBar().setValue(self.vscroll)
            t.horizontalScrollBar().setValue(self.hscroll)
            filt = getattr(t, "_slate_filter", None)
            if filt is not None:
                filt.apply()
            # Tell listeners once, now that the selection is final.
            try:
                t.itemSelectionChanged.emit()
            except (AttributeError, RuntimeError):
                pass
        return False


# ------------------------------------------------------------ inline edits
def enable_inline_edits(table: QTableWidget, save: Callable, columns: Iterable[int] = None,
                        key_column: int = KEY_COLUMN) -> None:
    """
    Let chosen columns be edited in place - and save every edit.

    `save(key, column, text)` returns a Result, (ok, message) or a bool. When
    it fails the cell goes back to what it was and the reason is shown; a
    cell is never left showing a value the database does not have.
    """
    from .feedback import Result, toast
    allowed = set(columns) if columns is not None else None
    table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                          | QAbstractItemView.EditTrigger.EditKeyPressed)
    previous = {}

    def remember(item):
        if item is not None:
            previous[(item.row(), item.column())] = item.data(Qt.ItemDataRole.EditRole)

    def changed(item):
        if item is None or getattr(table, "_slate_reverting", False):
            return
        if allowed is not None and item.column() not in allowed:
            return
        before = previous.get((item.row(), item.column()))
        if before == item.data(Qt.ItemDataRole.EditRole):
            return
        key = key_of_row(table, item.row(), key_column)
        try:
            result = Result.from_value(save(key, item.column(), item.text()),
                                       failure_message="The change was not saved.")
        except Exception as exc:
            logger.exception("Inline edit save failed: %s", exc)
            result = Result.failure("The change was not saved.", detail=str(exc))
        if result.ok:
            previous[(item.row(), item.column())] = item.data(Qt.ItemDataRole.EditRole)
            if result.message:
                toast(table, result.message, "success")
            return
        table._slate_reverting = True
        try:
            item.setData(Qt.ItemDataRole.EditRole, before)
        finally:
            table._slate_reverting = False
        toast(table, result.message, "error", details=result.detail)

    table.currentItemChanged.connect(lambda current, _prev: remember(current))
    table.itemDoubleClicked.connect(remember)
    table.itemChanged.connect(changed)
    # Making cells editable emits itemChanged too; that is not an edit.
    table._slate_reverting = True
    try:
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is not None and (allowed is None or col in allowed):
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    remember(item)
    finally:
        table._slate_reverting = False


# --------------------------------------------------------------- filtering
class TableFilter(QObject):
    """
    Hide the rows that do not match: a search box (debounced) over chosen
    columns, plus any number of combo filters and predicates. Re-applied by
    KeepSelection after every refill.
    """

    counted = Signal(int, int)          # visible rows, all rows

    def __init__(self, table: QTableWidget, search: QLineEdit = None,
                 columns: Iterable[int] = None, delay_ms: int = 200):
        super().__init__(table)
        self.table = table
        self.search = search
        self.columns = list(columns) if columns is not None else None
        self._combos = []               # (combo, column, match)
        self._predicates = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(delay_ms))
        self._timer.timeout.connect(self.apply)
        if search is not None:
            search.textChanged.connect(lambda _t: self._timer.start())
        table._slate_filter = self
        model = table.model()
        for signal in ("rowsInserted", "modelReset"):
            try:
                getattr(model, signal).connect(lambda *_: self._timer.start())
            except Exception:
                pass

    def add_combo(self, combo: QComboBox, column: int, match: Callable = None):
        """
        Rows pass when the combo's value (currentData, "" = all) matches the
        column. `match(cell_text, value)` - or `match(cell_text, value, row)`
        when it needs the rest of the row - replaces the plain comparison.
        """
        if match is not None:
            import inspect
            try:
                wants_row = len(inspect.signature(match).parameters) >= 3
            except (TypeError, ValueError):
                wants_row = False
            if wants_row:
                plain = match
                match = lambda cell, value, _row=None, _m=plain: _m(cell, value, _row)
                match._slate_wants_row = True
        self._combos.append((combo, column, match))
        combo.currentIndexChanged.connect(lambda _i: self.apply())

    def add_predicate(self, predicate: Callable[[int], bool]):
        """predicate(row) -> bool; a row must pass every one."""
        self._predicates.append(predicate)

    def _text(self, row, column):
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def row_matches(self, row: int) -> bool:
        text = self.search.text().strip().casefold() if self.search is not None else ""
        if text:
            columns = self.columns if self.columns is not None else range(self.table.columnCount())
            haystack = " ".join(self._text(row, c) for c in columns).casefold()
            if not all(word in haystack for word in text.split()):
                return False
        for combo, column, match in self._combos:
            value = combo.currentData()
            if value in (None, ""):
                continue
            cell = self._text(row, column)
            if match is None:
                ok = cell.casefold() == str(value).casefold()
            elif getattr(match, "_slate_wants_row", False):
                ok = match(cell, value, row)
            else:
                ok = match(cell, value)
            if not ok:
                return False
        return all(predicate(row) for predicate in self._predicates)

    def apply(self):
        try:
            total = self.table.rowCount()
            visible = 0
            hidden = []
            for row in range(total):
                show = self.row_matches(row)
                self.table.setRowHidden(row, not show)
                visible += int(show)
                if not show:
                    hidden.append(row)
            # A row the search hid must not stay selected: Delete or Mark
            # Failed would act on a record nobody can see.
            model = self.table.selectionModel()
            if hidden and model is not None:
                from PySide6.QtCore import QItemSelection, QItemSelectionModel
                drop = QItemSelection()
                last_col = max(0, self.table.columnCount() - 1)
                for row in hidden:
                    drop.select(self.table.model().index(row, 0),
                                self.table.model().index(row, last_col))
                model.select(drop, QItemSelectionModel.SelectionFlag.Deselect)
            self.counted.emit(visible, total)
        except RuntimeError:
            pass

    def active(self) -> bool:
        if self.search is not None and self.search.text().strip():
            return True
        return any(combo.currentData() not in (None, "") for combo, _c, _m in self._combos)

    def clear(self):
        if self.search is not None:
            self.search.clear()
        for combo, _c, _m in self._combos:
            combo.setCurrentIndex(0)
        self.apply()


def row_count(visible: int, total: int, noun: str = "") -> str:
    """'41 of 42 machines', '6 licences', '' for none - one wording for every screen."""
    if not total:
        return ""
    words = (" %s%s" % (noun, "" if total == 1 else "s")) if noun else ""
    return ("%d of %d%s" % (visible, total, words)) if visible != total else ("%d%s" % (total, words))


class TableToolbar(QWidget):
    """
    Search, filters and Refresh above a table, with "12 of 40" beside them.

    `on_refresh` is the tab's own reload (a Refresh button and F5 both call
    it). Filters are added with add_filter().
    """

    def __init__(self, table: QTableWidget, placeholder: str = "Search…",
                 columns: Iterable[int] = None, on_refresh: Callable = None,
                 parent=None, noun: str = ""):
        super().__init__(parent)
        self.noun = noun
        from slate.core.infra.gate import Gate
        from slate.gui.core.controls import make_button
        self.table = table
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Gate.SPACE_2)

        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        row.addWidget(self.search, 1)

        self.filter = TableFilter(table, self.search, columns)
        self._filters_row = QHBoxLayout()
        self._filters_row.setSpacing(Gate.SPACE_2)
        row.addLayout(self._filters_row)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        row.addWidget(self.count_label)
        self.filter.counted.connect(self._show_count)

        self.refresh_button = None
        if on_refresh is not None:
            self.refresh_button = make_button("Refresh", "secondary",
                                              tooltip="Read the latest from the database (F5)",
                                              on_click=on_refresh)
            try:
                from slate.gui.core.icons import icon
                self.refresh_button.setIcon(icon("refresh"))
            except Exception:
                pass
            row.addWidget(self.refresh_button)

    def add_filter(self, label: str, options, column: int, match: Callable = None) -> QComboBox:
        """
        A combo filter. `options` is [(text, value)]; the first should be the
        "All ..." entry with value "".
        """
        combo = QComboBox()
        combo.setToolTip(label)
        combo.setAccessibleName(label)
        for text, value in options:
            combo.addItem(str(text), value)
        self._filters_row.addWidget(combo)
        self.filter.add_combo(combo, column, match)
        return combo

    def _show_count(self, visible: int, total: int):
        self.count_label.setText(row_count(visible, total, self.noun))
