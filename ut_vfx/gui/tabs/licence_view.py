"""
Licences, read as a compliance question rather than an inventory.

The old screen listed what had been bought. That answers nothing anybody
actually asks: it cannot say whether the studio is short of seats, and it
cannot say which renewal is money being burnt. Both answers need the peak
concurrent use, so this screen leads with the finding and keeps the seat count
as supporting detail.

Every row carries a sentence saying what it means. A status word on its own is
not something IT can take to purchasing.
"""

from datetime import date, datetime

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ut_vfx.core.infra.gate import Gate
from ut_vfx.core.infra.licence_repository import LicenceRepository
from ut_vfx.core.domain import licence_compliance as lc
from ..core.controls import make_button, page_title
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from .my_leave_view import Figure


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


class LicenceDialog(QDialog):
    """Add or correct a purchase."""

    def __init__(self, row=None, parent=None):
        super().__init__(parent)
        self.row = row or {}
        self.setWindowTitle("Edit licence" if row else "Add a licence")
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_2)

        self.name = QLineEdit(str(self.row.get("software_name") or ""))
        self.name.setPlaceholderText("Nuke, Houdini, Maya...")
        form.addRow("Software", self.name)

        self.seats = QSpinBox()
        self.seats.setRange(0, 9999)
        self.seats.setValue(int(self.row.get("total_seats") or 0))
        form.addRow("Seats bought", self.seats)

        self.expiry = QDateEdit()
        self.expiry.setCalendarPopup(True)
        existing = lc.as_date(self.row.get("expiration_date"))
        self.expiry.setDate(QDate(existing.year, existing.month, existing.day)
                            if existing else QDate.currentDate().addYears(1))
        form.addRow("Renews on", self.expiry)
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

    def _save(self):
        if not self.name.text().strip():
            QMessageBox.information(self, "Name it", "A licence needs a software name.")
            return
        self.accept()

    def payload(self):
        d = self.expiry.date()
        return (self.name.text().strip(), self.seats.value(),
                date(d.year(), d.month(), d.day()))


class ReadingDialog(QDialog):
    """Write down what the licence server is reporting right now."""

    def __init__(self, licences, preset=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record usage")
        self.setMinimumWidth(420)
        self._licences = licences

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_2)

        self.software = QComboBox()
        for row in licences:
            self.software.addItem(str(row.get("software_name") or ""), row)
        if preset:
            index = self.software.findText(str(preset.get("software_name") or ""))
            if index >= 0:
                self.software.setCurrentIndex(index)
        self.software.currentIndexChanged.connect(self._sync_seats)
        form.addRow("Software", self.software)

        self.in_use = QSpinBox()
        self.in_use.setRange(0, 9999)
        form.addRow("Seats in use now", self.in_use)
        root.addLayout(form)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Record", "primary", on_click=self.accept))
        root.addLayout(buttons)

        self._sync_seats()

    def _sync_seats(self, *_):
        row = self.software.currentData() or {}
        seats = int(row.get("total_seats") or 0)
        self.hint.setText(
            "%d seat(s) bought. Take this reading when the studio is busy - a "
            "quiet-afternoon number makes an over-subscribed licence look fine."
            % seats)

    def payload(self):
        row = self.software.currentData() or {}
        return (str(row.get("software_name") or ""), self.in_use.value(),
                int(row.get("total_seats") or 0))


