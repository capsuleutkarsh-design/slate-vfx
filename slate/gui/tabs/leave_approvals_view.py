"""
Leave, as the people who decide on it see it.

A supervisor and HR both act on this screen, and they are not doing the same
job. The supervisor knows whether the studio can spare somebody that week. HR
knows the policy and keeps the record. So a request travels: supervisor first,
then HR, and it is only spent once both have said yes.

Which decision you are being asked to make is decided by what you are - the
queue only offers you the stage you own, so a supervisor is never shown HR's
button and refused by it. HR can also decide a request still waiting on a
supervisor, for them: that is the way out for a request whose supervisor
cannot act, and it is recorded as such.

To decide well an approver needs more than the dates: the person's balance
before and after, what the days are made of (the sandwich rule), and who else
from the team is away then. The panel on the right shows all three for the
selected request.
"""

from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSplitter,
    QTableWidget, QVBoxLayout, QWidget,
)

from slate.core.infra.db_results import DatabaseUnavailableError
from slate.core.infra.gate import Gate
from slate.core.infra.leave_repository import LeaveRepository, as_date, NO_APPROVER
from slate.core.domain import leave_policy as lp
from ..core.controls import make_button, page_title, prose
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from ..core.table_style import style_table
from ..core.stat_card import StatStrip
from slate.gui.components.table_tools import (
    KeepSelection, make_item, selected_keys, setup_table,
)
from slate.core.domain import people
from slate.core.domain.dates import format_date
from .my_leave_view import (
    balance_text, days_text, decision_tooltip, kind_text, status_tone,
)

# HR's word for a request stuck at the supervisor stage with nobody able to
# take it: it is theirs to decide, so it is in their 'Waiting on me'.
STRANDED = "No supervisor can act - yours to decide"


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


