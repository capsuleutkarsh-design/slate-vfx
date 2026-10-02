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
    QComboBox, QDateEdit, QDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QMessageBox, QPlainTextEdit, QSizePolicy, QTableWidget,
    QVBoxLayout, QWidget,
)

from slate.core.infra.db_results import DatabaseUnavailableError
from slate.core.infra.gate import Gate
from slate.core.infra.leave_repository import LeaveRepository, NO_APPROVER
from slate.core.domain import leave_policy as lp
from ..core.controls import make_button, page_title
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from ..core.table_style import style_table
from ..core.stat_card import StatStrip
from ..core.data_display import setup_date_edit
from slate.gui.components.table_tools import (
    KeepSelection, make_item, select_keys, selected_keys, setup_table,
)
from slate.core.domain import people
from slate.core.domain.dates import format_date


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


def status_tone(status: str) -> str:
    s = lp.normalise_status(status)
    if s == lp.STATUS_APPROVED:
        return "OK"
    if s in (lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR, lp.STATUS_CANCEL_REQUESTED):
        return "WARN"
    if s == lp.STATUS_REJECTED:
        return "BAD"
    return "IDLE"


def days_text(value) -> str:
    """'1 day', '2.5 days'."""
    value = float(value or 0)
    return "%g day%s" % (value, "" if value == 1 else "s")


def balance_text(value) -> str:
    """A balance figure: negative balances read as overdrawn, never as 0."""
    value = float(value or 0)
    return "%g" % value


def kind_text(row) -> str:
    """'Casual', or 'Casual (second half)' for a half day."""
    kind = (row.get("type") or "").title()
    part = lp.half_day_label(row.get("half_day_part"))
    if not part and row.get("half_day") and str(row.get("half_day")).lower() not in ("0", "false"):
        part = "Half day"
    return "%s (%s)" % (kind, part.lower()) if part else kind


def decision_text(row) -> str:
    """
    What the approvers said, for the person who asked: the latest decision
    with who and when, and both stages' notes in the tooltip.
    """
    status = lp.normalise_status(row.get("status"))
    parts = []
    if row.get("cancel_reason") and status in (lp.STATUS_CANCELLED, lp.STATUS_CANCEL_REQUESTED):
        parts.append(str(row.get("cancel_reason")))
    hr_note = str(row.get("hr_note") or "").strip()
    sup_note = str(row.get("supervisor_note") or "").strip()
    if not hr_note and not sup_note:
        sup_note = str(row.get("decision_note") or "").strip()
    who = row.get("hr_by") or row.get("supervisor_by")
    when = row.get("hr_at") or row.get("supervisor_at")
    note = hr_note or sup_note
    if note:
        parts.append(note)
    if who and status not in lp.PENDING_STATUSES:
        parts.append("%s, %s" % (people.display_name(who), format_date(when)) if when
                     else people.display_name(who))
    return " - ".join(p for p in parts if p)


def decision_tooltip(row) -> str:
    lines = []
    if row.get("route_note"):
        lines.append(str(row.get("route_note")))
    if row.get("supervisor_by"):
        lines.append("Supervisor (%s): %s" % (people.display_name(row.get("supervisor_by")),
                                              row.get("supervisor_note") or "no note"))
    if row.get("hr_by"):
        lines.append("HR (%s): %s" % (people.display_name(row.get("hr_by")),
                                      row.get("hr_note") or row.get("decision_note") or "no note"))
    if row.get("cancel_reason"):
        lines.append("Withdrawal: %s" % row.get("cancel_reason"))
    return "\n".join(lines)


def end_of_next_leave_year(today: date = None) -> date:
    today = today or date.today()
    return date(today.year + 1, 12, 31)


