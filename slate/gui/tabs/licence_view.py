"""
Licences, read as a compliance question rather than an inventory.

The old screen listed what had been bought. That answers nothing anybody
actually asks: it cannot say whether the studio is short of seats, and it
cannot say which renewal is money being burnt. Both answers need the peak
concurrent use, so this screen leads with the finding and keeps the seat count
as supporting detail.

Every row carries a sentence saying what it means. A status word on its own is
not something IT can take to purchasing - nor is a seat count without what the
seats cost, so a licence can carry its annual cost, vendor and contract.

Readings can be typed (now or back-filled), imported from a saved licence
server report (lmstat / rlmstat), and seen, corrected or deleted per licence -
one typo used to keep a licence "Over-subscribed 312%" for a whole year.

People who approve renewals (view_licences) see all of it read-only; changing
anything needs manage_it.
"""

import logging
import os
from datetime import date, datetime

from PySide6.QtCore import QDate, QDateTime, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDateTimeEdit, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSizePolicy, QSpinBox, QTableWidget,
    QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.infra.licence_repository import LicenceRepository
from slate.core.domain import licence_compliance as lc
from slate.core.domain import money
from slate.core.domain import licence_server_report as server_report
from ..core.controls import make_button, page_title, tidy_form
from ..core.stat_card import StatStrip
from ..core.table_style import style_table
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from ..core.data_display import (
    export_table_dialog, from_qdate, money_item, setup_date_edit, setup_datetime_edit,
)
from slate.core.domain.dates import format_date, format_datetime
from slate.gui.components import feedback

logger = logging.getLogger(__name__)

WINDOW_KEY = "LICENCE_PEAK_WINDOW_DAYS"
WINDOWS = (("Peak over 30 days", 30), ("Peak over 90 days", 90), ("Peak over a year", 365))


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


def licence_label(row: dict) -> str:
    """'Nuke - 20 seats, renews 18 Apr 2027': two contracts of one product told apart."""
    seats = int(row.get("total_seats") or 0)
    expiry = lc.as_date(row.get("expiration_date"))
    renews = ("renews %s" % format_date(expiry)) if expiry else "no expiry"
    ref = (" (%s)" % row.get("contract_ref")) if row.get("contract_ref") else ""
    return "%s%s - %s, %s" % (row.get("software_name") or "", ref, lc.plural(seats, "seat"), renews)


