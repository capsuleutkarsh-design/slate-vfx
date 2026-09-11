"""
The two things only HR do to leave: keep the calendar, and close the year.

Both were built into the engine and reachable from nothing. A holiday table
that no screen can edit is a table somebody edits in the database; a
carry-forward cap that nothing ever runs is a policy the studio believes it has
and does not.

The year end is destructive - it is the moment leave people thought they had
stops existing - so it is shown as a preview first and never applied from a
single click.
"""

from datetime import date

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from slate.core.infra.gate import Gate
from slate.core.infra.leave_repository import LeaveRepository
from slate.core.domain import leave_policy as lp
from ..core.controls import make_button
from slate.gui.core.offline_notice import on_database_error


class HolidayCalendarDialog(QDialog):
    """The studio's public holidays - the list the day count is charged against."""

    def __init__(self, repo: LeaveRepository = None, parent=None):
        super().__init__(parent)
        self.repo = repo or LeaveRepository()
        self.setWindowTitle("Holiday calendar")
        self.resize(620, 520)
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        note = QLabel(
            "Every day on this list is free for the studio, and the sandwich rule "
            "is measured against it. Getting it wrong changes what leave costs, so "
            "it is worth doing in one sitting at the start of the year.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        root.addWidget(note)

        entry = QHBoxLayout()
        entry.setSpacing(Gate.SPACE_2)

        self.day = QDateEdit()
        self.day.setCalendarPopup(True)
        self.day.setDate(QDate.currentDate())
        entry.addWidget(self.day)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Diwali, Republic Day...")
        self.name.returnPressed.connect(self.add)
        entry.addWidget(self.name, 1)

        self.location = QComboBox()
        self.location.setEditable(True)
        self.location.addItems(["All", "Mumbai", "Chennai", "Remote"])
        entry.addWidget(self.location)

        entry.addWidget(make_button("Add", "primary", on_click=self.add))
        root.addLayout(entry)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Day", "Holiday", "Applies to"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        for i in range(3):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._sync)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.btn_remove = make_button("Remove", "danger", on_click=self.remove)
        buttons.addWidget(self.btn_remove)
        buttons.addStretch(1)
        buttons.addWidget(make_button("Done", "secondary", on_click=self.accept))
        root.addLayout(buttons)

        self.refresh()

    @on_database_error
    def refresh(self):
        self._rows = self.repo.holiday_rows()
        self.table.setRowCount(len(self._rows))
        today = date.today()
        for r, row in enumerate(self._rows):
            day = row.get("holiday_date")
            day = day.date() if hasattr(day, "date") else day
            try:
                weekday = day.strftime("%A")
            except Exception:
                weekday = ""
            cells = [str(day), weekday, row.get("name") or "", row.get("location") or "All"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                # A holiday already behind us is history, not something to plan
                # around - dimming it keeps the eye on the rest of the year.
                if isinstance(day, date) and day < today:
                    item.setForeground(QColor(Gate.TEXT_DIM))
                self.table.setItem(r, c, item)
        self._sync()

    def _sync(self, *_):
        self.btn_remove.setEnabled(bool(self.table.selectedIndexes()))

    def add(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.information(self, "Name it", "A holiday needs a name.")
            return
        d = self.day.date()
        if not self.repo.add_holiday(date(d.year(), d.month(), d.day()), name,
                                     self.location.currentText().strip() or "All"):
            QMessageBox.warning(self, "Not saved", "That holiday could not be added.")
        self.name.clear()
        self.refresh()

    def remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        picked = [self._rows[r] for r in rows if r < len(self._rows)]
        if not picked:
            return
        if QMessageBox.question(
            self, "Remove holiday",
            "Remove %d holiday(s)? Leave already taken keeps the day count it was "
            "charged at - only future requests change." % len(picked),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        for row in picked:
            self.repo.remove_holiday(row.get("id"))
        self.refresh()


class YearEndDialog(QDialog):
    """Close a leave year: carry what the cap allows, lapse the rest."""

    def __init__(self, username: str, repo: LeaveRepository = None, parent=None):
        super().__init__(parent)
        self.username = username
        self.repo = repo or LeaveRepository()
        self.setWindowTitle("Close a leave year")
        self.resize(640, 520)
        self._preview = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        cap = lp.policy(None)["carry_forward_cap"]
        note = QLabel(
            "Closing a year draws a line under it. Up to %g day(s) carry into the "
            "next year and anything above that is lost. Nothing is written until "
            "you confirm, and a year already closed is never closed twice."
            % cap)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        root.addWidget(note)

        picker = QHBoxLayout()
        picker.setSpacing(Gate.SPACE_2)
        picker.addWidget(QLabel("Leave year"))
        self.year = QComboBox()
        this_year = date.today().year
        for y in range(this_year, this_year - 5, -1):
            self.year.addItem(str(y), y)
        # Default to the year just gone: closing the year you are still in
        # lapses leave people have not had a chance to take.
        self.year.setCurrentIndex(1 if self.year.count() > 1 else 0)
        self.year.currentIndexChanged.connect(self.refresh)
        picker.addWidget(self.year)
        picker.addStretch(1)
        root.addLayout(picker)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(self.state)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Person", "Balance at year end", "Carries over", "Lapses"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.btn_close = make_button("Close the year", "danger", on_click=self.close_year)
        buttons.addWidget(self.btn_close)
        root.addLayout(buttons)

        self.refresh()

    @on_database_error
    def refresh(self, *_):
        year = self.year.currentData()
        done = self.repo.closes(year)
        self._preview = self.repo.preview_close(year)

        closed = {str(r["user_id"]).lower() for r in done}
        lapsing = sum(1 for r in self._preview
                      if r["user_id"].lower() not in closed and r["lapsed"] > 0)

        if closed and len(closed) >= len(self._preview):
            self.state.setText("%d already closed. There is nothing left to do for %d."
                               % (len(closed), year))
            self.btn_close.setEnabled(False)
        else:
            self.state.setText(
                "%d person(s) to close for %d. %d of them will lose leave."
                % (len(self._preview) - len(closed), year, lapsing))
            self.btn_close.setEnabled(True)

        self.table.setRowCount(len(self._preview))
        for r, row in enumerate(self._preview):
            already = row["user_id"].lower() in closed
            cells = [
                row["user_id"] + ("  (already closed)" if already else ""),
                "%g" % row["closing_balance"],
                "%g" % row["carried"],
                "%g" % row["lapsed"],
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if already:
                    item.setForeground(QColor(Gate.TEXT_DIM))
                elif c == 3 and row["lapsed"] > 0:
                    item.setForeground(QColor(Gate.BAD))
                elif c == 2:
                    item.setForeground(QColor(Gate.OK))
                self.table.setItem(r, c, item)

    def close_year(self):
        year = self.year.currentData()
        lapsing = sum(r["lapsed"] for r in self._preview)
        if QMessageBox.question(
            self, "Close %d" % year,
            "This writes the year end for %d person(s) and lapses %g day(s) in "
            "total.\n\nIt cannot be undone from this screen. Go ahead?"
            % (len(self._preview), lapsing),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        result = self.repo.close_year(year, self.username)
        QMessageBox.information(
            self, "Year closed",
            "%d closed, %d were already done." % (result["closed"], result["skipped"]))
        self.refresh()