class LeaveApprovalsView(QWidget):
    """The queue of other people's leave."""

    changed = Signal()

    COLUMNS = ["Person", "From", "To", "Type", "Days", "Balance after", "Status",
               "Waiting on", "Reason"]

    def __init__(self, username: str, stage: str = "HR", db_manager=None, parent=None):
        """
        stage is the decision this person is here to make - "Supervisor" or "HR".
        """
        super().__init__(parent)
        self.username = (username or "").strip()
        self.stage = stage if stage in lp.APPROVAL_STAGES else "HR"
        self.repo = LeaveRepository(db_manager)
        self._rows = []
        self._everything = []
        self._by_id = {}
        self._balances = {}

        self.waiting_status = (lp.STATUS_PENDING_SUPERVISOR if self.stage == "Supervisor"
                               else lp.STATUS_PENDING_HR)
        # What this person may act on. HR: their own stage, withdrawals of
        # approved leave, and - for the supervisor - requests stuck at the
        # first stage.
        if self.stage == "HR":
            self.actionable = (lp.STATUS_PENDING_HR, lp.STATUS_CANCEL_REQUESTED,
                               lp.STATUS_PENDING_SUPERVISOR)
            self.mine_first = (lp.STATUS_PENDING_HR, lp.STATUS_CANCEL_REQUESTED)
        else:
            self.actionable = (lp.STATUS_PENDING_SUPERVISOR,)
            self.mine_first = (lp.STATUS_PENDING_SUPERVISOR,)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        # The subtitle says what the default filter shows. It promised
        # "waiting on you first, then everything else" over a filter that
        # showed only what was waiting.
        root.addWidget(page_title(
            "Leave requests",
            "Waiting on your decision. Change the filter to see everything else."
            if self.stage == "HR"
            else "Your team's requests waiting on your decision. "
                 "Change the filter to see the rest."))

        # A leave year nobody closed. Balances already apply the cap to it,
        # but the record is only written when HR close the year.
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            f"color: {Gate.TEXT}; background: {Gate.WARN_SURFACE}; border: 1px solid {Gate.WARN}; "
            f"border-radius: {Gate.RADIUS_MD}px; padding: 8px 12px; font-size: 12.5px;")
        self.banner.hide()
        root.addWidget(self.banner)

        self.figures = StatStrip()
        root.addWidget(self.figures)
        self.card_mine = self.figures.add("Waiting on you", 0, caption="your decision")
        self.card_other = self.figures.add("Elsewhere in the chain", 0,
                                           caption="with the other approver")
        self.card_away = self.figures.add("Away today", 0, caption="approved and out")
        self.card_month = self.figures.add("Approved this month", 0,
                                           caption="leave starting this month")

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.filter_state = QComboBox()
        self.filter_state.addItem("Waiting on me", "mine")
        self.filter_state.addItem("Everything outstanding", "open")
        self.filter_state.addItem("Everything", "all")
        for s in lp.LEAVE_STATUSES:
            self.filter_state.addItem(s, s)
        self.filter_state.currentIndexChanged.connect(self.apply_filters)

        # Search and filter work on the rows already read. Every keystroke used
        # to read the whole request table again.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, reason or type...")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.apply_filters)
        self.search.textChanged.connect(lambda *_: self._search_timer.start())

        controls.addWidget(self.filter_state)
        controls.addWidget(self.search, 1)

        self.btn_approve = make_button("Approve", "primary", on_click=self.approve)
        self.btn_reject = make_button("Reject", "danger", on_click=self.reject_requests)
        controls.addWidget(self.btn_approve)
        controls.addWidget(self.btn_reject)

        # The holiday list, year end, comp off and project rest belong to HR
        # alone - a supervisor deciding one team's leave has no business editing
        # what every day in the studio costs. One menu, one style.
        self.btn_revoke = None
        if self.stage == "HR":
            self.btn_revoke = make_button(
                "Revoke", "secondary", on_click=self.revoke_requests,
                tooltip="Take back approved leave, with a reason the person sees")
            controls.addWidget(self.btn_revoke)
            tools = make_button("HR tools", "secondary")
            menu = QMenu(tools)
            menu.addAction("Holidays...", self.edit_calendar)
            menu.addAction("Year end...", self.close_year)
            menu.addAction("Comp off earned...", self.review_comp_off)
            if lp.policy().get("project_rest_enabled"):
                menu.addAction("Grant project rest...", self.grant_project_rest)
            tools.setMenu(menu)
            tools.setToolTip("Holidays, year end, comp off and project rest")
            controls.addWidget(tools)
        root.addLayout(controls)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        # Body-size rows (they were ~11 px). The reason takes what is left and
        # is cut with "..." - the full text is in the tooltip and the panel.
        # Person and Waiting on have a set width, cut with "..." and named in
        # full on hover: sized to their contents, one 63-character name pushed
        # Status, Waiting on and Reason off the screen.
        style_table(self.table, {
            "Person": ("interactive", 180), "From": "contents", "To": "contents",
            "Type": "contents", "Days": "numeric", "Balance after": "numeric",
            "Status": "contents", "Waiting on": ("interactive", 150), "Reason": "stretch",
        }, multi_select=True)
        self.table.horizontalHeaderItem(self.COLUMNS.index("Balance after")).setToolTip(
            "Paid leave left if only this request is approved")
        setup_table(self.table)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.itemSelectionChanged.connect(self._show_details)
        left_box.addWidget(self.table, 1)

        self.empty = EmptyState(
            "Nothing waiting on you",
            "Requests from the studio appear here when it is your turn to decide.",
            glyph="leave")
        left_box.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)
        split.addWidget(left)

        self.details = DetailsPanel()
        split.addWidget(self.details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([900, 320])
        root.addWidget(split, 1)

        self.refresh()
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=30, topics=("leave_requests", "holiday_calendar"))

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self, *_):
        everything = self.repo.all_requests()
        for row in everything:
            row["_status"] = lp.normalise_status(row.get("status"))

        # A supervisor decides for their own team. Every supervisor in the
        # studio used to see - and could approve - every request in it.
        if self.stage == "Supervisor":
            reports = self.repo.reports_to(self.username)
            if reports:
                everything = [r for r in everything
                              if str(r.get("user_id") or "").strip().lower() in reports]
            else:
                # Nobody is recorded as reporting to this person. Showing the
                # whole studio would be the old bug; showing nothing without
                # saying why reads as broken, so the empty state explains it.
                everything = []
                self.empty.set_message(
                    "Nobody reports to you yet",
                    "Leave requests appear here once somebody's record names you "
                    "as their manager. HR set that on the Users & Roles tab.")

        # Who each request waits on, read once for the whole list.
        for row, waiting in zip(everything, self.repo.waiting_on_all(everything)):
            row["_waiting"] = waiting
        self._everything = everything
        self._balances = {}
        self._paint_figures(everything)
        self._paint_banner()
        self.apply_filters()

    def apply_filters(self, *_):
        wanted = self.filter_state.currentData()
        rows = list(self._everything)
        if wanted == "mine":
            rows = [r for r in rows if self._waits_on_me(r)]
        elif wanted == "open":
            rows = [r for r in rows if r["_status"] in
                    lp.PENDING_STATUSES + (lp.STATUS_CANCEL_REQUESTED,)]
        elif wanted not in ("all", None):
            rows = [r for r in rows if r["_status"] == wanted]

        needle = self.search.text().strip().lower()
        if needle:
            rows = [r for r in rows if needle in " ".join(
                [str(r.get(k) or "") for k in ("user_id", "reason", "type")]
                + [people.display_name(r.get("user_id"))]).lower()]

        # Mine first, then soonest starting - somebody leaving on Monday needs
        # an answer before somebody leaving next month.
        rows.sort(key=lambda r: (
            0 if self._waits_on_me(r) else 1,
            str(r.get("start_date") or "9999"),
        ))
        self._rows = rows
        self._paint_rows(rows)
        self.empty.refresh()
        self._sync_buttons()
        self._show_details()

    def _is_own(self, row) -> bool:
        return str(row.get("user_id") or "").strip().lower() == self.username.lower()

    def _stranded(self, row) -> bool:
        """At the supervisor stage with no supervisor able to act: HR's to decide."""
        return (self.stage == "HR" and row["_status"] == lp.STATUS_PENDING_SUPERVISOR
                and row.get("_waiting") == NO_APPROVER)

    def _waits_on_me(self, row) -> bool:
        """
        This person's decision: their stage, and for HR also requests stranded
        at the supervisor stage (a supervisor who left, or cannot approve) -
        those sat in 'Elsewhere in the chain', outside HR's default view.
        """
        if self._is_own(row):
            return False
        return row["_status"] in self.mine_first or self._stranded(row)

    def _paint_banner(self):
        if self.stage != "HR":
            self.banner.hide()
            return
        try:
            year = self.repo.unclosed_year()
        except DatabaseUnavailableError:
            raise   # the screen's outage notice, not an empty panel
        except Exception:
            year = None
        if year is None:
            self.banner.hide()
            return
        self.banner.setText(
            "Leave year %d has not been closed. Balances already apply the carry-forward "
            "cap to it; close it in HR tools > Year end to record what carried over." % year)
        self.banner.show()

    def _paint_figures(self, everything):
        mine = sum(1 for r in everything if self._waits_on_me(r))
        open_total = sum(1 for r in everything if r["_status"] in
                         lp.PENDING_STATUSES + (lp.STATUS_CANCEL_REQUESTED,))
        other_stage = max(0, open_total - mine)

        today = date.today()
        away = 0
        this_month = 0
        for r in everything:
            if r["_status"] not in lp.GRANTED_STATUSES:
                continue
            start, end = as_date(r.get("start_date")), as_date(r.get("end_date"))
            if start and end and start <= today <= end:
                away += 1
            if start and start.year == today.year and start.month == today.month:
                this_month += 1

        self.card_mine.set_value(mine)
        self.card_mine.set_tone("warn" if mine else None)
        self.card_other.set_value(other_stage)
        self.card_away.set_value(away)
        self.card_away.set_tone("accent" if away else None)
        self.card_month.set_value(this_month)

    def balance_of(self, username) -> dict:
        key = str(username or "").strip().lower()
        if key not in self._balances:
            try:
                self._balances[key] = self.repo.balance(username)
            except DatabaseUnavailableError:
                raise   # the screen's outage notice, not an empty panel
            except Exception:
                self._balances[key] = {}
        return self._balances[key]

    def balance_before(self, row):
        """
        The requester's paid-leave balance with nothing waiting counted -
        what granted leave has left. None for decided or other-type requests.
        """
        if (row.get("type") or "").title() not in lp.ACCRUED_TYPES:
            return None
        if row["_status"] not in lp.PENDING_STATUSES:
            return None
        balance = self.balance_of(row.get("user_id"))
        if "available" not in balance:
            return None
        return float(balance["available"]) + float(balance.get("pending_from_pool") or 0)

    def balance_after(self, row):
        """
        The paid-leave balance if this request alone is approved.

        It was the balance after every waiting request, so two of somebody's
        requests (3 and 10 days) both read 17.
        """
        before = self.balance_before(row)
        if before is None:
            return None
        return before - float(row.get("days_charged") or 0)

    def _paint_rows(self, rows):
        self._by_id = {}
        me = people.display_name(self.username)
        with KeepSelection(self.table):
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                self._by_id[row.get("id")] = row
                status = row["_status"]
                charge = row.get("days_charged")
                who = row.get("user_id")
                person = people.person(who)
                name = people.display_name(who)
                # A leaver's request still has to be decided; say they have left.
                if person is not None and (person.has_left() or not person.active):
                    name += " (left)"
                waiting = row.get("_waiting") or "-"
                if self._is_own(row) and status in self.actionable:
                    waiting = "Needs another approver"
                elif self._stranded(row):
                    waiting = STRANDED
                elif status == lp.STATUS_PENDING_SUPERVISOR and waiting == me:
                    waiting = "You"
                after = self.balance_after(row)
                reason = row.get("reason") or ""
                if row.get("cancel_reason") and status == lp.STATUS_CANCEL_REQUESTED:
                    reason = "Withdrawal: %s" % row.get("cancel_reason")
                cells = [
                    make_item(name, key=row.get("id"), tooltip=people.label(who)),
                    make_item(format_date(row.get("start_date")),
                              sort_value=str(row.get("start_date") or "")),
                    make_item(format_date(row.get("end_date")),
                              sort_value=str(row.get("end_date") or "")),
                    make_item(kind_text(row)),
                    make_item(("%g" % float(charge)) if charge is not None else "-",
                              sort_value=float(charge or 0)),
                    make_item(balance_text(after) if after is not None else "-",
                              sort_value=after if after is not None else None,
                              foreground=Gate.BAD if (after is not None and after < 0) else None,
                              tooltip=("Overdrawn by %s" % days_text(-after))
                              if after is not None and after < 0 else None),
                    make_item(status, foreground=_tone(status_tone(status)),
                              tooltip=decision_tooltip(row) or None),
                    make_item(waiting, tooltip=waiting if waiting != "-" else None,
                              foreground=Gate.BAD if waiting in (NO_APPROVER, STRANDED) else (
                                  Gate.WARN if status in self.mine_first else None)),
                    make_item(reason, tooltip=reason or None),
                ]
                for c, item in enumerate(cells):
                    self.table.setItem(r, c, item)

    # --------------------------------------------------------------- details
    def _show_details(self, *_):
        picked = self._selected()
        if len(picked) != 1:
            self.details.show_nothing(len(picked))
            return
        row = picked[0]
        try:
            away = self.repo.also_away(row)
        except DatabaseUnavailableError:
            raise   # the screen's outage notice, not an empty panel
        except Exception:
            away = []
        balance = self.balance_of(row.get("user_id"))
        start, end = as_date(row.get("start_date")), as_date(row.get("end_date"))
        charge = None
        if start and end:
            try:
                charge = lp.days_charged(
                    start, end, self.repo.holidays_for(row.get("user_id"), start, end),
                    half_day=bool(row.get("half_day")) and start == end)
            except DatabaseUnavailableError:
                raise   # the screen's outage notice, not an empty panel
            except Exception:
                charge = None
        self.details.show_request(row, balance, charge, away)

    # --------------------------------------------------------------- actions
    def _selected(self):
        return [self._by_id[k] for k in selected_keys(self.table) if k in self._by_id]

    def _mine(self):
        """Only the ones this person may actually decide - never their own."""
        return [r for r in self._selected()
                if r["_status"] in self.actionable and not self._is_own(r)]

    def _sync_buttons(self, *_):
        picked = self._selected()
        actionable = bool(self._mine())
        self.btn_approve.setEnabled(actionable)
        self.btn_reject.setEnabled(actionable)
        own = any(self._is_own(r) for r in picked)
        tip = "Your own request needs another approver." if own and not actionable else ""
        self.btn_approve.setToolTip(tip)
        self.btn_reject.setToolTip(tip)
        if self.btn_revoke is not None:
            self.btn_revoke.setEnabled(any(
                r["_status"] in lp.GRANTED_STATUSES and not self._is_own(r) for r in picked))

    def _report(self, done, picked, outcomes):
        stale = [o for o in outcomes if getattr(o, "code", "") == "stale"]
        failed = [o for o in outcomes if not o and getattr(o, "code", "") != "stale"]
        if stale and not failed:
            QMessageBox.information(
                self, "Leave requests",
                "%d of %d had already been decided or withdrawn - the list has been "
                "refreshed." % (len(stale), len(picked)))
        elif failed or stale:
            reasons = sorted({getattr(o, "reason", "") for o in failed + stale if getattr(o, "reason", "")})
            QMessageBox.warning(
                self, "Not all saved",
                "%d of %d were recorded. The rest were left unchanged.\n\n%s"
                % (done, len(picked), "\n".join(reasons)))
        elif done:
            from slate.gui.components.feedback import toast
            toast(self, "%d request%s recorded." % (done, "" if done == 1 else "s"), "success")

    def _decide(self, approved: bool, note: str = "", picked=None):
        picked = picked if picked is not None else self._mine()
        if not picked:
            return
        outcomes = [self.repo.decide(row.get("id"), self.stage, approved, self.username, note)
                    for row in picked]
        done = sum(1 for o in outcomes if o)
        self._report(done, picked, outcomes)
        self.refresh()
        self.changed.emit()

    def approve(self):
        picked = self._mine()
        if not picked:
            return
        # Say where it goes next, because "approved" means different things at
        # the two stages and the person deciding should know which one they did.
        on_behalf = [r for r in picked if r["_status"] == lp.STATUS_PENDING_SUPERVISOR
                     and self.stage == "HR"]
        withdrawals = [r for r in picked if r["_status"] == lp.STATUS_CANCEL_REQUESTED]
        if self.stage == "Supervisor":
            onward = "It will go to HR for the final decision."
        else:
            onward = "That is the final approval - the days will be deducted."
            if on_behalf:
                onward += ("\n\n%d of them still wait on a supervisor. You are approving "
                           "on their behalf, and that is recorded." % len(on_behalf))
            if withdrawals:
                onward += ("\n\n%d of them ask to withdraw approved leave. Approving gives "
                           "those days back." % len(withdrawals))
        overdrawn = self.overdrawn_lines(picked)
        if overdrawn:
            onward += ("\n\n%s\nReject and ask for Unpaid instead if it should not come "
                       "from their balance." % "\n".join(overdrawn))
        if QMessageBox.question(
            self, "Approve leave",
            "Approve %d request%s?\n\n%s" % (len(picked), "" if len(picked) == 1 else "s", onward),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        self._decide(True, picked=picked)

    def overdrawn_lines(self, picked) -> list:
        """
        'Pari Shah will be overdrawn by 2 days.' for each person these
        approvals take below zero. Approve used to say nothing about it.
        """
        asked = {}
        for row in picked:
            if self.balance_before(row) is not None:
                key = str(row.get("user_id") or "").strip().lower()
                before, days = asked.get(key, (self.balance_before(row), 0.0))
                asked[key] = (before, days + float(row.get("days_charged") or 0))
        return ["%s will be overdrawn by %s." % (people.display_name(who), days_text(days - before))
                for who, (before, days) in sorted(asked.items()) if before - days < 0]

    def reject_requests(self):
        picked = self._mine()
        if not picked:
            return
        dialog = ReasonDialog(
            "Reject leave", "Reject %s? The person who asked will see why."
            % people.plural(len(picked), "request"), "Reject", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._decide(False, dialog.reason(), picked=picked)

    def revoke_requests(self):
        picked = [r for r in self._selected()
                  if r["_status"] in lp.GRANTED_STATUSES and not self._is_own(r)]
        if not picked:
            return
        intro = "Revoke %s? The days go back to the balance." % people.plural(
            len(picked), "approved request")
        # Leave already taken does not disappear from attendance: those days
        # turn into absences, and the export carries them to payroll.
        started = [r for r in picked
                   if (as_date(r.get("start_date")) or date.max) <= date.today()]
        if started:
            intro += ("\n\n%s already started. Those days will show as absent in "
                      "attendance." % ("It has" if len(picked) == 1 else
                                       people.plural(len(started), "of them has",
                                                     "of them have")))
        dialog = ReasonDialog("Revoke approved leave", intro, "Revoke", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        reason = dialog.reason()
        outcomes = [self.repo.revoke(row.get("id"), self.username, reason)
                    for row in picked]
        self._report(sum(1 for o in outcomes if o), picked, outcomes)
        self.refresh()
        self.changed.emit()

    def edit_calendar(self):
        from .leave_admin import HolidayCalendarDialog
        HolidayCalendarDialog(self.repo, self).exec()
        # Day counts are charged against this list, so anything on screen that
        # was computed from it is now stale.
        self.refresh()
        self.changed.emit()

    def review_comp_off(self):
        """
        What the attendance record says people have earned back.

        The service that works this out has existed all along and nothing ever
        called it, so comp-off was never credited to anybody. It is shown as a
        preview first: a ledger nobody can audit is worse than no ledger.
        """
        from .leave_admin import CompOffReviewDialog
        CompOffReviewDialog(self.repo, self).exec()
        self.refresh()
        self.changed.emit()

    def close_year(self):
        from .leave_admin import YearEndDialog
        YearEndDialog(self.username, self.repo, self).exec()
        self.refresh()
        self.changed.emit()

    def grant_project_rest(self):
        from .leave_admin import GrantProjectRestDialog
        dialog = GrantProjectRestDialog(self.username, self.repo, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.outcome:
            from slate.gui.components.feedback import toast
            toast(self, "Project rest granted.", "success")
        self.refresh()
        self.changed.emit()


class ReasonDialog(QDialog):
    """
    The reason the person will read, for a rejection or a revoke.

    It was a bare one-line QInputDialog, and an empty reason closed it and
    opened a second message; the problem is now said in place.
    """

    def __init__(self, title, intro, action, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        text = QLabel(intro)
        text.setWordWrap(True)
        root.addWidget(text)
        self.text = prose(QPlainTextEdit())
        self.text.setPlaceholderText("Why? The person will see this.")
        self.text.setFixedHeight(90)
        root.addWidget(self.text)
        self.note = QLabel("A reason is needed - the person will read it.")
        self.note.setStyleSheet(f"color: {Gate.WARN}; font-size: 12.5px;")
        self.note.hide()
        root.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button(action, "danger", on_click=self._accept))
        root.addLayout(buttons)

    def reason(self) -> str:
        return self.text.toPlainText().strip()

    def _accept(self):
        if not self.reason():
            self.note.show()
            self.text.setFocus()
            return
        self.accept()


class DetailsPanel(QFrame):
    """
    The selected request, worked out: the days it is made of, the balance
    before and after, and who else from the team is away.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("leaveDetails")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QFrame#leaveDetails {{ background: {Gate.PANEL}; border: 1px solid {Gate.LINE}; "
            f"border-radius: {Gate.RADIUS_MD}px; }}")
        self.setMinimumWidth(260)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Gate.SPACE_3, Gate.SPACE_3, Gate.SPACE_3, Gate.SPACE_3)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color: {Gate.TEXT}; font-size: 15px; font-weight: 600; "
                                 "background: transparent; border: none;")
        outer.addWidget(self.title)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12.5px; "
                                "background: transparent; border: none;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(self.body)
        outer.addWidget(scroll, 1)
        self.show_nothing(0)

    @staticmethod
    def _esc(text) -> str:
        import html
        return html.escape(str(text or ""))

    def show_nothing(self, count: int):
        self.title.setText("Details")
        self.body.setText(
            "Pick one request to see what it costs, the person's balance and who "
            "else from the team is away then." if count == 0 else
            "%d requests selected. Pick one to see its details." % count)

    def show_request(self, row, balance, charge, away):
        esc = self._esc
        # The same dash as the Joining & Leaving heading.
        self.title.setText("%s – %s" % (people.display_name(row.get("user_id")), kind_text(row)))
        lines = ["<b>%s to %s</b>" % (esc(format_date(row.get("start_date"), weekday=True)),
                                      esc(format_date(row.get("end_date"), weekday=True)))]
        if charge:
            lines.append("Working days: %d" % len(charge["working_days"]))
            if charge["sandwich_days"]:
                lines.append("Sandwich days: %s" % esc(", ".join(
                    format_date(d, weekday=True) for d in charge["sandwich_days"])))
        days = float(row.get("days_charged") or 0)
        lines.append("Charged: %s" % esc(days_text(days)))

        kind = (row.get("type") or "").title()
        status = lp.normalise_status(row.get("status"))
        def coloured(value):
            return "<span style='color:%s'>%s</span>" % (
                Gate.BAD if value < 0 else Gate.TEXT, esc(balance_text(value)))

        pending = status in lp.PENDING_STATUSES
        if kind in lp.ACCRUED_TYPES and "available" in balance:
            available = float(balance["available"])
            waiting = float(balance.get("pending_from_pool") or 0)
            lines.append("<br><b>Paid leave</b> (%s)" % esc(lp.POOL_NOTE))
            if pending:
                # Before and after this request alone; the other waiting
                # requests are named separately rather than folded in.
                before = available + waiting
                lines.append("Before this request: %s" % coloured(before))
                lines.append("After this request: %s" % coloured(before - days))
                if waiting > days:
                    lines.append("If everything waiting is approved (%s): %s"
                                 % (esc(days_text(waiting)), coloured(available)))
            else:
                lines.append("Available now: %s" % coloured(available))
        elif kind == "Comp Off" and balance:
            # Less what other requests waiting on comp off already hold.
            have = float(balance.get("comp_off", 0))
            held = float(balance.get("comp_off_pending", 0)) - (days if pending else 0)
            lines.append("<br><b>Comp off</b>: %s earned and unspent" % esc(days_text(have)))
            if held > 0:
                lines.append("Held by other requests waiting: %s" % esc(days_text(held)))
            if pending:
                lines.append("After this request: %s" % coloured(have - held - days))

        notes = decision_tooltip(row)
        if notes:
            lines.append("<br><b>Decisions</b><br>%s" % esc(notes).replace("\n", "<br>"))
        if row.get("reason"):
            lines.append("<br><b>Reason</b><br>%s" % esc(row.get("reason")))

        lines.append("<br><b>Also away from the team</b>")
        if away:
            for other in away:
                lines.append("%s: %s to %s (%s)" % (
                    esc(people.display_name(other.get("user_id"))),
                    esc(format_date(other.get("start_date"))),
                    esc(format_date(other.get("end_date"))),
                    esc(lp.normalise_status(other.get("status")))))
        else:
            lines.append("Nobody else from the team has leave then.")
        self.body.setText("<br>".join(lines))
