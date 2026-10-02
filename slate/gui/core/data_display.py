"""
The Qt side of the shared data formats: date pickers, table cells and export.

The rules themselves are plain Python in slate.core.domain (dates, money,
table_export, people) so they can be tested without a window. This module
puts them on widgets, so every screen gets the same thing:

    from slate.gui.core.data_display import (
        setup_date_edit, date_item, datetime_item, money_item, number_item,
        export_table_dialog)

    edit = QDateEdit(); setup_date_edit(edit)      # '3 Oct 2026', weeks start Monday
    table.setItem(r, c, date_item(row["start_date"]))
    table.setItem(r, c, money_item(row["budget"], row["currency"]))
    export_table_dialog(self, table, "licences")   # CSV/XLSX of what is on screen

Table cells sort by value, not by text. "12 Sep 2026" sorts before
"3 Oct 2026" as text; a date_item sorts by the date, a money_item by the
amount. Turn sorting on with table.setSortingEnabled(True) as usual.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from PySide6.QtCore import QDate, QDateTime, Qt, QTime
from PySide6.QtWidgets import (
    QCalendarWidget, QDateEdit, QDateTimeEdit, QFileDialog, QMessageBox, QTableWidgetItem,
)

from slate.core.domain import dates as _dates
from slate.core.domain import money as _money

logger = logging.getLogger(__name__)

# The role a sortable item keeps its sort key in.
SORT_ROLE = Qt.ItemDataRole.UserRole + 101


# ------------------------------------------------------------------ dates

def to_qdate(value) -> QDate:
    """A QDate from a date, datetime, ISO text or QDate; an invalid QDate for none."""
    if isinstance(value, QDate):
        return value
    d = _dates.parse_date(value)
    return QDate(d.year, d.month, d.day) if d else QDate()


def from_qdate(value: QDate) -> Optional[date]:
    """A datetime.date from a QDate (None when the QDate is invalid)."""
    if value is None or not value.isValid():
        return None
    return date(value.year(), value.month(), value.day())


def setup_calendar(calendar: Optional[QCalendarWidget]) -> None:
    """Weeks start on Monday, and no ISO week numbers down the side."""
    if calendar is None:
        return
    calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)


def setup_date_edit(edit: QDateEdit, *, weekday: bool = False, popup: bool = True) -> QDateEdit:
    """
    The studio's date picker: '3 Oct 2026' (or 'Sat 3 Oct 2026'), a calendar
    popup, and weeks starting on Monday. Returns the same widget.
    """
    edit.setDisplayFormat(_dates.QT_DATE_FORMAT_WEEKDAY if weekday else _dates.QT_DATE_FORMAT)
    if popup:
        edit.setCalendarPopup(True)
        setup_calendar(edit.calendarWidget())
    return edit


def setup_datetime_edit(edit: QDateTimeEdit, *, popup: bool = True) -> QDateTimeEdit:
    edit.setDisplayFormat(_dates.QT_DATETIME_FORMAT)
    if popup:
        edit.setCalendarPopup(True)
        setup_calendar(edit.calendarWidget())
    return edit


def date_edit(value=None, *, weekday: bool = False, parent=None) -> QDateEdit:
    """A new QDateEdit set up the studio's way, showing value (default today)."""
    edit = QDateEdit(parent)
    setup_date_edit(edit, weekday=weekday)
    qd = to_qdate(value) if value is not None else QDate.currentDate()
    edit.setDate(qd if qd.isValid() else QDate.currentDate())
    return edit


# ------------------------------------------------------------------ table cells

