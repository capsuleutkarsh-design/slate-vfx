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
    QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from slate.core.infra.gate import Gate
from slate.core.infra.leave_repository import LeaveRepository
from slate.core.domain import leave_policy as lp
from ..core.controls import make_button
from slate.gui.core.offline_notice import on_database_error


class HolidayEditDialog(QDialog):
    """
    One holiday, on its own, so it can be corrected rather than re-entered.

    Editing did not exist. A date announced wrongly or a name typed wrongly had
    to be removed and added back - two steps, of which the destructive one
    succeeds on its own. An interruption between them loses the day silently,
    and every leave request spanning it quietly changes price.
    """

    def __init__(self, locations, row=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit holiday" if row else "Add holiday")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_2)

        self.day = QDateEdit()
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("d MMMM yyyy")
        form.addRow("Date", self.day)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Diwali, Republic Day...")
        form.addRow("Holiday", self.name)

        self.location = QComboBox()
        self.location.setEditable(True)
        self.location.addItem("All")
        for place in locations:
            self.location.addItem(place)
        self.location.setToolTip(
            "All means everybody. A place name means only the people whose "
            "record says that place, spelled the same way.")
        form.addRow("Applies to", self.location)
        root.addLayout(form)

        if row:
            existing = row.get("holiday_date")
            existing = existing.date() if hasattr(existing, "date") else existing
            if isinstance(existing, date):
                self.day.setDate(QDate(existing.year, existing.month, existing.day))
            self.name.setText(str(row.get("name") or ""))
            self.location.setCurrentText(str(row.get("location") or "All"))
        else:
            self.day.setDate(QDate.currentDate())

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "secondary", on_click=self.reject))
        buttons.addWidget(make_button("Save", "primary", on_click=self._accept))
        root.addLayout(buttons)

    def _accept(self):
        if not self.name.text().strip():
            QMessageBox.information(self, "Name it", "A holiday needs a name.")
            return
        self.accept()

    def values(self) -> dict:
        d = self.day.date()
        return {
            "holiday_date": date(d.year(), d.month(), d.day()),
            "name": self.name.text().strip(),
            "location": self.location.currentText().strip() or "All",
        }


