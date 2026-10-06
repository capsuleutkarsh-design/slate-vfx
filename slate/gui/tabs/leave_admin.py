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
    QCheckBox, QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from slate.core.infra.gate import Gate
from ..core.table_style import style_table
from slate.core.infra.leave_repository import LeaveRepository
from slate.core.infra.db_results import DatabaseReadError
from slate.core.domain import leave_policy as lp
from ..core.controls import make_button
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.data_display import date_item, setup_date_edit
from slate.core.domain.dates import format_date
from slate.core.domain import people


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

        # The same date format as the calendar's table and its date box - this
        # dialog spelled the month out, the box showed 30-09-2026 and the
        # table 2026-09-30.
        self.day = setup_date_edit(QDateEdit(), weekday=True)
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
        # "Holidays" everywhere: the Attendance button said HOLIDAYS, the
        # Leave button "Calendar" and this title "Holiday calendar".
        self.setWindowTitle("Holidays")
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

        # Each field of the add row is labelled - the date and the "All" box
        # had no labels, so nothing said what the second box meant.
        entry.addWidget(QLabel("Date"))
        self.day = setup_date_edit(QDateEdit(), weekday=True)
        self.day.setDate(QDate.currentDate())
        entry.addWidget(self.day)

        entry.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Diwali, Republic Day...")
        self.name.returnPressed.connect(self.add)
        entry.addWidget(self.name, 1)

        # Offered from the places the studio's own user records name, rather
        # than a fixed list of three cities belonging to whoever this was
        # written for. A holiday applies to a location only when the spelling
        # matches, so guessing it is worse than not offering it.
        entry.addWidget(QLabel("Applies to"))
        self.location = QComboBox()
        self.location.setEditable(True)
        self.location.setToolTip(
            "All means everybody. A place name means only the people whose "
            "record says that place, spelled the same way.")
        entry.addWidget(self.location)

        entry.addWidget(make_button("Add", "primary", on_click=self.add))
        root.addLayout(entry)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Day", "Holiday", "Applies to"])
        style_table(self.table, {"Date": "contents", "Day": "contents",
                                 "Holiday": "contents", "Applies to": "stretch"})
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
            cells = [format_date(day), weekday, row.get("name") or "", row.get("location") or "All"]
            for c, text in enumerate(cells):
                item = date_item(day) if c == 0 else QTableWidgetItem(text)
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

    def _place(self, typed):
        """
        The place in the studio's own spelling ('mumbai' -> 'Mumbai'), or None
        when HR decline a place nobody's record names - such a holiday applies
        to nobody, and was accepted without a word.
        """
        place = str(typed or "").strip() or "All"
        if place.lower() == "all":
            return "All"
        for known in self._locations:
            if known.lower() == place.lower():
                return known
        if QMessageBox.question(
            self, "Nobody works there",
            "Nobody's record says %s, so this holiday would apply to nobody. "
            "Save it anyway?" % place,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return None
        return place

    def add(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.information(self, "Name it", "A holiday needs a name.")
            return
        d = self.day.date()
        day = date(d.year(), d.month(), d.day())
        place = self._place(self.location.currentText())
        if place is None:
            return
        if not self.repo.add_holiday(day, name, place):
            # Most often it is already there: the insert skips a holiday on a
            # date that place already has, which used to look like success -
            # the name was cleared and nothing appeared. Say which, and keep
            # the typed name so nothing has to be typed again.
            clash = [r for r in self.repo.holiday_rows(day.year)
                     if str(r.get("holiday_date"))[:10] == day.isoformat()
                     and str(r.get("location") or "All").lower() == place.lower()]
            if clash:
                QMessageBox.information(
                    self, "Already on the calendar",
                    "There is already a holiday on %s for %s: %s."
                    % (format_date(day), place, clash[0].get("name") or "unnamed"))
            else:
                QMessageBox.warning(self, "Not saved", "That holiday could not be added.")
            return
        self.name.clear()
        # Show it: a holiday added for another year used to vanish from a
        # list filtered to this one, which read as "it did not save".
        self._wanted_year = day.year
        self._load_years()
        self.refresh()
        self._select_day(day, place)
        self._recharge(day, day)

    def _select_day(self, day, place):
        for r, row in enumerate(self._rows):
            if str(row.get("holiday_date"))[:10] == day.isoformat() and                     str(row.get("location") or "All").lower() == place.lower():
                self.table.selectRow(r)
                self.table.scrollToItem(self.table.item(r, 0))
                return

    def _recharge(self, first, last):
        """
        Requests still waiting for a decision keep the day count worked out
        when they were sent. A holiday added or removed changes what those
        days cost, so offer to work them out again.
        """
        affected = [r for r in self.repo.all_requests()
                    if lp.normalise_status(r.get("status")) in lp.PENDING_STATUSES
                    and str(r.get("start_date"))[:10] <= last.isoformat()
                    and str(r.get("end_date"))[:10] >= first.isoformat()]
        if not affected:
            return 0
        if QMessageBox.question(
            self, "Re-cost waiting requests",
            "%d leave request%s still waiting for a decision cover%s that date. "
            "Work out what %s cost%s again with the new holiday list?"
            % (len(affected), "" if len(affected) == 1 else "s",
               "s" if len(affected) == 1 else "", "it" if len(affected) == 1 else "they",
               "s" if len(affected) == 1 else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return 0
        return self.repo.recharge_pending(first, last)

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
        values["location"] = self._place(values["location"])
        if values["location"] is None:
            return
        if not self.repo.update_holiday(row.get("id"), values["holiday_date"],
                                        values["name"], values["location"]):
            QMessageBox.warning(
                self, "Holiday not saved",
                "That change could not be saved. If another holiday is already "
                "on that date for the same place, this one cannot move onto it.")
            self._load_years()
            self.refresh()
            return
        self._load_years()
        self.refresh()
        old = row.get("holiday_date")
        old = old.date() if hasattr(old, "date") else old
        for day in {values["holiday_date"], old}:
            if isinstance(day, date):
                self._recharge(day, day)

    def remove(self):
        picked = self._selected()
        if not picked:
            return
        if QMessageBox.question(
            self, "Remove holiday",
            "Remove %d holiday%s? Approved leave keeps the day count it was "
            "charged at. Requests still waiting for a decision can be worked out "
            "again afterwards." % (len(picked), "" if len(picked) == 1 else "s"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        failed = []
        removed = []
        for row in picked:
            if self.repo.remove_holiday(row.get("id")):
                removed.append(row)
            else:
                failed.append(row)
        self._load_years()
        self.refresh()
        if failed:
            QMessageBox.warning(
                self, "Holidays not removed",
                "%d of %d could not be removed: %s. Somebody may have changed the "
                "list meanwhile." % (len(failed), len(picked), ", ".join(
                    "%s (%s)" % (r.get("name") or "unnamed", format_date(r.get("holiday_date")))
                    for r in failed)))
        days = []
        for row in removed:
            day = row.get("holiday_date")
            day = day.date() if hasattr(day, "date") else day
            if isinstance(day, date):
                days.append(day)
        if days:
            self._recharge(min(days), max(days))


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

        # Each row is ticked; HR untick any they do not want credited.
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Person", "Day", "Hours", "Earns", "Why"])
        self.table.itemChanged.connect(lambda *_: self._sync_credit())
        style_table(self.table, {"Person": "contents", "Day": "contents", "Hours": "numeric",
                                 "Earns": "numeric", "Why": "stretch"})
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
        days = self.window_pick.currentData() or 90
        since = date.today() - timedelta(days=int(days))
        self._entries = self._service().review(since) if rules.get("comp_off_enabled") else []

        if not rules.get("comp_off_enabled"):
            self.note.setText(
                "This studio does not operate comp off, so nothing is earned back "
                "for working a day off. HR or an admin can turn it on in "
                "Settings > Studio Policy > Comp-off.")
        elif not self._entries:
            self.note.setText(
                "Nothing in the last %d days qualifies. %s" % (days, self._rule()))
        else:
            total = sum(float(e["days"]) for e in self._entries)
            self.note.setText(
                "%s, worth %g day%s of comp off in total. Untick any you "
                "do not want credited; nothing is credited until you confirm. %s"
                % (people.plural(len(self._entries), "day worked qualifies",
                                 "days worked qualify"), total,
                   "" if total == 1 else "s", self._rule()))

        self.table.blockSignals(True)
        self.table.setRowCount(len(self._entries))
        for r, entry in enumerate(self._entries):
            cells = [
                people.display_name(entry.get("user_id")),
                format_date(entry.get("day"), weekday=True),
                "%.1f" % float(entry.get("hours") or 0),
                "%g" % float(entry.get("days") or 0),
                str(entry.get("reason") or ""),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                if c in (2, 3):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 3:
                    item.setForeground(QColor(Gate.OK))
                self.table.setItem(r, c, item)
        # Second credits for one day, found by the upgrade: shown read-only
        # and counted nowhere. Nothing is deleted - taking them back is HR's call.
        for dup in self.repo.comp_off_duplicates():
            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = [people.display_name(dup.get("user_id")),
                     format_date(dup.get("earned_on"), weekday=True), "",
                     "%g" % float(dup.get("days") or 0),
                     ("Duplicate - not counted. %s" % (dup.get("reason") or "")).strip()]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(QColor(Gate.TEXT_DIM))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self._sync_credit()

    @staticmethod
    def _rule() -> str:
        return ("A weekly off or public holiday counts from %g hours worked (half a "
                "standard day); a long enough day also earns it. Days with an open "
                "session, a missing punch or an automatic punch-out never do, and a "
                "day already credited is never counted twice." % lp.comp_off_min_hours())

    def ticked(self) -> list:
        """The entries HR left ticked."""
        return [entry for r, entry in enumerate(self._entries)
                if self.table.item(r, 0) is not None
                and self.table.item(r, 0).checkState() == Qt.CheckState.Checked]

    def _sync_credit(self):
        self.btn_credit.setEnabled(bool(self.ticked()))

    def credit(self):
        entries = self.ticked()
        if not entries:
            return
        total = sum(float(e["days"]) for e in entries)
        if QMessageBox.question(
            self, "Credit comp off",
            "Credit %g day%s of comp off for %s?\n\nEach one is written to the "
            "ledger with its reason and an expiry, and a day already credited is "
            "never credited twice."
            % (total, "" if total == 1 else "s",
               people.plural(len(entries), "day worked", "days worked")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        written = self._service().credit(entries)
        QMessageBox.information(
            self, "Credited",
            "%d of %d written to the ledger." % (written, len(entries)))
        self.refresh()


class YearEndDialog(QDialog):
    """
    Close a leave year: carry what the cap allows, lapse the rest.

    Only finished years are offered, and they close in order - closing the
    year in progress, or 2026 before 2025, wrote a line every later balance
    was measured from (LeaveRepository.close_refusal says why a year cannot
    be closed). People are listed by name with their own status, and those
    with no joining date are flagged rather than closed on an invented
    balance.
    """

    COLUMNS = ["Person", "Status", "Balance at year end", "Carries over", "Lapses"]

    def __init__(self, username: str, repo: LeaveRepository = None, parent=None):
        super().__init__(parent)
        self.username = username
        self.repo = repo or LeaveRepository()
        self.setWindowTitle("Close a leave year")
        self.resize(720, 560)
        self._preview = []
        self._closed = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        cap = lp.policy(None)["carry_forward_cap"]
        note = QLabel(
            "Closing a year draws a line under it. Up to %g days carry into the "
            "next year and anything above that is lost. Nothing is written until "
            "you confirm, and a year already closed is never closed twice." % cap)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        root.addWidget(note)

        picker = QHBoxLayout()
        picker.setSpacing(Gate.SPACE_2)
        picker.addWidget(QLabel("Leave year"))
        self.year = QComboBox()
        self._load_years()
        self.year.currentIndexChanged.connect(self.refresh)
        picker.addWidget(self.year)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people...")
        self.search.textChanged.connect(self._filter)
        picker.addWidget(self.search, 1)
        root.addLayout(picker)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        root.addWidget(self.state)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        style_table(self.table, {"Person": "stretch", "Status": "contents",
                                 "Balance at year end": "numeric",
                                 "Carries over": "numeric", "Lapses": "numeric"})
        from slate.gui.components.table_tools import setup_table
        setup_table(self.table)
        # Biggest loss first: that is who HR will be asked about.
        self.table.horizontalHeader().setSortIndicator(4, Qt.SortOrder.DescendingOrder)
        root.addWidget(self.table, 1)

        self.include_no_date = QCheckBox("Also close people with no joining date "
                                         "(their balance is counted from 1 January)")
        self.include_no_date.toggled.connect(lambda *_: self._update_state())
        root.addWidget(self.include_no_date)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_cancel = make_button("Cancel", "ghost", on_click=self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_close = make_button("Close the year", "danger", on_click=self.close_year)
        buttons.addWidget(self.btn_close)
        root.addLayout(buttons)

        self.refresh()

    @on_database_error
    def _load_years(self):
        """
        The year to close next, then the closed ones to look back at. Years
        before the last close were offered and could only be refused.
        """
        wanted = self.repo.unclosed_year()
        if wanted is not None:
            self.year.addItem(str(wanted), wanted)
        latest = self.repo.last_closed_year()
        for y in self.repo.closed_years():
            # The latest closed year can still take somebody closed late.
            self.year.addItem("%d (closed)" % y if y == latest
                              else "%d (closed - view only)" % y, y)

    def _to_close(self) -> list:
        include = self.include_no_date.isChecked()
        return [r for r in self._preview
                if r["user_id"].lower() not in self._closed
                and (include or r.get("status") != "no_joining_date")]

    @on_database_error
    def refresh(self, *_):
        from slate.gui.components.table_tools import KeepSelection, make_item
        year = self.year.currentData()
        recorded = {str(r["user_id"]).lower(): r for r in self.repo.closes(year)}
        self._closed = set(recorded)
        try:
            self._preview = self.repo.preview_close(year)
        except DatabaseReadError as exc:
            # Not "nothing to close": the list could not be read.
            from slate.gui.components.state_notice import show_load_error
            self._preview = []
            self.table.setRowCount(0)
            show_load_error(self.table, exc, retry=self.refresh, what="the people to close")
            self.state.setText("The people list could not be read, so nothing can be closed yet.")
            self.btn_close.setEnabled(False)
            return

        with KeepSelection(self.table):
            self.table.setRowCount(len(self._preview))
            for r, row in enumerate(self._preview):
                already = row["user_id"].lower() in self._closed
                no_date = row.get("status") == "no_joining_date"
                status = "Closed" if already else (
                    "No joining date - set it first" if no_date else "To close")
                tip = None
                if already:
                    # What was recorded is what balances count from; the live
                    # figure moves when leave in that year changes afterwards.
                    live = row["closing_balance"]
                    row = dict(row, **{k: float(recorded[row["user_id"].lower()].get(k) or 0)
                                       for k in ("closing_balance", "carried", "lapsed")})
                    if abs(live - row["closing_balance"]) > 0.005:
                        status = "Closed - now %g" % live
                        tip = ("Recorded at the close: %g. Leave in %d changed since, so it "
                               "works out at %g now. Balances still count from what was "
                               "recorded." % (row["closing_balance"], year, live))
                dim = Gate.TEXT_DIM if already else None
                cells = [
                    make_item(row.get("display_name") or row["user_id"], key=row["user_id"],
                              tooltip=row["user_id"], foreground=dim),
                    make_item(status, tooltip=tip, foreground=Gate.WARN if (
                        (no_date and not already) or tip) else dim),
                    make_item("%g" % row["closing_balance"], sort_value=row["closing_balance"],
                              foreground=dim),
                    make_item("%g" % row["carried"], sort_value=row["carried"],
                              foreground=dim or Gate.OK),
                    make_item("%g" % row["lapsed"], sort_value=row["lapsed"],
                              foreground=dim or (Gate.BAD if row["lapsed"] > 0 else None)),
                ]
                for c, item in enumerate(cells):
                    self.table.setItem(r, c, item)
        self._filter()
        self._update_state()

    def _filter(self, *_):
        needle = self.search.text().strip().lower()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            text = (item.text() + " " + (item.toolTip() or "")).lower() if item else ""
            self.table.setRowHidden(r, bool(needle) and needle not in text)

    def _update_state(self):
        year = self.year.currentData()
        refusal = self.repo.close_refusal(year)
        todo = self._to_close()
        lapsing = sum(1 for r in todo if r["lapsed"] > 0)
        no_date = sum(1 for r in self._preview
                      if r["user_id"].lower() not in self._closed
                      and r.get("status") == "no_joining_date")
        if refusal:
            self.state.setText(refusal)
            self.btn_close.setEnabled(False)
        elif not todo:
            self.state.setText("Nothing left to close for %d." % year + (
                " %s no joining date." % people.plural(no_date, "person has", "people have")
                if no_date else ""))
            self.btn_close.setEnabled(False)
        else:
            text = "%s to close for %d. %d of them will lose leave." % (
                people.plural(len(todo), "person", "people"), year, lapsing)
            if no_date and not self.include_no_date.isChecked():
                text += (" %s no joining date %s left out - set their joining date on "
                         "Users & Roles, or tick the box below."
                         % (people.plural(no_date, "person with", "people with"),
                            "is" if no_date == 1 else "are"))
            self.state.setText(text)
            self.btn_close.setEnabled(True)
        # Nothing to do here: the way out is 'Close', not 'Cancel'.
        self.btn_cancel.setText("Cancel" if self.btn_close.isEnabled() else "Close")

    def close_year(self):
        year = self.year.currentData()
        # Only the people this will actually write - not the ones already closed.
        todo = self._to_close()
        lapsing = sum(r["lapsed"] for r in todo)
        if QMessageBox.question(
            self, "Close %d" % year,
            "This writes the year end for %d %s and lapses %g day%s in "
            "total.\n\nIt cannot be undone from this screen. Go ahead?"
            % (len(todo), "person" if len(todo) == 1 else "people", lapsing,
               "" if lapsing == 1 else "s"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.repo.close_year(year, self.username,
                                          include_no_joining=self.include_no_date.isChecked())
        except DatabaseReadError:
            result = {"refused": "The people list could not be read, so nothing was closed. "
                                 "Try again in a moment; if it keeps happening, tell IT."}
        failed = result.get("failed") or []
        if result.get("refused"):
            QMessageBox.warning(self, "Close %d" % year, result["refused"])
        elif failed:
            QMessageBox.warning(
                self, "Close %d" % year,
                "%d closed, %d were already done. %s NOT saved: %s.\n\nClose the year "
                "again to finish them - the ones already closed are not touched twice."
                % (result["closed"], result["skipped"],
                   people.plural(len(failed), "close was", "closes were"),
                   ", ".join(people.display_name(u) for u in failed)))
        else:
            QMessageBox.information(
                self, "Year closed",
                "%d closed, %d were already done." % (result["closed"], result["skipped"]))
        self.refresh()


class GrantProjectRestDialog(QDialog):
    """
    HR grant project rest: days off after a project, decided by HR. It was in
    everybody's request list as if it were an entitlement.
    """

    def __init__(self, username: str, repo: LeaveRepository = None, parent=None):
        super().__init__(parent)
        self.username = username
        self.repo = repo or LeaveRepository()
        self.outcome = None
        self.setWindowTitle("Grant project rest")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_2)
        from slate.gui.components.person_picker import PersonPicker
        self.person = PersonPicker(placeholder="Who gets the rest?", allow_empty=False)
        form.addRow("Person", self.person)
        self.start = setup_date_edit(QDateEdit(QDate.currentDate()), weekday=True)
        self.end = setup_date_edit(QDateEdit(QDate.currentDate()), weekday=True)
        self.start.dateChanged.connect(lambda d: (self.end.setMinimumDate(d),
                                                  self.end.setDate(max(self.end.date(), d))))
        form.addRow("From", self.start)
        form.addRow("To", self.end)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("After the delivery of ...")
        form.addRow("Reason", self.reason)
        root.addLayout(form)

        # What the dates cover, live, as Request leave shows it. HR granted
        # blind: no day count at all.
        self.cost = QLabel("")
        self.cost.setWordWrap(True)
        self.cost.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12.5px;")
        root.addWidget(self.cost)
        for signal in (self.start.dateChanged, self.end.dateChanged):
            signal.connect(lambda *_: self._recost())
        self.person.person_changed.connect(lambda *_: self._recost())
        self._recost()

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {Gate.WARN}; font-size: 12.5px;")
        self.note.hide()
        root.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Grant", "primary", on_click=self._grant))
        root.addLayout(buttons)

    @on_database_error
    def _recost(self):
        """The working days the dates cover, against the person's own holidays."""
        start, end = self.start.date().toPython(), self.end.date().toPython()
        who = self.person.username()
        holidays = self.repo.holidays_for(who, start, end) if who else \
            self.repo.holidays(start.year, "All", through_year=end.year)
        working = len(lp.days_charged(start, end, holidays)["working_days"])
        self.cost.setText(
            "Those dates hold no working days." if not working else
            "%s of project rest. Not taken from the paid leave balance."
            % people.plural(working, "working day"))

    def _grant(self):
        who = self.person.username()
        if not who:
            self.note.setText("Choose the person.")
            self.note.show()
            return
        start = self.start.date().toPython()
        end = self.end.date().toPython()
        outcome = self.repo.grant_project_rest(who, start, end, self.username,
                                               self.reason.text().strip())
        if not outcome:
            self.note.setText(outcome.reason)
            self.note.show()
            return
        self.outcome = outcome
        self.accept()
