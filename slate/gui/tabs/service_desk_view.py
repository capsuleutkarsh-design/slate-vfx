"""
The IT side of support: a queue, not a list.

The difference between the two is what the rows are sorted by. A list shows you
what is newest. A queue shows you what is closest to breaking a promise - and
that is the whole job of a service desk.

What the old tab was: a table of log entries with an Add button, no owner, no
priority that meant anything, and nothing that could tell you a ticket had been
sitting untouched past its response window.

The SLA clocks count the studio's working hours (P1 around the clock); see
slate/core/domain/service_desk.py. Every action goes through TicketRepository,
which writes the ticket, a line in its conversation and the notification
together.
"""

import logging
from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QTableWidget, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.domain import service_desk as sd
from slate.core.domain.service_desk import (
    CATEGORIES, PRIORITY_LABEL, STATUSES, is_open, normalise_status, priority_rank,
    priority_tone, sla_state, sla_tone, status_tone,
)
from slate.core.infra.ticket_repository import TicketError, TicketRepository
from ..core.controls import prose, make_button, page_title, style_button, tidy_form
from ..core.stat_card import StatStrip
from ..core.table_style import style_table
from ..core.empty_state import EmptyState
from .my_tickets_view import RaiseTicketDialog, TicketThreadDialog
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.data_display import export_table_dialog
from slate.gui.components import feedback
from slate.gui.components.state_notice import clear_state, show_load_error
from slate.core.domain import people
from slate.core.domain.dates import MISSING

logger = logging.getLogger(__name__)

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


# ------------------------------------------------------------------ dialogs