class HolidayCalendarDialog(QDialog):
    """The studio's public holidays - the list the day count is charged against."""

    ANY_YEAR = "All years"

    def __init__(self, repo: LeaveRepository = None, parent=None, year=None):
        super().__init__(parent)
        self.repo = repo or LeaveRepository()
        self.setWindowTitle("Holiday calendar")
        self.resize(660, 560)
        self._rows = []
        self._locations = []
        self._wanted_year = year

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

        # The calendar is every holiday the studio has ever had. Without this
        # the year somebody came to fix is somewhere in the middle of it.
        filter_row = QHBoxLayout()
        filter_row.setSpacing(Gate.SPACE_2)
        filter_row.addWidget(QLabel("Year"))
        self.combo_year = QComboBox()
        self.combo_year.setMinimumWidth(120)
        self.combo_year.currentIndexChanged.connect(lambda *_: self.refresh())
        filter_row.addWidget(self.combo_year)
        filter_row.addStretch(1)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        filter_row.addWidget(self.lbl_count)
        root.addLayout(filter_row)

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

        # Offered from the places the studio's own user records name, rather
        # than a fixed list of three cities belonging to whoever this was
        # written for. A holiday applies to a location only when the spelling
        # matches, so guessing it is worse than not offering it.
        self.location = QComboBox()
        self.location.setEditable(True)
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
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.btn_edit = make_button("Edit", "secondary", on_click=self.edit)
        self.btn_remove = make_button("Remove", "danger", on_click=self.remove)
        buttons.addWidget(self.btn_edit)
        buttons.addWidget(self.btn_remove)
        buttons.addStretch(1)
        buttons.addWidget(make_button("Done", "secondary", on_click=self.accept))
        root.addLayout(buttons)

        self._load_years()
        self.refresh()

    # ------------------------------------------------------------- loading

    @on_database_error
    def _load_years(self):
        """Which years to offer, including the ones nothing is booked in yet."""
        this_year = date.today().year
        years = set(self.repo.holiday_years())
        years.update({this_year, this_year + 1})

        self.combo_year.blockSignals(True)
        self.combo_year.clear()
        self.combo_year.addItem(self.ANY_YEAR, None)
        for year in sorted(years, reverse=True):
            self.combo_year.addItem(str(year), year)
        wanted = self._wanted_year or this_year
        index = self.combo_year.findData(wanted)
        self.combo_year.setCurrentIndex(index if index >= 0 else 0)
        self.combo_year.blockSignals(False)

    @on_database_error
    def refresh(self):
        self._locations = self.repo.locations()
        current = self.location.currentText()
        self.location.clear()
        self.location.addItem("All")
        for place in self._locations:
            self.location.addItem(place)
        if current:
            self.location.setCurrentText(current)

        year = self.combo_year.currentData() if self.combo_year.count() else None
        self._rows = self.repo.holiday_rows(year)
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

        self.lbl_count.setText(
            "%d holiday%s" % (len(self._rows), "" if len(self._rows) == 1 else "s"))
        self._sync()

    def _sync(self, *_):
        picked = bool(self.table.selectedIndexes())
        self.btn_remove.setEnabled(picked)
        self.btn_edit.setEnabled(picked)

    def _selected(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._rows[r] for r in rows if r < len(self._rows)]

    # ------------------------------------------------------------- editing

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
        self._load_years()
        self.refresh()

    def edit(self):
        picked = self._selected()
        if len(picked) != 1:
            QMessageBox.information(
                self, "Pick one", "Choose a single holiday to edit.")
            return

        row = picked[0]
        dialog = HolidayEditDialog(self._locations, row, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        if not self.repo.update_holiday(row.get("id"), values["holiday_date"],
                                        values["name"], values["location"]):
            QMessageBox.warning(
                self, "Not saved",
                "That change could not be saved. If another holiday is already "
                "on that date for the same place, this one cannot move onto it.")
        self._load_years()
        self.refresh()

    def remove(self):
        picked = self._selected()
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
        self._load_years()
        self.refresh()


class CompOffReviewDialog(QDialog):
    """
    What the attendance record says people have earned back, before it is given.

    The service that works this out has been in the codebase all along and
    nothing ever called it, so comp-off was credited to nobody: the ledger the
    balance reads from stayed empty for ever and the figure on the artist's
    screen was always zero.

    Shown as a preview first, and every row says why it qualified. A ledger
    nobody can audit is worse than no ledger, because people believe it.
    """

    def __init__(self, repo: LeaveRepository = None, parent=None):
        super().__init__(parent)
        self.repo = repo or LeaveRepository()
        self.setWindowTitle("Comp off earned")
        self.resize(680, 520)
        self._entries = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        root.addWidget(self.note)

        picker = QHBoxLayout()
        picker.setSpacing(Gate.SPACE_2)
        picker.addWidget(QLabel("Look back"))
        self.window_pick = QComboBox()
        for label, days in (("90 days", 90), ("30 days", 30), ("A year", 365)):
            self.window_pick.addItem(label, days)
        self.window_pick.currentIndexChanged.connect(self.refresh)
        picker.addWidget(self.window_pick)
        picker.addStretch(1)
        root.addLayout(picker)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Person", "Day", "Hours", "Earns", "Why"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        for i in range(4):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Close", "ghost", on_click=self.reject))
        self.btn_credit = make_button("Credit these", "primary", on_click=self.credit)
        buttons.addWidget(self.btn_credit)
        root.addLayout(buttons)

        self.refresh()

    def _service(self):
        from slate.core.domain.comp_off_service import CompOffService
        return CompOffService(repo=self.repo)

    @on_database_error
    def refresh(self, *_):
        from datetime import timedelta

        rules = lp.policy(None)
        if not rules.get("comp_off_enabled"):
            self.note.setText(
                "This studio does not operate comp off, so nothing is earned back "
                "for working a day off. Turn it on in the studio policy first.")
            self.btn_credit.setEnabled(False)
            self.table.setRowCount(0)
            self._entries = []
            return

        days = self.window_pick.currentData() or 90
        since = date.today() - timedelta(days=int(days))
        self._entries = self._service().review(since)

        if not self._entries:
            self.note.setText(
                "Nothing in the last %d days qualifies. Comp off is earned by "
                "working a weekly off, working a public holiday, or a long "
                "enough day - and a day already credited is never counted twice."
                % days)
        else:
            total = sum(float(e["days"]) for e in self._entries)
            self.note.setText(
                "%d day(s) of work qualify, worth %g day(s) of comp off in total. "
                "Nothing is credited until you confirm."
                % (len(self._entries), total))
        self.btn_credit.setEnabled(bool(self._entries))

        self.table.setRowCount(len(self._entries))
        for r, entry in enumerate(self._entries):
            cells = [
                str(entry.get("user_id") or ""),
                str(entry.get("day") or ""),
                "%.1f" % float(entry.get("hours") or 0),
                "%g" % float(entry.get("days") or 0),
                str(entry.get("reason") or ""),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (2, 3):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 3:
                    item.setForeground(QColor(Gate.OK))
                self.table.setItem(r, c, item)

    def credit(self):
        if not self._entries:
            return
        total = sum(float(e["days"]) for e in self._entries)
        if QMessageBox.question(
            self, "Credit comp off",
            "Credit %g day(s) of comp off to %d entry(ies)?\n\nEach one is "
            "written to the ledger with its reason and an expiry, and a day "
            "already credited is never credited twice."
            % (total, len(self._entries)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        written = self._service().credit(self._entries)
        QMessageBox.information(
            self, "Credited",
            "%d of %d written to the ledger." % (written, len(self._entries)))
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