class Figure(QFrame):
    """
    One number with a caption.

    Kept for the IT screens that still use it (Licences, Service desk); the
    Leave screens use the shared StatCard.
    """

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

    DURATIONS = (("", "Full day"),) + lp.HALF_DAY_PARTS

    def __init__(self, repo: LeaveRepository, available: float, comp_off: float,
                 username: str = "", parent=None, comp_off_pending: float = 0.0):
        super().__init__(parent)
        self.repo = repo
        self.available = available
        self.comp_off = comp_off
        self.comp_off_pending = comp_off_pending
        # Whose request this is. The holidays a request is charged against
        # depend on where the person works, and whether it clashes with
        # something depends on what they have already asked for.
        self.username = (username or "").strip()
        self.setWindowTitle("Request leave")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"QDialog {{ background-color: {Gate.GROUND}; }}")  # the dialog only: without a selector every field in it took this background

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_3)

        # Only what this person may ask for: Comp Off when the studio runs it,
        # never Project Rest - HR grant that, it is not requested.
        self.kind = QComboBox()
        self.kind.addItems(list(lp.requestable_types()))

        today = QDate.currentDate()
        # With the weekday ('Sat 3 Oct 2026'): whether a day is a Saturday is
        # exactly what matters when asking for leave. Weeks start on Monday.
        self.start = setup_date_edit(QDateEdit(today), weekday=True)
        self.end = setup_date_edit(QDateEdit(today), weekday=True)
        # Nothing beyond the end of next leave year: a date in 2030 was costed
        # as if it were next week.
        last = end_of_next_leave_year()
        latest = QDate(last.year, last.month, last.day)
        self.start.setMaximumDate(latest)
        self.end.setMaximumDate(latest)
        # The end can never be before the start - it used to say "nothing to
        # deduct" for a reversed range, as if those were holidays.
        self.end.setMinimumDate(self.start.date())

        self.half_day = QComboBox()
        for key, label in self.DURATIONS:
            self.half_day.addItem(label, key)

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

        # Past dates are allowed (sick leave is often asked for afterwards),
        # but said out loud.
        self.retro = QLabel("This is a retrospective request - your approver will see that.")
        self.retro.setWordWrap(True)
        self.retro.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12.5px;")
        self.retro.hide()
        outer.addWidget(self.retro)

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

        self.start.dateChanged.connect(self._sync_end)
        for widget in (self.start, self.end):
            widget.dateChanged.connect(self._recost)
            widget.dateChanged.connect(self._sync_half_day)
        self.half_day.currentIndexChanged.connect(self._recost)
        self.kind.currentIndexChanged.connect(self._recost)
        self._sync_half_day()
        self._recost()

    def _sync_half_day(self, *_):
        """
        Half day is only offered on a single day.

        Left enabled on a range it read as a discount on the whole request -
        five days away for 4.5 days of leave - so it is switched off rather
        than silently ignored.
        """
        single = self.start.date() == self.end.date()
        self.half_day.setEnabled(single)
        if not single and self.half_day.currentIndex() != 0:
            self.half_day.setCurrentIndex(0)
        self.half_day.setToolTip(
            "" if single else "A half day only applies to a single day.")

    def _sync_end(self, value):
        self.end.setMinimumDate(value)
        if self.end.date() < value:
            self.end.setDate(value)

    def half_day_part(self):
        return self.half_day.currentData() or None

    def charge(self) -> dict:
        start = self.start.date().toPython()
        end = self.end.date().toPython()
        # A half day on a range took half a day off the whole request, so five
        # days away cost 4.5. It only means anything on a single day.
        half = bool(self.half_day_part()) and start == end
        return lp.days_charged(
            start, end, self.repo.holidays_for(self.username, start, end),
            half_day=half)

    def _comp_off_for(self, start) -> float:
        """Comp-off still valid on the day of the leave, less what is already asked for."""
        try:
            valid = self.repo.comp_off_balance(self.username, on=start)
        except DatabaseUnavailableError:
            raise   # the screen's outage notice, not an empty panel
        except Exception:
            valid = self.comp_off
        return max(0.0, valid - self.comp_off_pending)

    def _recost(self, *_):
        start = self.start.date().toPython()
        self.retro.setVisible(start < date.today())
        if self.end.date() < self.start.date():
            self.cost.setText("The end date is before the start date.")
            return
        charge = self.charge()
        working = len(charge["working_days"])
        absorbed = charge["sandwich_days"]

        if not working:
            self.cost.setText(
                "Those dates are all non-working days, so there is nothing to deduct.")
            return

        text = "This will cost <b>%s</b>." % days_text(charge["total"])
        if absorbed:
            days = ", ".join(format_date(d, weekday=True) for d in absorbed)
            text += (" That includes %s, because taking the working day between "
                     "two holidays counts the holidays too." % days)

        kind = self.kind.currentText()
        if kind in lp.ACCRUED_TYPES:
            if self.available < 0:
                text += "  You are overdrawn by %s." % days_text(-self.available)
            else:
                text += "  You have %s of paid leave available." % days_text(self.available)
        elif kind == "Comp Off":
            text += "  You have %s of comp off for that date." % days_text(self._comp_off_for(start))
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

        # Asking twice for the same week held both against the balance, so a
        # fortnight vanished for one week away.
        clashes = self.repo.clash(self.username, self.start.date().toPython(),
                                  self.end.date().toPython())
        if clashes:
            first = clashes[0]
            self.note.setText(
                "You already have a request covering those days: %s to %s (%s, %s). "
                "Withdraw that one first, or pick different dates."
                % (format_date(first.get("start_date")), format_date(first.get("end_date")),
                   (first.get("type") or "Leave"), lp.normalise_status(first.get("status"))))
            self.note.show()
            return

        kind = self.kind.currentText()
        if kind in lp.ACCRUED_TYPES and charge["total"] > self.available:
            self.note.setText(
                "That is %s and you have %s available. Anything beyond your "
                "balance becomes unpaid leave - ask for Unpaid instead, or shorten it."
                % (days_text(charge["total"]), days_text(max(0.0, self.available))))
            self.note.show()
            return
        if kind == "Comp Off":
            have = self._comp_off_for(self.start.date().toPython())
            if charge["total"] > have:
                self.note.setText("You have %s of comp off for that date and this needs %s."
                                  % (days_text(have), days_text(charge["total"])))
                self.note.show()
                return

        self.accept()

    def values(self) -> dict:
        part = self.half_day_part()
        return {
            "type": self.kind.currentText(),
            "start": self.start.date().toPython(),
            "end": self.end.date().toPython(),
            "half_day": bool(part),
            "half_day_part": part,
            "reason": self.reason.toPlainText().strip(),
        }


