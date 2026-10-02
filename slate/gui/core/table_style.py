"""
One way a data table looks and behaves.

Before this, tables were styled four or five different ways: Hardware and
Deployment in amber (the warning colour) with ALL-CAPS headers, Licences and
IT Support at 10 px with no cell padding, Attendance and Users with their own
grey upper-case headers, Scheduling and Bidding with every column stretched to
the same width and row numbers down the side that looked like ids. Each tab's
own stylesheet also stopped Qt from drawing the colours it set on cells, and
the global theme then overrode each tab's selection colour anyway.

The look (header, selection, padding, text colours) is in main.qss and applies
to every table on its own. This module does what a stylesheet cannot: row
height, column sizing, hiding the row numbers, and the behaviour flags.

    from slate.gui.core.table_style import style_table, set_cell_status

    style_table(self.table, columns={
        "Code": "contents",
        "Milestone": "stretch",
        "Due": "contents",
        "Budget": "numeric",
    })
    set_cell_status(item, "bad")        # a red cell that stays red
    dim_cell(item)                      # 'Unassigned', '-', and other placeholders

Column roles:
    stretch     takes the spare width (the name or description column)
    contents    as wide as its content (codes, dates, statuses, short names)
    numeric     as wide as its content, right-aligned (counts, money, sizes)
    interactive a starting width the user can drag; give it as ("interactive", 180)
    fixed       a fixed width: ("fixed", 90)
Unlisted columns are "interactive". If nothing stretches, the last column does.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QStyledItemDelegate, QTableView, QTableWidgetItem,
)

from slate.core.infra.gate import Gate


class _AlignDelegate(QStyledItemDelegate):
    """Draws a column's text with a fixed alignment (numbers to the right)."""

    def __init__(self, alignment, parent=None):
        super().__init__(parent)
        self._alignment = alignment

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.displayAlignment = self._alignment


def _header_labels(table):
    model = table.model()
    labels = []
    if model is None:
        return labels
    for column in range(model.columnCount()):
        value = model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        labels.append(str(value) if value is not None else "")
    return labels


def _role_map(table, columns):
    """columns -> {column index: (role, width)}. Accepts names or indexes."""
    if not columns:
        return {}
    if isinstance(columns, (list, tuple)):
        columns = dict(enumerate(columns))
    labels = [label.strip().lower() for label in _header_labels(table)]
    resolved = {}
    for key, role in columns.items():
        if isinstance(key, int):
            index = key
        else:
            try:
                index = labels.index(str(key).strip().lower())
            except ValueError:
                continue
        width = None
        if isinstance(role, (list, tuple)):
            role, width = role[0], (role[1] if len(role) > 1 else None)
        resolved[index] = (str(role or "interactive").lower(), width)
    return resolved


def style_table(table: QTableView, columns=None, *, editable: bool = False,
                select_rows: bool = True, multi_select: bool = None,
                alternating: bool = True, sortable: bool = None,
                row_height: int = None, clear_local_style: bool = True,
                word_wrap: bool = False) -> QTableView:
    """
    Give a QTableView / QTableWidget the product's table behaviour.

    Call it after the columns exist (after setColumnCount/setHorizontalHeaderLabels
    or setModel). Safe to call again when the columns change.

    clear_local_style removes the table's own stylesheet, which is what the
    shared look needs: a per-table sheet overrides main.qss for that table and
    hides cell colours. Pass False only for a table that paints something main.qss
    cannot express.
    """
    if clear_local_style:
        table.setStyleSheet("")

    if select_rows:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    if multi_select is not None:
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection if multi_select
            else QAbstractItemView.SelectionMode.SingleSelection)
    if not editable:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(alternating)
    table.setWordWrap(word_wrap)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setShowGrid(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    if sortable is not None:
        table.setSortingEnabled(sortable)

    # Row numbers change on every sort and reload; they are not ids and should
    # not look like them.
    rows = table.verticalHeader()
    rows.setVisible(False)
    rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    rows.setDefaultSectionSize(row_height or Gate.ROW_HEIGHT)
    rows.setMinimumSectionSize(min(row_height or Gate.ROW_HEIGHT, 22))

    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setMinimumSectionSize(48)
    header.setStretchLastSection(False)

    roles = _role_map(table, columns)
    count = table.model().columnCount() if table.model() is not None else 0
    stretched = False
    for column in range(count):
        role, width = roles.get(column, ("interactive", None))
        if role == "stretch":
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            stretched = True
        elif role in ("contents", "status"):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        elif role == "numeric":
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            table.setItemDelegateForColumn(
                column, _AlignDelegate(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, table))
        elif role == "fixed":
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            if width:
                table.setColumnWidth(column, int(width))
        else:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            if width:
                table.setColumnWidth(column, int(width))
    if count and not stretched:
        header.setStretchLastSection(True)
    return table


# ----------------------------------------------------------------- cells
_STATUS = {
    "ok": "OK", "success": "OK", "done": "OK",
    "warn": "WARN", "warning": "WARN", "pending": "WARN",
    "bad": "BAD", "error": "BAD", "conflict": "BAD", "danger": "BAD",
    "info": "INFO", "review": "INFO",
    "accent": "ACCENT", "change": "ACCENT",
    "idle": "IDLE", "muted": "IDLE",
}


def status_colour(kind: str) -> str:
    """The Gate colour for a status word ('ok', 'warn', 'bad', 'info', 'accent', 'idle')."""
    return getattr(Gate, _STATUS.get(str(kind or "").strip().lower(), "TEXT"))


def set_cell_status(item: QTableWidgetItem, kind: str, background: bool = True,
                    alpha: float = 0.16) -> QTableWidgetItem:
    """
    Colour one cell by meaning: its text in the status colour and, unless
    background=False, a light wash of the same colour behind it. Works because
    main.qss never styles a bare ::item (which would make Qt drop the wash).
    """
    colour = status_colour(kind)
    item.setForeground(QBrush(QColor(colour)))
    if background:
        wash = QColor(colour)
        wash.setAlphaF(alpha)
        item.setBackground(QBrush(wash))
    item.setData(Qt.ItemDataRole.UserRole + 99, str(kind or ""))
    return item


def clear_cell_status(item: QTableWidgetItem) -> QTableWidgetItem:
    item.setData(Qt.ItemDataRole.ForegroundRole, None)
    item.setData(Qt.ItemDataRole.BackgroundRole, None)
    item.setData(Qt.ItemDataRole.UserRole + 99, None)
    return item


def dim_cell(item: QTableWidgetItem) -> QTableWidgetItem:
    """A placeholder value ('Unassigned', '-', 'Not set') in dim text, not data text."""
    item.setForeground(QBrush(QColor(Gate.TEXT_DIM)))
    return item


_SORT_KEY = Qt.ItemDataRole.UserRole + 98


class NumericItem(QTableWidgetItem):
    """A right-aligned cell that sorts by its number, not by its text ("9" < "10")."""

    def __init__(self, value, text: str = None):
        super().__init__(text if text is not None else ("" if value is None else str(value)))
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        try:
            key = float(value)
        except (TypeError, ValueError):
            key = float("-inf")
        self.setData(_SORT_KEY, key)

    def __lt__(self, other):
        mine, theirs = self.data(_SORT_KEY), other.data(_SORT_KEY) if other is not None else None
        if mine is not None and theirs is not None:
            return mine < theirs
        return super().__lt__(other)


def numeric_item(value, text: str = None) -> QTableWidgetItem:
    """A right-aligned cell that sorts by its number, not by its text."""
    return NumericItem(value, text)