class LicenceDialog(QDialog):
    """Add or correct a purchase."""

    def __init__(self, row=None, parent=None, repo: LicenceRepository = None):
        super().__init__(parent)
        self.row = row or {}
        self.repo = repo
        self.setWindowTitle("Edit licence" if row else "Add a licence")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = tidy_form(QFormLayout())

        self.name = QLineEdit(str(self.row.get("software_name") or ""))
        self.name.setPlaceholderText("Nuke, Houdini, Maya...")
        # The database keeps at most this many characters. A longer name was
        # refused and the licence silently not saved.
        self.name.setMaxLength(LicenceRepository.NAME_MAX)
        form.addRow("Software", self.name)

        self.contract = QLineEdit(str(self.row.get("contract_ref") or ""))
        self.contract.setPlaceholderText("PO or contract number - tells two contracts apart")
        self.contract.setMaxLength(120)
        form.addRow("Contract / PO", self.contract)

        # At least one: 0 seats bought made a licence in use read as Healthy.
        self.seats = QSpinBox()
        self.seats.setRange(1, 9999)
        self.seats.setValue(max(1, int(self.row.get("total_seats") or 1)))
        form.addRow("Seats bought", self.seats)

        self.expiry = setup_date_edit(QDateEdit())
        existing = lc.as_date(self.row.get("expiration_date"))
        self.expiry.setDate(QDate(existing.year, existing.month, existing.day)
                            if existing else QDate.currentDate().addYears(1))
        self.perpetual = QCheckBox("No expiry (perpetual)")
        self.perpetual.toggled.connect(lambda on: self.expiry.setEnabled(not on))
        self.perpetual.setChecked(bool(row) and existing is None)
        expiry_row = QHBoxLayout()
        expiry_row.setSpacing(Gate.SPACE_2)
        expiry_row.addWidget(self.expiry, 1)
        expiry_row.addWidget(self.perpetual)
        form.addRow("Renews on", expiry_row)

        self.cost = QLineEdit()
        self.cost.setPlaceholderText("Optional - what a year of these seats costs")
        if self.row.get("annual_cost") not in (None, ""):
            self.cost.setText(str(money.quantize(self.row.get("annual_cost"))))
        self.currency = QComboBox()
        for code, cur in money.CURRENCIES.items():
            self.currency.addItem("%s %s" % (cur.symbol, code), code)
        code = money.normalise_code(self.row.get("currency"))
        self.currency.setCurrentIndex(max(0, self.currency.findData(code)))
        cost_row = QHBoxLayout()
        cost_row.setSpacing(Gate.SPACE_2)
        cost_row.addWidget(self.cost, 1)
        cost_row.addWidget(self.currency)
        form.addRow("Annual cost", cost_row)

        self.vendor = QLineEdit(str(self.row.get("vendor") or ""))
        self.vendor.setPlaceholderText("Who it is bought from, and a contact")
        self.vendor.setMaxLength(120)
        form.addRow("Vendor", self.vendor)

        self.notes = QPlainTextEdit(str(self.row.get("notes") or ""))
        self.notes.setFixedHeight(64)
        self.notes.setPlaceholderText("Anything the next renewal needs to know")
        form.addRow("Notes", self.notes)
        root.addLayout(form)

        note = QLabel(
            "Seats bought is what the invoice says. What the studio actually uses "
            "comes from the readings, not from here.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Save", "primary", on_click=self._save))
        root.addLayout(buttons)

    # ----------------------------------------------------------- checks
    def problem(self) -> str:
        """Why this cannot be saved as it is, or ''."""
        if not self.name.text().strip():
            return "A licence needs a software name."
        if self.cost.text().strip():
            try:
                if money.parse_money(self.cost.text()) < 0:
                    return "The annual cost cannot be negative."
            except (ValueError, ArithmeticError):
                return "The annual cost is not a number."
        if self.repo is not None:
            others = self.repo.same_name(self.name.text().strip(), exclude_id=self.row.get("id"))
            if others and not self.contract.text().strip():
                return ("There is already a licence called %s. Give this one a contract or PO "
                        "number so the two can be told apart." % others[0].get("software_name"))
        return ""

    def _save(self):
        problem = self.problem()
        if problem:
            feedback.warn(self, "Save licence", problem)
            (self.contract if "contract" in problem else
             self.cost if "cost" in problem else self.name).setFocus()
            return
        expiry = self.expiry_date()
        if expiry is not None and expiry < date.today() and expiry != lc.as_date(self.row.get("expiration_date")):
            if not feedback.confirm(
                    self, "Save licence",
                    "%s is in the past - the licence will show as expired. Save anyway?"
                    % format_date(expiry), yes_label="Save anyway", no_label="Change the date"):
                self.expiry.setFocus()
                return
        self.accept()

    def expiry_date(self):
        if self.perpetual.isChecked():
            return None
        return from_qdate(self.expiry.date())

    def payload(self):
        return (self.name.text().strip(), self.seats.value(), self.expiry_date())

    def details(self) -> dict:
        cost = self.cost.text().strip()
        return {
            "annual_cost": money.parse_money(cost) if cost else None,
            "currency": self.currency.currentData(),
            "vendor": self.vendor.text().strip(),
            "contract_ref": self.contract.text().strip(),
            "notes": self.notes.toPlainText().strip(),
        }


