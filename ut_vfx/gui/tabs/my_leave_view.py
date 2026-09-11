"""
Leave, as the person asking for it sees it.

Three things and nothing else: how much have I got, ask for some, and what
happened to what I already asked for.

The balance is real. It comes from accrual - two days for every completed month
since you joined - not from a flat annual figure, which over-credits anybody who
started part way through the year. The day count is real too: it goes through
the studio's own calendar and sandwich rule, and the dialog shows the artist
*why* three days were deducted for one day away before they commit to it.
"""

from datetime import date

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPlainTextEdit, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ut_vfx.core.infra.gate import Gate
from ut_vfx.core.infra.leave_repository import LeaveRepository
from ut_vfx.core.domain import leave_policy as lp
from ..core.controls import make_button, page_title
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


def status_tone(status: str) -> str:
    s = lp.normalise_status(status)
    if s == lp.STATUS_APPROVED:
        return "OK"
    if s in (lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR):
        return "WARN"
    if s == lp.STATUS_REJECTED:
        return "BAD"
    return "IDLE"


class Figure(QFrame):
    """One number with a caption."""

    def __init__(self, label, value, caption="", tone="TEXT", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Gate.PANEL};
                border: 1px solid {Gate.LINE};
                border-left: 2px solid {_tone(tone)};
                border-radius: {Gate.RADIUS_MD}px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(1)

        name = QLabel(str(label).upper())
        name.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: 11px; "
            f"letter-spacing: 1.4px; background: transparent; border: none;")
        layout.addWidget(name)

        figure = QLabel(str(value))
        figure.setStyleSheet(
            f"color: {_tone(tone)}; font-family: {Gate.FONT_LABEL_STRONG}; font-size: 28px; "
            f"font-weight: 600; background: transparent; border: none;")
        layout.addWidget(figure)

        if caption:
            note = QLabel(caption)
            note.setWordWrap(True)
            note.setStyleSheet(
                f"color: {Gate.TEXT_DIM}; font-size: 12px; background: transparent; border: none;")
            layout.addWidget(note)