class SortableItem(QTableWidgetItem):
    """A table cell that sorts by the key stored in SORT_ROLE, not by its text."""

    def __lt__(self, other):
        mine = self.data(SORT_ROLE)
        theirs = other.data(SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        return super().__lt__(other)


def _item(text: str, key) -> SortableItem:
    item = SortableItem(text)
    item.setData(SORT_ROLE, key)
    return item


def date_item(value, *, weekday: bool = False, empty: str = _dates.MISSING) -> SortableItem:
    """A cell showing '3 Oct 2026', sorted by date. The ISO date is its tooltip."""
    text = _dates.format_date(value, weekday=weekday, empty=empty)
    item = _item(text, _dates.date_sort_key(value))
    iso = _dates.to_iso(value)
    if iso:
        item.setToolTip(iso)
    return item


def datetime_item(value, *, seconds: bool = False, relative: bool = False,
                  empty: str = _dates.MISSING) -> SortableItem:
    """
    A cell showing '17 Sep 2026, 23:02' (or '5 min ago' with relative=True,
    the full time then in the tooltip), sorted by the moment.
    """
    full = _dates.format_datetime(value, seconds=seconds, empty=empty)
    text = _dates.format_age(value, empty=empty) if relative else full
    item = _item(text, _dates.date_sort_key(value))
    if relative and full:
        item.setToolTip(full)
    return item


def money_item(amount, currency: Optional[str] = None, *, compact: bool = False) -> SortableItem:
    """
    A right-aligned cell showing the amount in its currency ('₹2,07,00,000.00'),
    sorted by the amount. A compact cell carries the exact figure as tooltip.
    """
    exact = _money.format_money(amount, currency)
    text = _money.format_money(amount, currency, compact=True) if compact else exact
    try:
        key = float(_money.to_decimal(amount))
    except ValueError:
        key = 0.0
    item = _item(text, key)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if compact:
        item.setToolTip(exact)
    return item


def number_item(value, fmt: str = "{:,}", *, suffix: str = "", empty: str = _dates.MISSING) -> SortableItem:
    """A right-aligned number cell that sorts numerically."""
    if value is None or value == "":
        item = _item(empty, float("-inf"))
    else:
        try:
            number = float(value) if not isinstance(value, Decimal) else float(value)
            item = _item(fmt.format(value) + suffix, number)
        except (TypeError, ValueError):
            item = _item(str(value), float("-inf"))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def text_item(value, *, empty: str = "") -> SortableItem:
    """A plain cell that sorts case-insensitively."""
    text = "" if value is None else str(value)
    return _item(text or empty, text.lower())


# ------------------------------------------------------------------ export

def export_table_dialog(parent, table, name: str, *, title: str = "Export") -> Optional[str]:
    """
    Ask where to save, then write what the table shows - the visible columns
    and rows, in the order on screen - as CSV (UTF-8, opens in Excel) or
    XLSX. Returns the path written, or None. Tells the person either way.
    """
    from slate.core.domain import table_export

    default = table_export.default_filename(name)
    path, chosen = QFileDialog.getSaveFileName(
        parent, title, default,
        "CSV (opens in Excel) (*.csv);;Excel workbook (*.xlsx)")
    if not path:
        return None
    if chosen.startswith("Excel") and not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    elif not path.lower().endswith((".csv", ".xlsx")):
        path += ".csv"

    headers, rows = table_export.rows_from_qtable(table)
    try:
        written = table_export.export_rows(path, headers, rows)
    except Exception as exc:
        logger.exception("Export to %s failed", path)
        QMessageBox.warning(parent, "Not exported",
                            f"The file could not be written:\n{exc}")
        return None
    QMessageBox.information(parent, "Exported",
                            f"{written} row{'s' if written != 1 else ''} saved to\n{path}")
    return path


def select_row_by_id(table, record_id, column: int = 0) -> bool:
    """
    Select and show the row whose id cell (column, often hidden) holds
    record_id. For after a save: the new or edited record is where the
    person's eye goes. True when found.
    """
    wanted = str(record_id)
    for r in range(table.rowCount()):
        item = table.item(r, column)
        if item is not None and item.text() == wanted:
            table.selectRow(r)
            table.scrollToItem(item)
            return True
    return False