class LicenceView(QWidget):
    """Purchased against used, and what that means for each renewal."""

    changed = Signal()

    def __init__(self, username: str = "", db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.repo = LicenceRepository(db_manager)
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        root.addWidget(page_title(
            "Licences",
            "What we bought, what we actually use, and what to do before each renewal"))

        self.figures = QHBoxLayout()
        self.figures.setSpacing(Gate.SPACE_2)
        root.addLayout(self.figures)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.window_pick = QComboBox()
        for label, days in (("Peak over 90 days", 90), ("Peak over 30 days", 30),
                            ("Peak over a year", 365)):
            self.window_pick.addItem(label, days)
        self.window_pick.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.window_pick)
        controls.addStretch(1)

        controls.addWidget(make_button("Record usage", "primary", on_click=self.record_reading))
        controls.addWidget(make_button("Add licence", "secondary", on_click=self.add_licence))
        self.btn_edit = make_button("Edit", "ghost", on_click=self.edit_licence)
        controls.addWidget(self.btn_edit)
        root.addLayout(controls)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Software", "State", "Seats", "Peak", "Used", "Renews", "What this means"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        for i in range(6):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "No licences recorded",
            "Add what the studio has bought, then record what the licence server "
            "reports. The second one is what makes a renewal decidable.",
            glyph="key")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self, *_):
        days = self.window_pick.currentData() or 90
        self._rows = self.repo.compliance(days)
        self._paint_figures(self._rows)
        self._paint_rows(self._rows)
        self.empty.refresh()
        self._sync_buttons()

    def _paint_figures(self, rows):
        while self.figures.count():
            item = self.figures.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        over = sum(1 for r in rows if r["state"] == lc.OVER)
        soon = sum(1 for r in rows if r["state"] in (lc.SOON, lc.EXPIRED))
        bought = sum(int(r.get("total_seats") or 0) for r in rows)
        # Only count spare seats where there is a reading to count against -
        # a licence nobody measured is not evidence of waste.
        spare = sum(max(0, int(r.get("total_seats") or 0) - int(r["peak"]))
                    for r in rows if r.get("peak") is not None)

        for label, value, caption, tone in (
            ("Short of seats", over, "more in use than we own", "BAD" if over else "OK"),
            ("Renewals to decide", soon, "expired or inside %d days" % lc.RENEWAL_SOON_DAYS,
             "WARN" if soon else "TEXT_DIM"),
            ("Seats bought", bought, "across all products", "TEXT"),
            ("Never used at once", spare, "seats paid for above peak",
             "INFO" if spare else "TEXT_DIM"),
        ):
            card = Figure(label, value, caption, tone)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.figures.addWidget(card)

    def _paint_rows(self, rows):
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            peak = row.get("peak")
            use = row.get("utilisation")
            left = row.get("days_left")
            expiry = lc.as_date(row.get("expiration_date"))

            if left is None:
                renews = "not recorded"
            elif left < 0:
                renews = "%s (gone)" % expiry.isoformat()
            else:
                renews = "%s (%d d)" % (expiry.isoformat(), left)

            cells = [
                row.get("software_name") or "",
                row["state"],
                str(int(row.get("total_seats") or 0)),
                "-" if peak is None else str(int(peak)),
                "-" if use is None else "%d%%" % round(use * 100),
                renews,
                row.get("finding") or "",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (2, 3, 4):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 1:
                    item.setForeground(QColor(_tone(row["tone"])))
                if c == 4 and use is not None:
                    item.setForeground(QColor(
                        Gate.BAD if use > 1 else
                        Gate.INFO if use < lc.UNDER_USED_RATIO else Gate.OK))
                if c == 6:
                    item.setForeground(QColor(Gate.TEXT_2))
                self.table.setItem(r, c, item)

    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return self._rows[rows[0]] if rows and rows[0] < len(self._rows) else None

    def _sync_buttons(self, *_):
        self.btn_edit.setEnabled(self._selected() is not None)

    # --------------------------------------------------------------- actions
    def add_licence(self):
        dialog = LicenceDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, seats, expiry = dialog.payload()
        if not self.repo.save(name, seats, expiry):
            QMessageBox.warning(self, "Not saved", "The licence could not be recorded.")
        self.refresh()
        self.changed.emit()

    def edit_licence(self):
        row = self._selected()
        if not row:
            return
        dialog = LicenceDialog(row, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, seats, expiry = dialog.payload()
        if not self.repo.save(name, seats, expiry, row.get("id")):
            QMessageBox.warning(self, "Not saved", "The change could not be recorded.")
        self.refresh()
        self.changed.emit()

    def record_reading(self):
        licences = self.repo.licences()
        if not licences:
            QMessageBox.information(
                self, "Nothing to record against",
                "Add a licence first - a reading has to belong to a product.")
            return
        dialog = ReadingDialog(licences, self._selected(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, in_use, total = dialog.payload()
        if self.repo.record(name, in_use, total):
            if total and in_use > total:
                QMessageBox.warning(
                    self, "More in use than we own",
                    "%d in use against %d seats. That is a compliance problem, not a "
                    "rounding error - it needs raising today." % (in_use, total))
        else:
            QMessageBox.warning(self, "Not saved", "The reading could not be recorded.")
        self.refresh()
        self.changed.emit()