class ReadingDialog(QDialog):
    """Write down what the licence server reported - now, or at a time already past."""

    def __init__(self, licences, preset=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record usage")
        self.setMinimumWidth(460)
        self._licences = licences

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = tidy_form(QFormLayout())

        # Two contracts of the same product read the same in a bare name list,
        # so a reading easily landed on the wrong one.
        self.software = QComboBox()
        for row in licences:
            self.software.addItem(licence_label(row), row)
        if preset:
            for i in range(self.software.count()):
                if (self.software.itemData(i) or {}).get("id") == preset.get("id"):
                    self.software.setCurrentIndex(i)
                    break
        self.software.currentIndexChanged.connect(self._sync_seats)
        form.addRow("Software", self.software)

        self.in_use = QSpinBox()
        self.in_use.setRange(0, 9999)
        form.addRow("Seats in use", self.in_use)

        self.taken_at = setup_datetime_edit(QDateTimeEdit())
        self.taken_at.setDateTime(QDateTime.currentDateTime())
        self.taken_at.setMaximumDateTime(QDateTime.currentDateTime().addSecs(300))
        self.taken_at.setToolTip("When the licence server said this - earlier today, or last Friday's peak")
        form.addRow("Taken at", self.taken_at)
        root.addLayout(form)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Record", "primary", on_click=self._record))
        root.addLayout(buttons)

        self._sync_seats()

    def _sync_seats(self, *_):
        row = self.software.currentData() or {}
        seats = int(row.get("total_seats") or 0)
        # Start from the latest reading rather than 0.
        self.in_use.setValue(int(row.get("active_seats") or 0))
        self.hint.setText(
            "%s bought. Take this reading when the studio is busy - a quiet-afternoon "
            "number makes an over-subscribed licence look fine." % lc.plural(seats, "seat"))

    def _record(self):
        row = self.software.currentData() or {}
        seats = int(row.get("total_seats") or 0)
        if self.in_use.value() > seats:
            if not feedback.confirm(
                    self, "Record usage",
                    "%d in use is more than the %s bought - the licence will show as "
                    "over-subscribed. Is the number right?" % (self.in_use.value(), lc.plural(seats, "seat")),
                    yes_label="Record it", no_label="Correct it"):
                self.in_use.setFocus()
                return
        self.accept()

    def taken(self) -> datetime:
        return self.taken_at.dateTime().toPython().replace(microsecond=0)

    def payload(self):
        row = self.software.currentData() or {}
        return (str(row.get("software_name") or ""), self.in_use.value(),
                int(row.get("total_seats") or 0), row.get("id"))


class PeakChart(QWidget):
    """Readings as bars against the seats bought - the shape of the peak at a glance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.readings = []
        self.seats = 0
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, readings, seats):
        self.readings = list(reversed(readings))[-60:]       # oldest -> newest, last 60
        self.seats = int(seats or 0)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 6, -4, -6)
        painter.fillRect(self.rect(), QColor(Gate.PANEL))
        if not self.readings:
            painter.setPen(QColor(Gate.TEXT_DIM))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No readings yet")
            return
        top = max([self.seats] + [int(r.get("seats_in_use") or 0) for r in self.readings]) or 1
        width = rect.width() / max(1, len(self.readings))
        for i, r in enumerate(self.readings):
            used = int(r.get("seats_in_use") or 0)
            h = rect.height() * used / top
            colour = Gate.BAD if self.seats and used > self.seats else Gate.ACCENT
            painter.fillRect(int(rect.left() + i * width + 1), int(rect.bottom() - h),
                             max(1, int(width - 2)), int(h), QColor(colour))
        if self.seats:
            y = int(rect.bottom() - rect.height() * self.seats / top)
            painter.setPen(QColor(Gate.WARN))
            painter.drawLine(rect.left(), y, rect.right(), y)
            painter.drawText(rect.left() + 4, y - 3, "%s bought" % lc.plural(self.seats, "seat"))


class ReadingsDialog(QDialog):
    """The readings behind one licence's peak, with Correct and Delete."""

    def __init__(self, licence: dict, repo: LicenceRepository, read_only=False, parent=None):
        super().__init__(parent)
        self.licence = licence
        self.repo = repo
        self.read_only = read_only
        self.changed_anything = False
        self.setWindowTitle("Readings - %s" % (licence.get("software_name") or ""))
        self.setMinimumSize(620, 480)
        from slate.gui.components.screen_fit import fit_to_screen
        fit_to_screen(self, 760, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        heading = QLabel(licence_label(licence))
        heading.setStyleSheet(f"color: {Gate.TEXT}; font-size: {Gate.SIZE_LG}px; font-weight: 600;")
        root.addWidget(heading)
        self.chart = PeakChart()
        root.addWidget(self.chart)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Taken at", "In use", "Source", "Recorded by"])
        style_table(self.table, {"Taken at": "contents", "In use": "numeric",
                                 "Source": "stretch", "Recorded by": "contents"},
                    multi_select=False)
        from slate.gui.components.table_tools import setup_table
        setup_table(self.table, multi_select=False)
        self.table.itemSelectionChanged.connect(self._sync)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_correct = make_button("Correct…", "secondary", on_click=self.correct)
        self.btn_delete = make_button("Delete", "danger", on_click=self.delete)
        if not read_only:
            row.addWidget(self.btn_correct)
            row.addWidget(self.btn_delete)
        row.addStretch(1)
        row.addWidget(make_button("Close", "ghost", on_click=self.accept))
        root.addLayout(row)
        self.load()

    def load(self):
        from slate.gui.components.table_tools import KeepSelection, make_item
        self.readings = self.repo.history(self.licence)
        self.chart.set_data(self.readings, self.licence.get("total_seats"))
        with KeepSelection(self.table):
            self.table.setRowCount(len(self.readings))
            for r, reading in enumerate(self.readings):
                taken = reading.get("taken_at")
                self.table.setItem(r, 0, make_item(format_datetime(taken), sort_value=str(taken or ""),
                                                   key=reading.get("id")))
                used = int(reading.get("seats_in_use") or 0)
                item = make_item(str(used), sort_value=used)
                seats = int(self.licence.get("total_seats") or 0)
                if seats and used > seats:
                    item.setForeground(QColor(Gate.BAD))
                self.table.setItem(r, 1, item)
                source = reading.get("source") or "typed in"
                if reading.get("legacy"):
                    source += " (before contracts were told apart)"
                self.table.setItem(r, 2, make_item(source.capitalize() if source else ""))
                from slate.core.domain import people
                self.table.setItem(r, 3, make_item(people.display_name(reading.get("recorded_by"), empty="-")))
        self._sync()

    def _selected(self):
        from slate.gui.components.table_tools import selected_keys
        keys = selected_keys(self.table)
        return next((r for r in self.readings if keys and r.get("id") == keys[0]), None)

    def _sync(self, *_):
        picked = self._selected() is not None
        self.btn_correct.setEnabled(picked)
        self.btn_delete.setEnabled(picked)

    def delete(self):
        reading = self._selected()
        if reading is None:
            return
        if not feedback.confirm(self, "Delete reading",
                                "Delete the reading of %d taken %s? The peak is worked out again "
                                "without it." % (int(reading.get("seats_in_use") or 0),
                                                 format_datetime(reading.get("taken_at"))),
                                yes_label="Delete reading", no_label="Keep it", destructive=True):
            return
        if self.repo.delete_reading(reading.get("id")):
            self.changed_anything = True
            feedback.toast(self, "Reading deleted.", "success")
        else:
            feedback.warn(self, "Delete reading", "The reading could not be deleted.")
        self.load()

    def correct(self):
        reading = self._selected()
        if reading is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Correct reading")
        form = tidy_form(QFormLayout(dialog))
        spin = QSpinBox()
        spin.setRange(0, 9999)
        spin.setValue(int(reading.get("seats_in_use") or 0))
        form.addRow("Seats in use", spin)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", "ghost", on_click=dialog.reject))
        row.addWidget(make_button("Save", "primary", on_click=dialog.accept))
        form.addRow(row)
        self._correct_dialog = (dialog, spin)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if self.repo.update_reading(reading.get("id"), spin.value()):
            self.changed_anything = True
            feedback.toast(self, "Reading corrected.", "success")
        else:
            feedback.warn(self, "Correct reading", "The reading could not be changed.")
        self.load()


class ImportReadingsDialog(QDialog):
    """
    Readings from a saved licence-server report (lmstat -a / rlmstat -a).
    Each product is matched to a licence, which can be changed or skipped.
    """

    def __init__(self, licences, parent=None, text: str = "", path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Import from licence server")
        self.setMinimumSize(640, 480)
        from slate.gui.components.screen_fit import fit_to_screen
        fit_to_screen(self, 760, 560)
        self.licences = licences
        self.readings = []
        self.rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        intro = QLabel("Open a report saved from the licence server (FlexLM 'lmstat -a' or RLM "
                       "'rlmstat -a'), or paste it below. Slate reads the text only - it does not "
                       "contact the licence server.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        root.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(make_button("Open report…", "secondary", on_click=self.choose_file))
        self.taken_at = setup_datetime_edit(QDateTimeEdit())
        self.taken_at.setDateTime(QDateTime.currentDateTime())
        self.taken_at.setMaximumDateTime(QDateTime.currentDateTime().addSecs(300))
        row.addStretch(1)
        row.addWidget(QLabel("Taken at"))
        row.addWidget(self.taken_at)
        root.addLayout(row)

        self.text = QPlainTextEdit(text)
        self.text.setPlaceholderText("Users of nuke_i:  (Total of 10 licenses issued;  Total of 3 licenses in use)")
        self.text.setFixedHeight(90)
        self.text.textChanged.connect(self.parse)
        root.addWidget(self.text)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Import", "Product on the server", "In use", "Licence in Slate"])
        style_table(self.table, {"Import": "contents", "Product on the server": "contents",
                                 "In use": "numeric", "Licence in Slate": "stretch"})
        root.addWidget(self.table, 1)
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        root.addWidget(self.summary)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.ok_btn = make_button("Import readings", "primary", on_click=self.accept)
        buttons.addWidget(self.ok_btn)
        root.addLayout(buttons)
        if path:
            self.load_file(path)
        self.parse()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open licence server report", "",
                                              "Text reports (*.txt *.log *.out);;All files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read(2_000_000)
            stamp = datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0)
        except OSError as exc:
            feedback.warn(self, "Import from licence server", "The report could not be read:\n\n%s" % exc)
            return
        self.text.setPlainText(content)
        if stamp <= datetime.now():
            self.taken_at.setDateTime(QDateTime(stamp))

    def parse(self):
        self.readings = server_report.parse(self.text.toPlainText())
        self.rows = []
        self.table.setRowCount(len(self.readings))
        for r, reading in enumerate(self.readings):
            include = QCheckBox()
            combo = QComboBox()
            combo.addItem("Skip - not a licence we track", None)
            for licence in self.licences:
                combo.addItem(licence_label(licence), licence)
            match = server_report.match_licence(reading.product, self.licences)
            if match is not None:
                combo.setCurrentIndex(self.licences.index(match) + 1)
            include.setChecked(match is not None)
            combo.currentIndexChanged.connect(lambda _i, c=include, cb=combo: c.setChecked(cb.currentData() is not None))
            include.toggled.connect(self._count)
            self.table.setCellWidget(r, 0, include)
            from slate.gui.components.table_tools import make_item
            self.table.setItem(r, 1, make_item(reading.product))
            total = "" if reading.total is None else " of %d" % reading.total
            self.table.setItem(r, 2, make_item("%d%s" % (reading.in_use, total), sort_value=reading.in_use))
            self.table.setCellWidget(r, 3, combo)
            self.rows.append((include, combo, reading))
        self._count()

    def _count(self, *_):
        chosen = len(self.chosen())
        if not self.readings:
            self.summary.setText("No licence usage found in this text yet.")
        else:
            self.summary.setText("%d product%s found, %d to import." % (
                len(self.readings), "" if len(self.readings) == 1 else "s", chosen))
        self.ok_btn.setEnabled(chosen > 0)

    def chosen(self):
        """[(licence row, seats in use)], products mapped to the same licence added up."""
        merged = {}
        for include, combo, reading in self.rows:
            licence = combo.currentData()
            if include.isChecked() and licence is not None:
                key = licence.get("id")
                if key in merged:
                    merged[key] = (licence, merged[key][1] + reading.in_use)
                else:
                    merged[key] = (licence, reading.in_use)
        return list(merged.values())

    def taken(self) -> datetime:
        return self.taken_at.dateTime().toPython().replace(microsecond=0)


class LicenceView(QWidget):
    """Purchased against used, and what that means for each renewal."""

    changed = Signal()

    def __init__(self, username: str = "", db_manager=None, parent=None, read_only: bool = False):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.repo = LicenceRepository(db_manager)
        self.read_only = bool(read_only)
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        root.addWidget(page_title(
            "Licences",
            "What we bought, what we actually use, and what to do before each renewal"
            + (" - read only" if self.read_only else "")))

        # Figures: one strip, values updated in place.
        self.figures = StatStrip(compact=True)
        self.fig_over = self.figures.add("Over-subscribed", 0, tooltip="More in use than we own")
        self.fig_renew = self.figures.add("Renewing or expired", 0)
        self.fig_bought = self.figures.add("Seats bought", 0, tone="neutral",
                                           tooltip="Across licences that have not expired")
        self.fig_spare = self.figures.add("Spare seats", 0,
                                          tooltip="Paid for above peak use (licences with readings)")
        root.addWidget(self.figures)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.window_pick = QComboBox()
        for label, days in WINDOWS:
            self.window_pick.addItem(label, days)
        self.window_pick.setCurrentIndex(max(0, self.window_pick.findData(self._saved_window())))
        self.window_pick.currentIndexChanged.connect(self._window_changed)
        controls.addWidget(self.window_pick)
        controls.addStretch(1)

        self.btn_readings = make_button("Readings…", "secondary", on_click=self.show_readings,
                                        tooltip="Every reading of the selected licence - correct or delete one")
        self.btn_record = make_button("Record usage", "primary", on_click=self.record_reading)
        self.btn_import = make_button("Import from licence server…", "secondary",
                                      on_click=self.import_readings,
                                      tooltip="Readings from a saved lmstat / rlmstat report")
        self.btn_add = make_button("Add licence", "secondary", on_click=self.add_licence)
        self.btn_edit = make_button("Edit", "ghost", on_click=self.edit_licence)
        self.btn_remove = make_button("Remove", "danger", on_click=self.remove_licence)
        buttons = [self.btn_readings]
        if not self.read_only:
            buttons += [self.btn_record, self.btn_import, self.btn_add, self.btn_edit, self.btn_remove]
        for b in buttons:
            controls.addWidget(b)
        controls.addWidget(make_button(
            "Export…", "ghost",
            on_click=lambda: export_table_dialog(self, self.table, "licences")))
        root.addLayout(controls)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Software", "State", "Seats", "Peak", "Used", "Renews", "Annual cost", "What this means"])
        style_table(self.table, {
            "Software": ("interactive", 220), "State": "contents", "Seats": "numeric",
            "Peak": "numeric", "Used": "numeric", "Renews": "contents",
            "Annual cost": "numeric", "What this means": "stretch",
        }, multi_select=False)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        # Sortable headers (seats, peak, use and renewal sort by value, not
        # text), a search box, and double-click to edit (or to read the
        # readings, read-only).
        from slate.gui.components.table_tools import TableToolbar, setup_table
        setup_table(self.table, multi_select=False)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._fit_rows())
        self.table.doubleClicked.connect(
            lambda _index: self.show_readings() if self.read_only else self.edit_licence())
        self.toolbar = TableToolbar(self.table, placeholder="Search software, vendor or finding…",
                                    columns=(0, 1, 7), on_refresh=self.refresh)
        root.addWidget(self.toolbar)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "No licences recorded",
            "Add what the studio has bought, then record what the licence server "
            "reports. The second one is what makes a renewal decidable.",
            glyph="key")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=30,
                                         topics=("software_licenses", "licence_readings"))

    # ------------------------------------------------------------------ data
    @staticmethod
    def _saved_window() -> int:
        try:
            from slate.core.infra.global_config import GlobalConfig
            return int(GlobalConfig.get(WINDOW_KEY, 90) or 90)
        except Exception:
            return 90

    def _window_changed(self, *_):
        try:
            from slate.core.infra.global_config import GlobalConfig
            GlobalConfig.set(WINDOW_KEY, int(self.window_pick.currentData() or 90))
        except Exception as exc:
            logger.debug("Peak window not remembered: %s", exc)
        self.refresh()

    @on_database_error
    def refresh(self, *_):
        days = self.window_pick.currentData() or 90
        self._renewal_days = lc.renewal_window(self.repo.db)
        self._rows = self.repo.compliance(days, renewal_days=self._renewal_days)
        self._paint_figures(self._rows)
        self._paint_rows(self._rows)
        self.empty.refresh()
        self._sync_buttons()
        if not self.read_only:
            self.repo.send_renewal_reminders()

    def _paint_figures(self, rows):
        over = sum(1 for r in rows if r["state"] == lc.OVER)
        # Renewals counted from the date, whatever else is wrong: an
        # over-subscribed licence renewing in 30 days used to drop out.
        renew = sum(1 for r in rows if r.get("renewal_due"))
        current = [r for r in rows if r.get("days_left") is None or r["days_left"] >= 0]
        bought = sum(int(r.get("total_seats") or 0) for r in current)
        # Only count spare seats where there is a reading to count against -
        # a licence nobody measured is not evidence of waste.
        spare = sum(max(0, int(r.get("total_seats") or 0) - int(r["peak"]))
                    for r in current if r.get("peak") is not None)
        costs = money.sum_by_currency((r["spare_cost"], r.get("currency") or None)
                                      for r in current if r.get("spare_cost"))

        self.fig_over.set_value(over)
        self.fig_over.set_tone("bad" if over else "ok")
        self.fig_renew.set_value(renew)
        self.fig_renew.set_tone("warn" if renew else "idle")
        self.fig_renew.setToolTip("Expired, or renewing within %d days" % self._renewal_days)
        self.fig_bought.set_value(bought)
        text = str(spare)
        if costs:
            text += "  (%s a year)" % money.format_totals(costs, compact=True)
        self.fig_spare.set_value(text)
        self.fig_spare.set_tone("info" if spare else "idle")

    def _paint_rows(self, rows):
        from slate.gui.components.table_tools import KeepSelection
        with KeepSelection(self.table):
            self._fill_rows(rows)
        self._fit_rows()

    def _fit_rows(self, *_):
        """
        Findings wrap: rows grow to fit them (with a little air) rather than
        clipping the second line against the row border. Run again whenever
        the columns change width.
        """
        self.table.resizeRowsToContents()
        standard = self.table.verticalHeader().defaultSectionSize()
        for r in range(self.table.rowCount()):
            self.table.setRowHeight(r, max(standard, self.table.rowHeight(r) + 8))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._fit_rows)

    def _fill_rows(self, rows):
        from slate.gui.components.table_tools import make_item
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            peak = row.get("peak")
            use = row.get("utilisation")
            left = row.get("days_left")
            expiry = lc.as_date(row.get("expiration_date"))
            seats = int(row.get("total_seats") or 0)

            # The studio's date format; the days in words on the tooltip and
            # in the finding ('Renews in 31 days', 'Expired 10 days ago').
            renews = "No expiry" if left is None else format_date(expiry)

            name = row.get("software_name") or ""
            if row.get("contract_ref"):
                name += " (%s)" % row.get("contract_ref")
            cells = [
                name,
                row["state"],
                str(seats),
                "-" if peak is None else str(int(peak)),
                "-" if use is None else "%d%%" % round(use * 100),
                renews,
                None,
                row.get("finding") or "",
            ]
            # What each column sorts by: numbers and dates by value.
            sort_values = [None, lc.SEVERITY.get(row["state"], 9), seats,
                           None if peak is None else int(peak),
                           None if use is None else float(use),
                           9e9 if left is None else int(left), None, None]
            for c, text in enumerate(cells):
                if c == 6:
                    cost = row.get("annual_cost")
                    if cost in (None, ""):
                        item = make_item("-")
                        item.setForeground(QColor(Gate.TEXT_DIM))
                    else:
                        item = money_item(cost, row.get("currency") or None)
                        tip = [money.format_money(cost, row.get("currency") or None) + " a year"]
                        if row.get("vendor"):
                            tip.append("Vendor: %s" % row.get("vendor"))
                        item.setToolTip("\n".join(tip))
                    self.table.setItem(r, c, item)
                    continue
                item = make_item(text, sort_value=sort_values[c],
                                 key=(row.get("id") if row.get("id") is not None
                                      else row.get("software_name")) if c == 0 else None,
                                 align=(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                                 if c in (1, 5) else None)
                if c == 0:
                    tip = [name]
                    if row.get("vendor"):
                        tip.append("Vendor: %s" % row.get("vendor"))
                    if row.get("notes"):
                        tip.append(str(row.get("notes")))
                    item.setToolTip("\n".join(tip))
                if c in (2, 3, 4):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 1:
                    item.setForeground(QColor(_tone(row["tone"])))
                if c == 5:
                    item.setToolTip(lc.renewal_phrase(left))
                    if left is not None and left < 0:
                        item.setForeground(QColor(Gate.BAD))
                    elif row.get("renewal_due"):
                        item.setForeground(QColor(Gate.WARN))
                if c == 4 and use is not None:
                    item.setForeground(QColor(
                        Gate.BAD if use > 1 else
                        Gate.INFO if use < lc.UNDER_USED_RATIO else Gate.OK))
                if c == 7:
                    item.setForeground(QColor(Gate.TEXT_2))
                    item.setToolTip(text)
                self.table.setItem(r, c, item)

    def _selected(self):
        """The selected licence, found by its id - the table can be sorted."""
        from slate.gui.components.table_tools import selected_keys
        keys = selected_keys(self.table)
        if not keys:
            return None
        for row in self._rows:
            key = row.get("id") if row.get("id") is not None else row.get("software_name")
            if key == keys[0]:
                return row
        return None

    def _sync_buttons(self, *_):
        picked = self._selected() is not None
        self.btn_edit.setEnabled(picked)
        self.btn_remove.setEnabled(picked)
        self.btn_readings.setEnabled(picked)

    # --------------------------------------------------------------- actions
    def _guard(self, title) -> bool:
        if self.read_only:
            feedback.warn(self, title, "Licences are read-only for you. IT make changes here.")
            return False
        return True

    def add_licence(self):
        if not self._guard("Add licence"):
            return
        dialog = LicenceDialog(parent=self, repo=self.repo)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, seats, expiry = dialog.payload()
        if self.repo.save(name, seats, expiry, **dialog.details()):
            feedback.toast(self, "%s added." % name, "success")
        else:
            feedback.warn(self, "Add licence", "%s could not be recorded. Nothing was saved." % name)
        self.refresh()
        self.changed.emit()

    def edit_licence(self):
        if not self._guard("Edit licence"):
            return
        row = self._selected()
        if not row:
            return
        dialog = LicenceDialog(row, parent=self, repo=self.repo)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, seats, expiry = dialog.payload()
        if self.repo.save(name, seats, expiry, row.get("id"), **dialog.details()):
            feedback.toast(self, "%s saved." % name, "success")
        else:
            feedback.warn(self, "Edit licence", "The change to %s could not be recorded." % name)
        self.refresh()
        self.changed.emit()

    def remove_licence(self):
        """
        Delete a licence the studio no longer holds.

        The repository has been able to do this all along and no screen ever
        called it, so a cancelled contract stayed on the compliance list for
        ever - reported as expired, month after month, with nothing anybody
        could do about it from here.
        """
        if not self._guard("Remove licence"):
            return
        row = self._selected()
        if not row:
            return
        name = row.get("software_name") or "this licence"
        # Counted at the moment of removing, exactly what will go - not the
        # readings inside the peak window.
        count = self.repo.reading_count(row)
        if not feedback.confirm(
                self, "Remove %s" % name,
                "Remove %s and %s taken against it?\n\nThe readings go too - keeping them "
                "would leave a peak with nothing to compare it against. This cannot be undone."
                % (name, lc.plural(count, "usage reading")),
                yes_label="Remove licence", no_label="Keep it", destructive=True):
            return

        if self.repo.remove(row.get("id")):
            feedback.toast(self, "%s removed." % name, "success")
        else:
            feedback.warn(self, "Remove licence", "%s could not be removed." % name)
        self.refresh()
        self.changed.emit()

    def record_reading(self):
        if not self._guard("Record usage"):
            return
        licences = self.repo.licences()
        if not licences:
            feedback.inform(
                self, "Record usage",
                "Add a licence first - a reading has to belong to a product.")
            return
        dialog = ReadingDialog(licences, self._selected(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, in_use, total, licence_id = dialog.payload()
        if self.repo.record(name, in_use, total, taken_at=dialog.taken(), licence_id=licence_id,
                            recorded_by=self.username or None):
            feedback.toast(self, "Reading recorded: %d of %s in use." % (
                in_use, lc.plural(total, "seat")), "warning" if total and in_use > total else "success")
        else:
            feedback.warn(self, "Record usage", "The reading could not be recorded.")
        self.refresh()
        self.changed.emit()

    def import_readings(self):
        if not self._guard("Import from licence server"):
            return
        licences = self.repo.licences()
        if not licences:
            feedback.inform(self, "Import from licence server",
                            "Add the licences first - each reading has to belong to one.")
            return
        dialog = ImportReadingsDialog(licences, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        written = self.repo.import_readings(chosen, taken_at=dialog.taken(),
                                            recorded_by=self.username or None)
        if written == len(chosen) and written:
            feedback.toast(self, "%s imported." % lc.plural(written, "reading"), "success")
        else:
            feedback.warn(self, "Import from licence server",
                          "%d of %d readings were saved." % (written, len(chosen)))
        self.refresh()
        self.changed.emit()

    def show_readings(self):
        row = self._selected()
        if not row:
            return
        dialog = ReadingsDialog(row, self.repo, read_only=self.read_only, parent=self)
        dialog.exec()
        if dialog.changed_anything:
            self.refresh()
            self.changed.emit()