class MyLeaveView(QWidget):
    """The artist's side of Leave."""

    changed = Signal()

    COLUMNS = ["From", "To", "Type", "Days", "Status", "Waiting on", "Decision", "Reason"]

    def __init__(self, username: str, db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.repo = LeaveRepository(db_manager)
        self._balance = {}
        self._requests = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_4)

        header = QHBoxLayout()
        # The title takes the room; squeezed beside a stretch its subtitle
        # wrapped into a column three words wide.
        header.addWidget(page_title("My leave", "What you have, and what you have asked for"), 1)
        header.addWidget(make_button("Request leave", "primary", on_click=self.request_leave),
                         0, Qt.AlignmentFlag.AlignTop)
        # Withdraw: a request still waiting is simply withdrawn; approved leave
        # that has not started goes to HR as "Cancellation requested".
        self.btn_cancel = make_button("Withdraw", "ghost", on_click=self.cancel_request)
        self.btn_cancel.setEnabled(False)
        header.addWidget(self.btn_cancel, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.figures = StatStrip()
        root.addWidget(self.figures)
        self.card_available = self.figures.add("Paid leave available", "", tone="accent")
        self.card_accrued = self.figures.add("Earned", "")
        self.card_pending = self.figures.add("Awaiting approval", "")
        self.card_booked = self.figures.add("Booked", "", caption="approved, still to come")
        self.card_comp = self.figures.add("Comp off", "", tone="ok")

        # Casual, Sick and Earned share one balance; this says how it was used.
        self.taken = QLabel("")
        self.taken.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px; background: transparent;")
        root.addWidget(self.taken)

        caption = QLabel("MY REQUESTS")
        caption.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: 11.5px; "
            f"letter-spacing: 1.6px; background: transparent;")
        root.addWidget(caption)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        style_table(self.table, {
            "From": "contents", "To": "contents", "Type": "contents", "Days": "numeric",
            "Status": "contents", "Waiting on": "contents", "Decision": ("interactive", 220),
            "Reason": "stretch",
        })
        setup_table(self.table, multi_select=False)
        self.table.itemSelectionChanged.connect(self._sync_cancel)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "You have not requested any leave",
            "When you ask for time off it appears here, with where it has got to.",
            primary=("Request leave", self.request_leave),
            glyph="leave")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=30, topics=("leave_requests", "holiday_calendar"))

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self):
        self._balance = self.repo.balance(self.username)
        requests = self.repo.for_user(self.username)
        self._paint_figures(self._balance)

        self._requests = list(requests)
        by_id = {}
        with KeepSelection(self.table):
            self.table.setRowCount(len(requests))
            for r, row in enumerate(requests):
                by_id[row.get("id")] = row
                status = lp.normalise_status(row.get("status")) or lp.STATUS_PENDING_SUPERVISOR
                charge = row.get("days_charged")
                waiting = self.repo.waiting_on(row) if status in lp.PENDING_STATUSES \
                    or status == lp.STATUS_CANCEL_REQUESTED else ""
                reason = row.get("reason") or ""
                cells = [
                    make_item(format_date(row.get("start_date")),
                              sort_value=str(row.get("start_date") or ""), key=row.get("id")),
                    make_item(format_date(row.get("end_date")),
                              sort_value=str(row.get("end_date") or "")),
                    make_item(kind_text(row)),
                    make_item(("%g" % float(charge)) if charge is not None else "-",
                              sort_value=float(charge or 0)),
                    make_item(status, foreground=_tone(status_tone(status)),
                              tooltip=row.get("route_note") or None),
                    make_item(waiting or "-",
                              foreground=Gate.BAD if waiting == NO_APPROVER else None),
                    make_item(decision_text(row), tooltip=decision_tooltip(row) or None),
                    # Long reasons are cut with "..." and read in full on hover.
                    make_item(reason, tooltip=reason or None),
                ]
                for c, item in enumerate(cells):
                    self.table.setItem(r, c, item)
        self._by_id = by_id
        self.empty.refresh()
        self._sync_cancel()

    def _paint_figures(self, pool):
        available = float(pool.get("available") or 0)
        self.card_available.set_value(balance_text(available))
        if available < 0:
            self.card_available.set_tone("bad")
            self.card_available.set_caption("Overdrawn by %s. %s." % (days_text(-available), lp.POOL_NOTE))
        else:
            self.card_available.set_tone("accent")
            self.card_available.set_caption(lp.POOL_NOTE)

        self.card_accrued.set_value("%g" % pool.get("accrued", 0))
        self.card_accrued.set_caption(self._accrued_caption(pool))

        held = float(pool.get("pending_from_pool") or 0)
        self.card_pending.set_value("%g" % held)
        self.card_pending.set_tone("warn" if held else None)
        self.card_pending.set_caption("held against your balance")

        self.card_booked.set_value("%g" % float(pool.get("booked_from_pool") or 0))

        # Shown only where the studio operates comp-off. It said "not operated
        # here" whenever the balance happened to be 0 - to every new starter
        # at a studio that does operate it.
        operated = bool(lp.policy().get("comp_off_enabled"))
        self.card_comp.setVisible(operated)
        if operated:
            self.card_comp.set_value("%g" % float(pool.get("comp_off_available", pool.get("comp_off", 0))))
            pending = float(pool.get("comp_off_pending") or 0)
            self.card_comp.set_caption(
                "earned by working; %g waiting for approval" % pending if pending
                else "earned by working")

        taken = self.repo.taken_by_type(self.username)
        if taken:
            self.taken.setText("Taken in %d: %s" % (date.today().year, ", ".join(
                "%s %g" % (kind, days) for kind, days in sorted(taken.items()))))
            self.taken.show()
        else:
            self.taken.hide()

    @staticmethod
    def _accrued_caption(pool) -> str:
        """
        Where the 'Earned' figure comes from, in words that are true.

        It said '2/month since you joined' even after a year end (when the
        figure is what was carried plus this year) and for people with no
        joining date at all.
        """
        rate = lp.policy()["accrual_days_per_month"]
        year = (pool.get("as_of") or date.today()).year
        carried = float(pool.get("opening") or 0)
        earned_now = float(pool.get("accrued") or 0) - carried
        if pool.get("counting_from"):
            return "%g carried + %g earned in %d" % (carried, earned_now, year)
        if not pool.get("joined_on"):
            return "No joining date recorded - ask HR"
        return "%g/month since you joined" % rate

    def _selected_request(self):
        keys = selected_keys(self.table)
        if not keys:
            return None
        return getattr(self, "_by_id", {}).get(keys[0])

    def _sync_cancel(self, *_):
        """
        Withdraw is offered for a request still waiting on somebody, and for
        approved leave that has not started (HR then agree to it).
        """
        row = self._selected_request()
        status = lp.normalise_status(row.get("status")) if row else ""
        pending = status in lp.PENDING_STATUSES
        start = row.get("start_date") if row else None
        from slate.core.infra.leave_repository import as_date
        future = bool(row) and status == lp.STATUS_APPROVED and \
            (as_date(start) or date.min) > date.today()
        self.btn_cancel.setEnabled(bool(pending or future))
        if pending:
            self.btn_cancel.setToolTip("Withdraw this request.")
        elif future:
            self.btn_cancel.setToolTip("Ask HR to cancel this approved leave. "
                                       "The days come back once HR agree.")
        else:
            self.btn_cancel.setToolTip(
                "Pick a request that is still waiting, or approved leave that has "
                "not started yet.")

    # --------------------------------------------------------------- actions
    def request_leave(self):
        dialog = RequestLeaveDialog(
            self.repo, self._balance.get("available", 0.0),
            self._balance.get("comp_off", 0.0), self.username, self,
            comp_off_pending=self._balance.get("comp_off_pending", 0.0))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        outcome = self.repo.submit(self.username, values["type"], values["start"],
                                   values["end"], values["half_day"], values["reason"],
                                   half_day_part=values.get("half_day_part"))
        if not outcome:
            QMessageBox.warning(
                self, "Request not sent",
                (getattr(outcome, "reason", "") or "The request was not saved.")
                + " Nothing has been deducted.")
            return

        self.refresh()
        self.changed.emit()
        # Say where it went - "approved" and "sent" mean different things and
        # with future requests at the top the new row was easy to miss.
        detail = getattr(outcome, "detail", {}) or {}
        approver = detail.get("approver")
        to = people.display_name(approver) if approver else "HR"
        from slate.gui.components.feedback import toast
        toast(self, "Sent to %s - %s." % (to, days_text(detail.get("days", 0))), "success")
        if getattr(outcome, "request_id", None) is not None:
            select_keys(self.table, [outcome.request_id])

    def cancel_request(self):
        row = self._selected_request()
        if row is None:
            return
        status = lp.normalise_status(row.get("status"))
        span = "%s to %s" % (format_date(row.get("start_date")), format_date(row.get("end_date")))
        if status in lp.PENDING_STATUSES:
            if QMessageBox.question(
                self, "Withdraw this request",
                "Withdraw your leave from %s?\n\nThe days it is holding go "
                "back into your balance straight away." % span,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            ) != QMessageBox.StandardButton.Yes:
                return
            outcome = self.repo.cancel(row.get("id"), self.username)
            done = "Request withdrawn."
        else:
            reason, ok = QInputDialog.getText(
                self, "Withdraw approved leave",
                "Your leave from %s is approved. HR will be asked to cancel it, and the "
                "days come back once they agree.\n\nWhy? (optional)" % span)
            if not ok:
                return
            outcome = self.repo.request_cancellation(row.get("id"), self.username, reason)
            done = "Sent to HR to cancel."

        if not outcome:
            QMessageBox.warning(
                self, "Not withdrawn",
                getattr(outcome, "reason", "") or
                "That request could not be withdrawn. Somebody may have decided "
                "on it already - refresh and look at its status.")
        else:
            from slate.gui.components.feedback import toast
            toast(self, done, "success")
        self.refresh()
        self.changed.emit()
