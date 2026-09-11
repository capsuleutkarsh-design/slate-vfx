"""
Leave, as the people who decide on it see it.

A supervisor and HR both act on this screen, and they are not doing the same
job. The supervisor knows whether the studio can spare somebody that week. HR
knows the policy and keeps the record. So a request travels: supervisor first,
then HR, and it is only spent once both have said yes.

Which decision you are being asked to make is decided by what you are - the
queue only offers you the stage you own, so a supervisor is never shown HR's
button and refused by it.
"""

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.infra.leave_repository import LeaveRepository
from slate.core.domain import leave_policy as lp
from ..core.controls import make_button, page_title
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from .my_leave_view import Figure, status_tone


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


class LeaveApprovalsView(QWidget):
    """The queue of other people's leave."""

    changed = Signal()

    def __init__(self, username: str, stage: str = "HR", db_manager=None, parent=None):
        """
        stage is the decision this person is here to make - "Supervisor" or "HR".
        """
        super().__init__(parent)
        self.username = (username or "").strip()
        self.stage = stage if stage in lp.APPROVAL_STAGES else "HR"
        self.repo = LeaveRepository(db_manager)
        self._rows = []

        self.waiting_status = (lp.STATUS_PENDING_SUPERVISOR if self.stage == "Supervisor"
                               else lp.STATUS_PENDING_HR)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        root.addWidget(page_title(
            "Leave requests",
            "Waiting on you first, then everything else" if self.stage == "HR"
            else "Your team's requests, waiting on your decision"))

        self.figures = QHBoxLayout()
        self.figures.setSpacing(Gate.SPACE_2)
        root.addLayout(self.figures)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.filter_state = QComboBox()
        self.filter_state.addItem("Waiting on me", "mine")
        self.filter_state.addItem("Everything outstanding", "open")
        self.filter_state.addItem("Everything", "all")
        for s in lp.LEAVE_STATUSES:
            self.filter_state.addItem(s, s)
        self.filter_state.currentIndexChanged.connect(self.refresh)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by person or reason...")
        self.search.textChanged.connect(self.refresh)

        controls.addWidget(self.filter_state)
        controls.addWidget(self.search, 1)

        self.btn_approve = make_button("Approve", "primary", on_click=self.approve)
        self.btn_reject = make_button("Reject", "danger", on_click=self.reject_requests)
        controls.addWidget(self.btn_approve)
        controls.addWidget(self.btn_reject)

        # The calendar and the year end belong to HR alone - a supervisor
        # deciding one team's leave has no business editing what every day in
        # the studio costs.
        if self.stage == "HR":
            controls.addWidget(make_button(
                "Calendar", "secondary", on_click=self.edit_calendar,
                tooltip="The public holidays leave is charged against"))
            controls.addWidget(make_button(
                "Year end", "ghost", on_click=self.close_year,
                tooltip="Carry over what the cap allows and lapse the rest"))
        root.addLayout(controls)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Person", "From", "To", "Type", "Days", "Status", "Waiting on", "Reason"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        for i in range(7):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "Nothing waiting on you",
            "Requests from the studio appear here when it is your turn to decide.",
            glyph="leave")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self, *_):
        everything = self.repo.all_requests()
        for row in everything:
            row["_status"] = lp.normalise_status(row.get("status"))

        wanted = self.filter_state.currentData()
        rows = list(everything)
        if wanted == "mine":
            rows = [r for r in rows if r["_status"] == self.waiting_status]
        elif wanted == "open":
            rows = [r for r in rows if r["_status"] in
                    (lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR)]
        elif wanted not in ("all", None):
            rows = [r for r in rows if r["_status"] == wanted]

        needle = self.search.text().strip().lower()
        if needle:
            rows = [r for r in rows if needle in " ".join(
                str(r.get(k) or "") for k in ("user_id", "reason", "type")).lower()]

        # Mine first, then soonest starting - somebody leaving on Monday needs
        # an answer before somebody leaving next month.
        rows.sort(key=lambda r: (
            0 if r["_status"] == self.waiting_status else 1,
            str(r.get("start_date") or "9999"),
        ))
        self._rows = rows

        self._paint_figures(everything)
        self._paint_rows(rows)
        self.empty.refresh()
        self._sync_buttons()

    def _paint_figures(self, everything):
        while self.figures.count():
            item = self.figures.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        mine = sum(1 for r in everything if r["_status"] == self.waiting_status)
        other_stage = sum(1 for r in everything if r["_status"] in
                          (lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR)) - mine

        today = date.today()
        away = 0
        for r in everything:
            if r["_status"] != lp.STATUS_APPROVED:
                continue
            start, end = r.get("start_date"), r.get("end_date")
            if start and end and str(start) <= today.isoformat() <= str(end):
                away += 1

        for label, value, caption, tone in (
            ("Waiting on you", mine, "your decision", "WARN" if mine else "TEXT_DIM"),
            ("Elsewhere in the chain", max(0, other_stage), "with the other approver", "TEXT_DIM"),
            ("Away today", away, "approved and out", "ACCENT" if away else "TEXT_DIM"),
            ("Requests in total", len(everything), "all time", "TEXT"),
        ):
            card = Figure(label, value, caption, tone)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.figures.addWidget(card)

    def _paint_rows(self, rows):
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            status = row["_status"]
            charge = row.get("days_charged")
            cells = [
                row.get("user_id") or "",
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
                if c == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 5:
                    item.setForeground(QColor(_tone(status_tone(status))))
                if c == 6 and status == self.waiting_status:
                    item.setForeground(QColor(Gate.WARN))
                self.table.setItem(r, c, item)

    # --------------------------------------------------------------- actions
    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._rows[r] for r in rows if r < len(self._rows)]

    def _mine(self):
        """Only the ones this person is actually the approver for."""
        return [r for r in self._selected() if r["_status"] == self.waiting_status]

    def _sync_buttons(self, *_):
        actionable = bool(self._mine())
        self.btn_approve.setEnabled(actionable)
        self.btn_reject.setEnabled(actionable)

    def _decide(self, approved: bool, note: str = ""):
        picked = self._mine()
        if not picked:
            return
        done = 0
        for row in picked:
            if self.repo.decide(row.get("id"), self.stage, approved, self.username, note):
                done += 1
        if done < len(picked):
            QMessageBox.warning(
                self, "Not all saved",
                "%d of %d were recorded. The rest were left unchanged."
                % (done, len(picked)))
        self.refresh()
        self.changed.emit()

    def approve(self):
        picked = self._mine()
        if not picked:
            return
        # Say where it goes next, because "approved" means different things at
        # the two stages and the person deciding should know which one they did.
        if self.stage == "Supervisor":
            onward = "It will go to HR for the final decision."
        else:
            onward = "That is the final approval - the days will be deducted."
        if QMessageBox.question(
            self, "Approve leave",
            "Approve %d request(s)?\n\n%s" % (len(picked), onward),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        self._decide(True)

    def edit_calendar(self):
        from .leave_admin import HolidayCalendarDialog
        HolidayCalendarDialog(self.repo, self).exec()
        # Day counts are charged against this list, so anything on screen that
        # was computed from it is now stale.
        self.refresh()
        self.changed.emit()

    def close_year(self):
        from .leave_admin import YearEndDialog
        YearEndDialog(self.username, self.repo, self).exec()
        self.refresh()
        self.changed.emit()

    def reject_requests(self):
        picked = self._mine()
        if not picked:
            return
        note, ok = QInputDialog.getText(
            self, "Reject leave",
            "Why? The person who asked will see this.")
        if not ok:
            return
        if not note.strip():
            QMessageBox.information(
                self, "A reason is needed",
                "Rejecting without a reason leaves somebody guessing. Add one line.")
            return
        self._decide(False, note.strip())