class RequestLeaveDialog(QDialog):
    """Ask for time off, and see what it will cost before committing."""

    def __init__(self, repo: LeaveRepository, available: float, comp_off: float, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.available = available
        self.comp_off = comp_off
        self.setWindowTitle("Request leave")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"background-color: {Gate.GROUND}; color: {Gate.TEXT};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_3)

        self.kind = QComboBox()
        self.kind.addItems(lp.LEAVE_TYPES)

        today = QDate.currentDate()
        self.start = QDateEdit(today)
        self.start.setCalendarPopup(True)
        self.end = QDateEdit(today)
        self.end.setCalendarPopup(True)

        self.half_day = QComboBox()
        self.half_day.addItems(["Full day", "Half day"])

        self.reason = QPlainTextEdit()
        self.reason.setPlaceholderText("Why, briefly. Your supervisor and HR both see this.")
        self.reason.setFixedHeight(76)

        form.addRow("Type", self.kind)
        form.addRow("From", self.start)
        form.addRow("To", self.end)
        form.addRow("Duration", self.half_day)
        form.addRow("Reason", self.reason)
        outer.addLayout(form)

        # The cost, worked out live. An artist should never be surprised after
        # the fact that a one day absence took three days off their balance.
        self.cost = QLabel("")
        self.cost.setWordWrap(True)
        self.cost.setStyleSheet(
            f"color: {Gate.TEXT_2}; font-size: 13px; background: {Gate.PANEL}; "
            f"border: 1px solid {Gate.LINE}; border-radius: {Gate.RADIUS_MD}px; padding: 11px;")
        outer.addWidget(self.cost)

        self.note = QLabel("")
        self.note.setStyleSheet(f"color: {Gate.WARN}; font-size: 12.5px;")
        self.note.setWordWrap(True)
        self.note.hide()
        outer.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Send request", "primary", on_click=self._submit))
        outer.addLayout(buttons)

        for widget in (self.start, self.end):
            widget.dateChanged.connect(self._recost)
        self.half_day.currentIndexChanged.connect(self._recost)
        self.kind.currentIndexChanged.connect(self._recost)
        self.start.dateChanged.connect(self._sync_end)
        self._recost()

    def _sync_end(self, value):
        if self.end.date() < value:
            self.end.setDate(value)

    def charge(self) -> dict:
        start = self.start.date().toPython()
        end = self.end.date().toPython()
        return lp.days_charged(start, end, self.repo.holidays(start.year),
                               half_day=self.half_day.currentIndex() == 1)

    def _recost(self, *_):
        charge = self.charge()
        working = len(charge["working_days"])
        absorbed = charge["sandwich_days"]

        if not working:
            self.cost.setText(
                "Those dates are all non-working days, so there is nothing to deduct.")
            return

        text = "This will cost <b>%g day%s</b>." % (
            charge["total"], "" if charge["total"] == 1 else "s")
        if absorbed:
            days = ", ".join(d.strftime("%a %d %b") for d in absorbed)
            text += (" That includes %s, because taking the working day between "
                     "two holidays counts the holidays too." % days)

        kind = self.kind.currentText()
        if kind in lp.ACCRUED_TYPES:
            text += "  You have %g day(s) available." % self.available
        elif kind == "Comp Off":
            text += "  You have %g comp-off day(s)." % self.comp_off
        elif kind == "Unpaid":
            text += "  Unpaid leave is not deducted from your balance."
        self.cost.setText(text)

    def _submit(self):
        if self.end.date() < self.start.date():
            self.note.setText("The end date is before the start date.")
            self.note.show()
            return
        if not self.reason.toPlainText().strip():
            self.note.setText("Add a short reason - your supervisor will want one.")
            self.note.show()
            return

        charge = self.charge()
        if not charge["working_days"]:
            self.note.setText("Those dates contain no working days.")
            self.note.show()
            return

        kind = self.kind.currentText()
        if kind in lp.ACCRUED_TYPES and charge["total"] > self.available:
            self.note.setText(
                "That is %g day(s) and you have %g available. Anything beyond your "
                "balance becomes unpaid leave - ask for Unpaid instead, or shorten it."
                % (charge["total"], self.available))
            self.note.show()
            return
        if kind == "Comp Off" and charge["total"] > self.comp_off:
            self.note.setText("You have %g comp-off day(s) and this needs %g."
                              % (self.comp_off, charge["total"]))
            self.note.show()
            return

        self.accept()

    def values(self) -> dict:
        return {
            "type": self.kind.currentText(),
            "start": self.start.date().toPython(),
            "end": self.end.date().toPython(),
            "half_day": self.half_day.currentIndex() == 1,
            "reason": self.reason.toPlainText().strip(),
        }


class MyLeaveView(QWidget):
    """The artist's side of Leave."""

    changed = Signal()

    def __init__(self, username: str, db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.repo = LeaveRepository(db_manager)
        self._balance = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_4)

        header = QHBoxLayout()
        header.addWidget(page_title("My leave", "What you have, and what you have asked for"))
        header.addStretch(1)
        header.addWidget(make_button("Request leave", "primary", on_click=self.request_leave),
                         0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.figures = QHBoxLayout()
        self.figures.setSpacing(Gate.SPACE_2)
        root.addLayout(self.figures)

        caption = QLabel("MY REQUESTS")
        caption.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: 11.5px; "
            f"letter-spacing: 1.6px; background: transparent;")
        root.addWidget(caption)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["From", "To", "Type", "Days", "Status", "Waiting on", "Reason"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        for i in range(6):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "You have not requested any leave",
            "When you ask for time off it appears here, with where it has got to.",
            primary=("Request leave", self.request_leave),
            glyph="leave")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self):
        self._balance = self.repo.balance(self.username)
        requests = self.repo.for_user(self.username)

        while self.figures.count():
            item = self.figures.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        pool = self._balance
        cards = [
            ("Available", "%g" % pool["available"],
             "earned %g, used %g" % (pool["accrued"], pool["spent_from_pool"]), "ACCENT"),
            ("Accrued", "%g" % pool["accrued"],
             "%g/month since you joined" % lp.policy()["accrual_days_per_month"], "TEXT"),
            ("Awaiting approval", "%g" % pool["pending_from_pool"],
             "held against your balance", "WARN" if pool["pending_from_pool"] else "TEXT_DIM"),
            ("Comp off", "%g" % pool["comp_off"],
             "earned by working" if pool["comp_off"] else "not operated here",
             "OK" if pool["comp_off"] else "TEXT_DIM"),
        ]
        for label, value, caption, tone in cards:
            card = Figure(label, value, caption, tone)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.figures.addWidget(card)

        self.table.setRowCount(len(requests))
        for r, row in enumerate(requests):
            status = lp.normalise_status(row.get("status")) or lp.STATUS_PENDING_SUPERVISOR
            charge = row.get("days_charged")
            cells = [
                str(row.get("start_date") or ""),
                str(row.get("end_date") or ""),
                (row.get("type") or "").title(),
                ("%g" % float(charge)) if charge is not None else "-",
                status,
                lp.awaiting(status) or "-",
                row.get("reason") or "",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 4:
                    item.setForeground(QColor(_tone(status_tone(status))))
                self.table.setItem(r, c, item)

        self.empty.refresh()

    # --------------------------------------------------------------- actions
    def request_leave(self):
        dialog = RequestLeaveDialog(
            self.repo, self._balance.get("available", 0.0),
            self._balance.get("comp_off", 0.0), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        ok = self.repo.submit(self.username, values["type"], values["start"],
                              values["end"], values["half_day"], values["reason"])
        if not ok:
            QMessageBox.warning(self, "Not sent",
                                "The request was not saved. Nothing has been deducted.")
            return

        self.refresh()
        self.changed.emit()