class _Form(QDialog):
    """A small form dialog: fields, a hint, Cancel and one primary action."""

    def __init__(self, title: str, action: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.setStyleSheet(f"QDialog {{ background-color: {Gate.GROUND}; }}")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        self.root.setSpacing(Gate.SPACE_3)
        self.form = tidy_form(QFormLayout())
        self.root.addLayout(self.form)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        self.root.addWidget(self.hint)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.ok_btn = make_button(action, "primary", on_click=self._ok)
        row.addWidget(self.ok_btn)
        self.root.addLayout(row)

    def _ok(self):
        if self.ok_btn.isEnabled():
            self.accept()


class SetStatusDialog(_Form):
    """
    Move tickets to a status. Opens on the ticket's current status (it always
    started on Open, so a quick OK reopened things), and Resolved / Closed need
    a note for the person who raised it - the note goes into the conversation.
    """

    def __init__(self, tickets, parent=None):
        super().__init__("Set status", "Save", parent)
        self.status = QComboBox()
        for status in STATUSES:
            self.status.addItem(sd.display_status(status, "it"), status)
        current = normalise_status(tickets[0].get("status")) if tickets else "Open"
        self.status.setCurrentIndex(max(0, self.status.findData(current)))
        self.form.addRow("Status", self.status)
        self.note = prose(QPlainTextEdit())
        self.note.setFixedHeight(90)
        self.form.addRow("Note", self.note)
        if len(tickets) > 1:
            self.hint.setText("Applies to %d tickets." % len(tickets))
        self.status.currentIndexChanged.connect(self._validate)
        self.note.textChanged.connect(self._validate)
        self._validate()

    def _validate(self, *_):
        status = self.status.currentData()
        waiting = status == sd.WAITING
        needs_note = status in sd.CLOSED_STATUSES or waiting
        self.note.setPlaceholderText(
            "What do you need from them? They read this as a message." if waiting
            else "What was done - the person who raised it reads this." if needs_note
            else "Optional - shown in the conversation.")
        missing = needs_note and not self.note.toPlainText().strip()
        self.ok_btn.setEnabled(not missing)
        self.ok_btn.setToolTip(("Say what you need from them first." if waiting
                                else "Say what was done first.") if missing else "")

    def values(self):
        return self.status.currentData(), self.note.toPlainText().strip()


class PriorityDialog(_Form):
    """IT re-prioritise, with a reason that is kept on the ticket."""

    def __init__(self, ticket, parent=None, calendar: sd.BusinessCalendar = None):
        super().__init__("Change priority", "Change", parent)
        self._calendar = calendar
        self.priority = QComboBox()
        for code in sd.PRIORITIES:
            self.priority.addItem(PRIORITY_LABEL[code], code)
        self.priority.setCurrentIndex(sd.priority_rank(ticket.get("priority")))
        self.form.addRow("Priority", self.priority)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Why - e.g. blocks a delivery tomorrow")
        from slate.core.infra.ticket_repository import REASON_MAX
        self.reason.setMaxLength(REASON_MAX)
        self.form.addRow("Reason", self.reason)
        self.reason.textChanged.connect(self._validate)
        self.priority.currentIndexChanged.connect(self._validate)
        self._original = sd.normalise_priority(ticket.get("priority"))
        self._validate()

    def _validate(self, *_):
        new = self.priority.currentData()
        missing = ("Choose a different priority first." if new == self._original
                   else "Give a reason first." if not self.reason.text().strip() else "")
        self.ok_btn.setEnabled(not missing)
        self.ok_btn.setToolTip(missing)
        # What the change does to the clock, before it is made.
        if new != self._original and sd.priority_rank(new) < sd.priority_rank(self._original):
            self.hint.setText("The new promise starts now: %s"
                              % sd.describe_promise(new, self._calendar).split(" - ", 1)[-1])
        elif new != self._original:
            self.hint.setText("Still measured from when it was raised: %s"
                              % sd.describe_promise(new, self._calendar).split(" - ", 1)[-1])
        else:
            self.hint.setText("")

    def values(self):
        return self.priority.currentData(), self.reason.text().strip()


class AssignDialog(_Form):
    """Give tickets to somebody who works the queue."""

    def __init__(self, staff, parent=None):
        super().__init__("Assign to", "Assign", parent)
        from slate.gui.components.person_picker import PersonPicker
        wanted = {s.lower() for s in staff}
        self.person = PersonPicker(placeholder="Who should take it?", allow_empty=False,
                                   include=lambda username, _rec: username.lower() in wanted)
        self.form.addRow("Assign to", self.person)
        self.person.person_changed.connect(lambda _u: self._validate())
        if not staff:
            self.hint.setText("Nobody holds the manage_it ability yet - give it to a role in "
                              "Users & Roles first.")
        self._validate()

    def _validate(self):
        self.ok_btn.setEnabled(bool(self.person.username()))

    def username(self):
        return self.person.username()


class SlaReportDialog(QDialog):
    """Per month and priority: tickets resolved and how many kept their promise."""

    def __init__(self, tickets, parent=None, today: date = None, calendar: sd.BusinessCalendar = None):
        super().__init__(parent)
        self.setWindowTitle("SLA report")
        self.setMinimumSize(560, 320)
        self.setStyleSheet(f"QDialog {{ background-color: {Gate.GROUND}; }}")
        self.tickets = tickets
        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        self.calendar = calendar
        self.month = QComboBox()
        today = today or date.today()
        year, month = today.year, today.month
        self.month.addItem("Last 12 months", (year, month, 12))
        for _ in range(24):
            self.month.addItem(date(year, month, 1).strftime("%B %Y"), (year, month, 1))
            month -= 1
            if month == 0:
                year, month = year - 1, 12
        self.month.setCurrentIndex(1)
        self.month.currentIndexChanged.connect(self._fill)
        root.addWidget(self.month)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Priority", "Resolved", "Response kept", "Fix kept", "Median time to fix"])
        style_table(self.table, {"Priority": "stretch", "Resolved": "numeric",
                                 "Response kept": "numeric", "Fix kept": "numeric",
                                 "Median time to fix": "contents"})
        root.addWidget(self.table, 1)
        self.note = QLabel("Times are working hours; a P1 counts every hour.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        root.addWidget(self.note)
        row = QHBoxLayout()
        row.addWidget(make_button("Export…", "ghost",
                                  on_click=lambda: export_table_dialog(self, self.table, "sla_report")))
        row.addStretch(1)
        row.addWidget(make_button("Close", "ghost", on_click=self.accept))
        root.addLayout(row)
        self._fill()

    def _fill(self, *_):
        from slate.gui.components.table_tools import make_item
        year, month, months = self.month.currentData()
        lines = sd.sla_report(self.tickets, year, month, months, self.calendar)
        self.table.setRowCount(len(lines))
        for r, line in enumerate(lines):
            pct = lambda v: MISSING if v is None else "%d%%" % v
            median = line["median_hours"]
            cells = [PRIORITY_LABEL[line["priority"]], str(line["count"]),
                     pct(line["response_pct"]), pct(line["resolution_pct"]),
                     MISSING if median is None else sd.format_duration(
                         median, sd.around_the_clock(line["priority"]), self.calendar)]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, make_item(text))
        self.lines = lines


# ------------------------------------------------------------------- the pages

def desk_pages(queue, mine):
    """
    IT's two pages - the queue, and their own tickets - switched from the page
    header beside the title, so the title stays where Hardware, Licences and
    Deployment have it. A framed tab widget pushed it 35 px down and drew a box
    round the page. Both views keep their header row as .header.
    """
    from PySide6.QtWidgets import QTabBar, QTabWidget
    pages = QTabWidget()
    pages.setDocumentMode(True)
    pages.tabBar().hide()
    names = ("Queue", "My tickets")
    views = (queue, mine)
    for view, name in zip(views, names):
        pages.addTab(view, name)
    for index, view in enumerate(views):
        bar = QTabBar()
        bar.setDocumentMode(True)
        bar.setDrawBase(False)
        bar.setExpanding(False)
        for name in names:
            bar.addTab(name)
        bar.setCurrentIndex(index)
        bar.currentChanged.connect(pages.setCurrentIndex)
        pages.currentChanged.connect(bar.setCurrentIndex)
        view.header.insertWidget(1, bar, 0, Qt.AlignmentFlag.AlignVCenter)
    return pages


# ------------------------------------------------------------------- the queue

class ServiceDeskView(QWidget):
    """IT's queue."""

    changed = Signal()

    SEARCH_DELAY_MS = 250

    def __init__(self, username: str, db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.repo = TicketRepository(db_manager)
        self.db = self.repo.db
        self._all = []
        self._rows = []
        self._calendar = sd.DEFAULT_CALENDAR
        self._card_filter = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        header = self.header = QHBoxLayout()
        header.addWidget(page_title(
            "IT Support", "Service desk - sorted by what is closest to breaching, not by what is newest"), 1)
        header.addStretch(1)
        new_box = QVBoxLayout()
        new_box.setContentsMargins(0, 0, 0, Gate.SPACE_3)
        new_box.addStretch(1)
        new_box.addWidget(make_button(
            "New ticket", "primary", icon="plus", on_click=self.new_ticket,
            tooltip="Log a problem for somebody - a phone call or a walk-up"))
        new_box.addStretch(1)
        header.addLayout(new_box)
        root.addLayout(header)

        # Figures. Each one is a filter: click Breached to see the breached.
        self.stats = StatStrip(compact=True)
        self.card_open = self.stats.add("Unresolved", 0, tone="neutral",
                                        on_click=lambda: self._apply_card(""),
                                        tooltip="Open, in progress or waiting - show them all")
        self.card_breached = self.stats.add("Breached", 0, on_click=lambda: self._apply_card("breached"),
                                            tooltip="Show only tickets past their promise")
        self.card_risk = self.stats.add("At risk", 0, on_click=lambda: self._apply_card("at risk"),
                                        tooltip="Show only tickets close to their promise")
        self.card_unassigned = self.stats.add("Unassigned", 0,
                                              on_click=lambda: self._apply_card("unassigned"),
                                              tooltip="Show only tickets nobody has picked up")
        self.card_mine = self.stats.add("Mine", 0, on_click=lambda: self._apply_card("mine"),
                                        tooltip="Show only tickets assigned to you")
        root.addWidget(self.stats)

        # ---------------------------------------------------------- filtering
        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.filter_status = QComboBox()
        self.filter_status.addItem("Unresolved", "open")
        self.filter_status.addItem("Everything", "all")
        for s in STATUSES:
            self.filter_status.addItem(sd.display_status(s, "it"), s)

        self.filter_mine = QComboBox()
        self.filter_mine.addItem("Anyone's", "")
        self.filter_mine.addItem("Mine", self.username)
        self.filter_mine.addItem("Unassigned", "__none__")

        self.filter_priority = QComboBox()
        self.filter_priority.addItem("All priorities", "")
        for code in sd.PRIORITIES:
            self.filter_priority.addItem(PRIORITY_LABEL[code], code)

        self.filter_category = QComboBox()
        self.filter_category.addItem("All categories", "")
        for category in CATEGORIES:
            self.filter_category.addItem(category, category)

        for combo in (self.filter_status, self.filter_mine, self.filter_priority, self.filter_category):
            combo.currentIndexChanged.connect(self._filters_changed)
            controls.addWidget(combo)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tickets, #number or person…")
        self.search.setClearButtonEnabled(True)
        # Keystrokes filter what is already read - no database round trip
        # per letter - and only once typing pauses.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self.SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._apply_filters)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        controls.addWidget(self.search, 1)
        # Which figure the queue is filtered by - the combos cannot show it.
        self.card_chip = make_button("", "secondary", on_click=self.clear_card,
                                     tooltip="Showing what this figure counts - click to show everything again")
        self.card_chip.hide()
        controls.addWidget(self.card_chip)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        controls.addWidget(self.count_label)

        # Headers sort (by value); this puts the queue back in SLA order.
        self.btn_worst_first = make_button(
            "Worst first", "ghost", on_click=self.worst_first,
            tooltip="Undo a header sort: breached, then at risk, then by priority")
        controls.addWidget(self.btn_worst_first)
        root.addLayout(controls)

        actions = QHBoxLayout()
        actions.setSpacing(Gate.SPACE_2)
        self.btn_open = make_button("Open", "secondary", on_click=self.open_selected,
                                    tooltip="Read the ticket and reply (Enter, or double-click)")
        self.btn_take = make_button("Assign to me", "secondary", on_click=self.take)
        self.btn_status = make_button("Set status…", "secondary", on_click=self.change_status)
        self.btn_more = make_button("More ▾", "ghost",
                                    tooltip="Assign to…, Unassign, Change priority…, Responded by phone")
        more = QMenu(self.btn_more)
        self.act_assign = more.addAction("Assign to…", self.assign_to)
        self.act_unassign = more.addAction("Unassign", self.unassign)
        more.addSeparator()
        self.act_priority = more.addAction("Change priority…", self.change_priority)
        self.act_responded = more.addAction("Responded by phone", self.mark_responded)
        self.btn_more.setMenu(more)
        style_button(self.btn_more, "ghost")
        for b in (self.btn_open, self.btn_take, self.btn_status, self.btn_more):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(make_button("SLA report", "ghost", on_click=self.show_report,
                                      tooltip="Promises kept, per month and priority"))
        actions.addWidget(make_button(
            "Export…", "ghost",
            on_click=lambda: export_table_dialog(self, self.table, "it_tickets")))
        root.addLayout(actions)

        # -------------------------------------------------------------- queue
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "Summary", "Requester", "Category", "Priority", "Status", "Owner", "SLA"])
        # The shared table style: readable text with cell padding (it was ~10 px
        # and touched the grid lines), one selection colour. People's names
        # start at a fixed width and end in "..." (full name on the tooltip):
        # one long name sized to its contents squeezed Summary to a few words.
        style_table(self.table, {
            "#": "numeric", "Summary": "stretch", "Requester": ("interactive", 150),
            "Category": "contents", "Priority": "contents", "Status": "contents",
            "Owner": ("interactive", 140), "SLA": "contents",
        })
        # One line per ticket: a long summary is cut with "..." and read in
        # full on the tooltip (it wrapped into two cramped lines).
        self.table.setWordWrap(False)
        self.table.activated.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        from slate.gui.components.table_tools import setup_table
        setup_table(self.table)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "Nothing in the queue",
            "Tickets raised by the studio land here, worst-first.",
            glyph="ticket")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()
        # New tickets from other workstations, and the SLA clock, without a restart.
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=30,
                                         topics=("it_tickets", "it_ticket_comments"))

    # ------------------------------------------------------------------ data
    def _fetch(self) -> list:
        return self.repo.all()

    @on_database_error
    def refresh(self, *_):
        """Read the tickets (and the studio's hours) again, then show them."""
        try:
            self._calendar = self.repo.calendar(refresh=True)
            self._all = self._fetch()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("The ticket queue could not be read")
            show_load_error(self, exc, retry=self.refresh, what="the ticket queue")
            return
        clear_state(self)
        now = datetime.now()
        for r in self._all:
            r["_sla"] = sla_state(r, now, self._calendar)
        self._paint_stats(self._all)
        self._apply_filters()

    def _filters_changed(self, *_):
        self._show_card(None)
        self._apply_filters()

    def _show_card(self, card, label=""):
        """The figure the queue is filtered by: highlighted, and named in a chip."""
        self._active_card = card
        for each in self.stats.cards:
            each.set_selected(each is card)
        if card is None:
            self._card_filter = ""
        self.card_chip.setText("%s  ×" % label)
        self.card_chip.setVisible(bool(self._card_filter))

    def clear_card(self):
        self._show_card(None)
        self._apply_filters()

    def _apply_card(self, which):
        """A figure was clicked: show what it counts."""
        for combo in (self.filter_status, self.filter_mine, self.filter_priority, self.filter_category):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        if which == "mine":
            self.filter_mine.blockSignals(True)
            self.filter_mine.setCurrentIndex(1)
            self.filter_mine.blockSignals(False)
            which = ""
        elif which == "unassigned":
            self.filter_mine.blockSignals(True)
            self.filter_mine.setCurrentIndex(2)
            self.filter_mine.blockSignals(False)
            which = ""
        self._card_filter = which
        cards = {"breached": self.card_breached, "at risk": self.card_risk}
        self._show_card(cards.get(which), which.capitalize())
        self._apply_filters()

    def _matches_search(self, row, needle) -> bool:
        number = str(row.get("id") or "")
        # "#12" is that ticket and no other ("#1" used to find 10 and 11 as
        # well, through '#10' in the text searched). A bare "12" finds ticket
        # 12, and also tickets that mention 12.
        if needle.startswith("#") and needle[1:].strip().isdigit():
            return needle[1:].strip() == number
        if needle.isdigit() and needle == number:
            return True
        # People's names as well as their logins: the table shows names,
        # so that is what somebody types.
        # And the words the Status, Priority and SLA columns show ('Paused',
        # 'overdue', 'Waiting'): what you see is what you can search for.
        haystack = " ".join(
            [str(row.get(k) or "") for k in ("description", "submitted_by", "category", "assigned_to")]
            + [people.display_name(row.get("submitted_by")),
               people.display_name(row.get("assigned_to")),
               sd.display_status(row.get("status"), "it"),
               PRIORITY_LABEL[sd.normalise_priority(row.get("priority"))],
               self._sla_cell(row)[0]]).lower()
        return needle in haystack

    def _apply_filters(self):
        rows = list(self._all)
        wanted = self.filter_status.currentData()
        if wanted == "open":
            rows = [r for r in rows if is_open(r.get("status"))]
        elif wanted not in ("all", None):
            rows = [r for r in rows if normalise_status(r.get("status")) == wanted]

        owner = self.filter_mine.currentData()
        if owner == "__none__":
            rows = [r for r in rows if not (r.get("assigned_to") or "").strip()]
        elif owner:
            rows = [r for r in rows
                    if (r.get("assigned_to") or "").strip().lower() == owner.lower()]

        priority = self.filter_priority.currentData()
        if priority:
            rows = [r for r in rows if sd.normalise_priority(r.get("priority")) == priority]
        category = self.filter_category.currentData()
        if category:
            rows = [r for r in rows if (r.get("category") or "") == category]
        if self._card_filter:
            rows = [r for r in rows if r["_sla"]["state"] == self._card_filter]

        needle = self.search.text().strip().lower()
        if needle:
            rows = [r for r in rows if self._matches_search(r, needle)]

        # Worst first: breached, then at risk, then by priority, then by time left.
        rows.sort(key=lambda r: (
            sd.SLA_ORDER.get(r["_sla"]["state"], 5),
            priority_rank(r.get("priority")),
            r["_sla"]["hours_left"] if r["_sla"]["hours_left"] is not None else 9e9,
        ))
        self._rows = rows
        # Keep what IT had selected and where they had scrolled to: this also
        # runs on a timer, and must not pull the ticket out from under them.
        # The shared helper remembers tickets by id, not row numbers.
        from slate.gui.components.table_tools import KeepSelection
        with KeepSelection(self.table):
            self._paint_rows(rows)
        # A search that matches nothing is not an empty desk: say so, and offer
        # to clear it, rather than "Nothing in the queue".
        narrowed = (bool(needle) or bool(owner) or bool(priority) or bool(category)
                    or bool(self._card_filter) or wanted not in ("open", "all", None))
        self.empty.set_filtered(narrowed and bool(self._all), on_clear=self.clear_filters,
                                noun="tickets")
        from slate.gui.components.table_tools import row_count
        self.count_label.setText(row_count(len(rows), len(self._all), "ticket"))
        self.empty.refresh()
        self._sync_buttons()

    def clear_filters(self):
        """Back to the default view: open tickets, anyone's, no search."""
        self._show_card(None)
        combos = (self.filter_status, self.filter_mine, self.filter_priority, self.filter_category)
        for combo in combos:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        # The search box is not silenced: with its signals blocked it kept
        # showing its clear (x) button over an empty box.
        self.search.clear()
        self._search_timer.stop()
        self._apply_filters()

    def worst_first(self):
        """Back to the SLA order the queue is built in."""
        from slate.gui.components.table_tools import clear_sort
        clear_sort(self.table)
        self._apply_filters()

    def _paint_stats(self, everything):
        live = [r for r in everything if is_open(r.get("status"))]
        breached = sum(1 for r in live if r["_sla"]["state"] == "breached")
        at_risk = sum(1 for r in live if r["_sla"]["state"] == "at risk")
        unassigned = sum(1 for r in live if not (r.get("assigned_to") or "").strip())
        mine = sum(1 for r in live
                   if (r.get("assigned_to") or "").strip().lower() == self.username.lower())

        # Updated in place: the cards used to be torn down and rebuilt, with
        # their stylesheets, on every keystroke in the search box.
        for card, value, tone in (
            (self.card_open, len(live), "neutral"),
            (self.card_breached, breached, "bad" if breached else "idle"),
            (self.card_risk, at_risk, "warn" if at_risk else "idle"),
            (self.card_unassigned, unassigned, "warn" if unassigned else "idle"),
            # Zero is dim here like everywhere else (Mine stayed accent at 0).
            (self.card_mine, mine, "accent" if mine else "idle"),
        ):
            card.set_value(value)
            card.set_tone(tone)
        try:
            from slate.gui.components.notification_center import set_tab_badge
            set_tab_badge(self, "IT Support", unassigned,
                          "%d ticket%s nobody has picked up" % (unassigned, "" if unassigned == 1 else "s")
                          if unassigned else None)
        except Exception:
            pass

    def _sla_cell(self, row):
        state = row["_sla"]
        if state["state"] != "closed":
            text = sd.sla_text(state, self._calendar)
            due = state.get("due")
            tip = ""
            if due is not None and state["state"] != "paused":
                from slate.core.domain.dates import format_datetime
                tip = "Due %s%s" % (format_datetime(due, weekday=True),
                                    "" if state.get("around_the_clock") else " (working hours)")
            return text, tip, sla_tone(state["state"])
        # Closed: whether the promise was kept, recorded when it was resolved.
        met = row.get("resolution_met")
        if met is None:
            return MISSING, "", "IDLE"
        kept = sd._truthy(met)
        hours = row.get("resolution_hours")
        tip = "Fixed in %s of working time" % sd.format_duration(
            float(hours), sd.around_the_clock(row.get("priority")), self._calendar) if hours is not None else ""
        return ("Kept" if kept else "Missed"), tip, ("OK" if kept else "BAD")

    def _paint_rows(self, rows):
        from slate.gui.components.table_tools import make_item
        self.table.clearSpans()      # left by the "database did not answer" note
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            status = normalise_status(row.get("status"))
            priority = sd.normalise_priority(row.get("priority"))
            summary = sd.summary_of(row.get("description"))
            sla_text, sla_tip, sla_colour = self._sla_cell(row)
            cells = [
                str(row.get("id") or ""),
                summary,
                # Names, not logins ('Rahul Sharma', not 'rahul.s').
                people.display_name(row.get("submitted_by")),
                row.get("category") or "",
                PRIORITY_LABEL[priority],
                sd.display_status(status, "it"),
                people.display_name(row.get("assigned_to"), empty=MISSING),
                sla_text,
            ]
            hours = row["_sla"]["hours_left"]
            sort_values = [row.get("id"), None, None, None, priority_rank(priority), None,
                           None, hours if hours is not None else 9e9]
            for c, text in enumerate(cells):
                # Words, so left-aligned even though they sort by a number.
                item = make_item(text, sort_value=sort_values[c],
                                 key=row.get("id") if c == 0 else None,
                                 align=(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                                 if c in (4, 7) else None)
                if c == 1:
                    item.setToolTip(summary if not sd.body_of(row.get("description"))
                                    else "%s\n\n%s" % (summary, sd.body_of(row.get("description"))[:400]))
                elif c == 2:
                    # Who it is for; who logged it, when IT logged it for them.
                    tip = people.label(row.get("submitted_by"))
                    if row.get("raised_by"):
                        tip += "\nLogged by %s" % people.display_name(row.get("raised_by"))
                    item.setToolTip(tip)
                elif c == 4:
                    item.setForeground(QColor(_tone(priority_tone(priority))))
                elif c == 5:
                    item.setForeground(QColor(_tone(status_tone(status))))
                elif c == 6 and not (row.get("assigned_to") or "").strip() and is_open(status):
                    item.setForeground(QColor(Gate.WARN))
                elif c == 6:
                    item.setToolTip(text)
                elif c == 7:
                    item.setForeground(QColor(_tone(sla_colour)))
                    if sla_tip:
                        item.setToolTip(sla_tip)
                self.table.setItem(r, c, item)

    # --------------------------------------------------------------- actions
    def _selected(self):
        """The selected tickets, by id - the queue can be sorted by any header."""
        from slate.gui.components.table_tools import selected_keys
        wanted = selected_keys(self.table)
        by_id = {r.get("id"): r for r in getattr(self, "_rows", [])}
        return [by_id[k] for k in wanted if k in by_id]

    def _sync_buttons(self, *_):
        picked = self._selected()
        for b in (self.btn_open, self.btn_status, self.btn_more):
            b.setEnabled(bool(picked))
        # Owning and responding are for tickets still open: assigning a closed
        # one told its requester it "was picked up".
        live = self._open_only(picked)
        self.btn_take.setEnabled(bool(live))
        self.btn_take.setToolTip("" if live or not picked else "Resolved and closed tickets cannot be assigned")
        self.act_assign.setEnabled(bool(live))
        self.act_priority.setEnabled(len(picked) == 1 and bool(live))
        self.act_responded.setEnabled(any(not r.get("first_response_at") for r in live))
        self.act_unassign.setEnabled(any((r.get("assigned_to") or "").strip() for r in live))

    @staticmethod
    def _open_only(rows):
        return [r for r in rows if is_open(r.get("status"))]

    def _each(self, title, picked, action):
        """Run an action on each picked ticket; say what failed, never claim success for it."""
        done, failed = 0, []
        for row in picked:
            try:
                if action(row):
                    done += 1
            except DatabaseUnavailableError:
                raise
            except TicketError as exc:
                failed.append("#%s: %s" % (row.get("id"), exc))
            except Exception as exc:
                logger.exception("%s failed on #%s", title, row.get("id"))
                failed.append("#%s: %s" % (row.get("id"), exc))
        self.refresh()
        if done:
            self.changed.emit()
        if failed:
            feedback.warn(self, title, "Not every ticket was changed.\n\n" + "\n".join(failed[:8]))
        return done

    def take(self):
        # Picking a ticket up IS responding to it (the repository stops the
        # response clock). Taking one that a colleague owns asks first - it
        # used to move silently.
        picked = self._open_only(self._selected())
        owned = [r for r in picked if (r.get("assigned_to") or "").strip()
                 and r.get("assigned_to").strip().lower() != self.username.lower()]
        if owned:
            names = ", ".join(sorted({people.display_name(r["assigned_to"]) for r in owned}))
            if not feedback.confirm(
                    self, "Assign to me",
                    "%d of these %s already with %s. Take %s over?"
                    % (len(owned), "is" if len(owned) == 1 else "are", names,
                       "it" if len(owned) == 1 else "them"),
                    yes_label="Take over", no_label="Leave them"):
                picked = [r for r in picked if r not in owned]
        picked = [r for r in picked
                  if (r.get("assigned_to") or "").strip().lower() != self.username.lower()]
        if not picked:
            return
        if self._each("Assign to me", picked, lambda r: self.repo.assign(r, self.username, self.username)):
            feedback.toast(self, "%d ticket%s assigned to you." % (len(picked), "" if len(picked) == 1 else "s"),
                           "success")

    def assign_to(self):
        picked = self._open_only(self._selected())
        if not picked:
            return
        dialog = AssignDialog(self.repo.it_staff(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.username():
            return
        who = dialog.username()
        if self._each("Assign to", picked, lambda r: self.repo.assign(r, who, self.username)):
            feedback.toast(self, "Assigned to %s." % people.display_name(who), "success")

    def unassign(self):
        picked = [r for r in self._open_only(self._selected()) if (r.get("assigned_to") or "").strip()]
        if picked and self._each("Unassign", picked, lambda r: self.repo.assign(r, None, self.username)):
            feedback.toast(self, "Unassigned.", "success")

    def mark_responded(self):
        # For a phone call or a visit: replying in writing and picking a
        # ticket up already stop the response clock.
        picked = [r for r in self._open_only(self._selected()) if not r.get("first_response_at")]
        if picked and self._each("Responded by phone", picked,
                                 lambda r: self.repo.mark_responded(r, self.username)):
            feedback.toast(self, "Response recorded for %s." % ", ".join(
                "#%s" % r.get("id") for r in picked), "success")

    def change_status(self):
        picked = self._selected()
        if not picked:
            return
        dialog = SetStatusDialog(picked, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        status, note = dialog.values()
        done = self._each("Set status", picked,
                          lambda r: self.repo.set_status(r, status, self.username, note))
        if done:
            feedback.toast(self, "%d ticket%s now %s." % (
                done, "" if done == 1 else "s", sd.display_status(status, "it")), "success")

    def change_priority(self):
        picked = self._selected()
        if len(picked) != 1:
            return
        dialog = PriorityDialog(picked[0], self, calendar=self._calendar)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        priority, reason = dialog.values()
        if self._each("Change priority", picked,
                      lambda r: self.repo.change_priority(r, priority, reason, self.username)):
            feedback.toast(self, "Ticket #%s is now %s." % (picked[0].get("id"), PRIORITY_LABEL[priority]),
                           "success")

    def new_ticket(self):
        """A ticket for somebody else: a phone call, or a walk-up at the desk."""
        dialog = RaiseTicketDialog(self, username=self.username, for_someone_else=True,
                                   calendar=self._calendar)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            ticket_id = self.repo.raise_ticket(
                values["for"] or self.username, values["category"], values["summary"],
                values["detail"], values["impact"], values["urgency"],
                raised_by=self.username, machine=values["machine"] or None)
        except DatabaseUnavailableError:
            raise
        except TicketError as exc:
            feedback.warn(self, "New ticket", str(exc))
            return
        except Exception as exc:
            logger.exception("Ticket not saved")
            feedback.warn(self, "New ticket", "The ticket was not saved.\n\n%s" % exc)
            return
        self.refresh()
        from slate.gui.components.table_tools import select_keys
        select_keys(self.table, [ticket_id])
        feedback.toast(self, "Ticket #%s logged for %s." % (
            ticket_id, people.display_name(values["for"]) or values["for"]), "success")
        self.changed.emit()

    def show_report(self):
        SlaReportDialog(self._all, self, calendar=self._calendar).exec()

    def open_selected(self, *_):
        picked = self._selected()
        if not picked:
            return
        dialog = TicketThreadDialog(picked[0], self.db, self.username, self, repo=self.repo)
        dialog.exec()
        self.refresh()
        if dialog.changed_anything:
            self.changed.emit()
