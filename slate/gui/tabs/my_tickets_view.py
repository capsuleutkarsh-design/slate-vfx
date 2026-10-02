"""
IT support, as the person who needs help sees it.

Three things, and nothing else: report a problem, see what state it is in and
who has it, and talk to whoever picked it up. Every service desk worth using
gives the person who raised a ticket that much visibility - not knowing whether
anyone has looked at it is the single most common complaint about internal IT.

Triage, assignment and the queue belong to the other side of this module
(service_desk_view). Both sides open the same TicketThreadDialog, which knows
which side it is on: the requester can confirm a fix, withdraw or reopen; IT
can keep internal notes and reopen a closed ticket. Every action goes through
TicketRepository, so the ticket, the line in its conversation and the
notification are written together.
"""

import logging
import re

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextOption
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QScrollArea, QSizePolicy, QTableWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.domain import service_desk as sd
from slate.core.domain.service_desk import (
    CATEGORIES, IMPACT, URGENCY, PRIORITY_LABEL, describe_promise,
    normalise_status, priority_for, priority_tone, status_tone,
)
from slate.core.infra.ticket_repository import TicketError, TicketRepository, SUMMARY_MAX
from ..core.controls import make_button, page_title, tidy_form
from ..core.table_style import style_table
from ..core.empty_state import EmptyState
from slate.gui.core.offline_notice import on_database_error
from slate.gui.components import feedback
from slate.gui.components.state_notice import clear_state, show_load_error
from slate.core.domain import people
from slate.core.domain.dates import format_datetime

logger = logging.getLogger(__name__)

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


CHOOSE = "Choose…"


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


def _escape(text) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def issued_machines(username: str) -> list:
    """The machines this person has out on the loan ledger (for a new ticket)."""
    try:
        from slate.core.domain.onboarding_service import OnboardingService
        return [r.get("machine_name") for r in OnboardingService().held_by(username)
                if r.get("machine_name")]
    except DatabaseUnavailableError:
        return []
    except Exception as exc:
        logger.debug("Issued machines not read: %s", exc)
        return []


class RaiseTicketDialog(QDialog):
    """
    Report a problem. With for_someone_else=True (the IT desk) it also asks who
    the ticket is for - a phone call or somebody at the desk.
    """

    def __init__(self, parent=None, *, username: str = "", for_someone_else: bool = False,
                 calendar: sd.BusinessCalendar = None, machines_of=issued_machines):
        super().__init__(parent)
        self.setWindowTitle("Report a problem" if not for_someone_else else "New ticket")
        self.setMinimumWidth(500)
        self.setStyleSheet(f"QDialog {{ background-color: {Gate.GROUND}; }}")  # the dialog only: without a selector every field in it took this background
        self._calendar = calendar
        self._machines_of = machines_of
        self._username = username

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(Gate.SPACE_3)

        # Labels centred on their fields, not level with the top of them.
        form = tidy_form(QFormLayout())
        form.setVerticalSpacing(Gate.SPACE_3)

        self.person = None
        if for_someone_else:
            from slate.gui.components.person_picker import PersonPicker
            self.person = PersonPicker(placeholder="Who is the ticket for?", allow_empty=False)
            self.person.person_changed.connect(self._person_changed)
            form.addRow("Raised for", self.person)

        # No default category: "Workstation" was preselected whatever the
        # problem, so half the queue was filed under it.
        self.category = QComboBox()
        self.category.addItem(CHOOSE, "")
        for category in CATEGORIES:
            self.category.addItem(category, category)
        self.category.currentIndexChanged.connect(self._validate)

        # Two questions an artist can answer, rather than a priority they
        # cannot. Asked directly, everybody picks Critical and the queue stops
        # meaning anything at all.
        self.impact = QComboBox()
        for value, description in IMPACT:
            self.impact.addItem("%s - %s" % (value, description), value)
        self.impact.setCurrentIndex(2)

        self.urgency = QComboBox()
        for value, description in URGENCY:
            self.urgency.addItem("%s - %s" % (value, description), value)
        self.urgency.setCurrentIndex(1)

        self.summary = QLineEdit()
        self.summary.setPlaceholderText("One line - what is wrong?")
        self.summary.setMaxLength(SUMMARY_MAX)
        self.summary.textChanged.connect(self._validate)
        self.counter = QLabel("")
        self.counter.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        summary_row = QHBoxLayout()
        summary_row.setSpacing(Gate.SPACE_2)
        summary_row.addWidget(self.summary, 1)
        summary_row.addWidget(self.counter)

        self.detail = QPlainTextEdit()
        self.detail.setPlaceholderText(
            "What were you doing, what happened, and what you expected instead. "
            "Shot or project name helps.")
        self.detail.setFixedHeight(110)

        self.machine_note = QLabel("")
        self.machine_note.setWordWrap(True)
        self.machine_note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")

        form.addRow("Category", self.category)
        form.addRow("Who is affected", self.impact)
        form.addRow("Can you carry on", self.urgency)
        form.addRow("Summary", summary_row)
        form.addRow("Detail", self.detail)
        form.addRow("", self.machine_note)
        outer.addLayout(form)

        self.promise = QLabel("")
        self.promise.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12.5px;")
        self.promise.setWordWrap(True)
        outer.addWidget(self.promise)
        self.impact.currentIndexChanged.connect(self._show_promise)
        self.urgency.currentIndexChanged.connect(self._show_promise)
        self._show_promise()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.send_btn = make_button("Send to IT", "primary", on_click=self._submit)
        buttons.addWidget(self.send_btn)
        outer.addLayout(buttons)

        self._machines = []
        if not for_someone_else:
            self._show_machines(username)
        self._validate()
        self.summary.setFocus()

    # ----------------------------------------------------------- the form
    def current_priority(self) -> str:
        return priority_for(self.impact.currentData(), self.urgency.currentData())

    def _show_promise(self, *_):
        # Show the consequence as the answers are given, so the priority reads
        # as a result rather than a setting.
        self.promise.setText(describe_promise(self.current_priority(), self._calendar))

    def _person_changed(self, username):
        self._show_machines(username)
        self._validate()

    def _show_machines(self, username):
        self._machines = list(self._machines_of(username)) if username else []
        if self._machines:
            self.machine_note.setText("Your machine %s will be added to the ticket, so IT "
                                      "know where to look." % ", ".join(self._machines)
                                      if self.person is None else
                                      "Their machine %s will be added to the ticket."
                                      % ", ".join(self._machines))
            self.machine_note.show()
        else:
            self.machine_note.hide()

    def _problem(self) -> str:
        if self.person is not None and not self.person.username():
            return "Pick who the ticket is for."
        if not self.category.currentData():
            return "Choose what kind of problem it is."
        if not self.summary.text().strip():
            return "Give it a one-line summary so IT can triage it."
        return ""

    def _validate(self, *_):
        # Send stays off until the form can be sent; the reason sits on the
        # button's tooltip rather than in a note under the promise text.
        problem = self._problem()
        self.send_btn.setEnabled(not problem)
        self.send_btn.setToolTip(problem)
        length = len(self.summary.text())
        self.counter.setText("%d/%d" % (length, SUMMARY_MAX) if length > SUMMARY_MAX * 0.75 else "")

    def _submit(self):
        problem = self._problem()
        if problem:
            # Mark the field that needs attention and put the cursor in it.
            target = (self.person if self.person is not None and not self.person.username()
                      else self.category if not self.category.currentData() else self.summary)
            target.setFocus()
            self.send_btn.setToolTip(problem)
            return
        self.accept()

    def values(self) -> dict:
        return {
            "for": self.person.username() if self.person is not None else "",
            "category": self.category.currentData(),
            "impact": self.impact.currentData(),
            "urgency": self.urgency.currentData(),
            "priority": self.current_priority(),
            "summary": self.summary.text().strip(),
            "detail": self.detail.toPlainText().strip(),
            "machine": ", ".join(self._machines),
        }


class _BubbleText(QTextBrowser):
    """
    Message text that wraps anywhere and grows to fit. A QLabel cannot break
    a 600-character path, so one long token widened every bubble past the
    dialog and clipped the whole conversation.
    """

    def __init__(self, text: str, colour: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setOpenExternalLinks(False)
        # No padding: the global text-edit padding made the height below too
        # small, so the text scrolled inside its own bubble.
        self.setStyleSheet(f"QTextBrowser {{ background: transparent; border: none; padding: 0px; "
                           f"margin: 0px; color: {colour}; font-size: 13px; }}")
        self.setViewportMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setPlainText(text)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_):
        width = self.viewport().width()
        if width > 0:
            self.document().setTextWidth(width)
        margins = self.contentsMargins()
        height = (self.document().size().height() + margins.top() + margins.bottom()
                  + 2 * self.frameWidth())
        self.setFixedHeight(int(height) + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class TicketThreadDialog(QDialog):
    """A ticket, its state, and the conversation on it."""

    POLL_MS = 15000

    def __init__(self, ticket: dict, db, username: str, parent=None, *, side: str = None,
                 repo: TicketRepository = None):
        super().__init__(parent)
        self.repo = repo or TicketRepository(db)
        self.db = self.repo.db
        self.username = (username or "").strip()
        self.ticket = dict(ticket)
        # Which side of the desk this is. The requester's own ticket is the
        # requester's side even for IT staff.
        if side is None:
            side = "requester" if sd.is_requester(self.ticket, self.username) else "it"
        self.side = side
        self.changed_anything = False
        self._last_seen = None

        self.setWindowTitle("Ticket #%s" % self.ticket.get("id"))
        self.setMinimumSize(560, 520)
        self.setStyleSheet(f"QDialog {{ background-color: {Gate.GROUND}; }}")  # the dialog only: without a selector every field in it took this background
        # Room for the conversation: it opened at 560 x 533 and showed about
        # two messages under the description.
        from slate.gui.components.screen_fit import fit_to_screen
        fit_to_screen(self, 780, 720)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(Gate.SPACE_3)

        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; "
            f"font-size: 19px; font-weight: 600;")
        outer.addWidget(self.heading)

        self.meta = QLabel()
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.meta.setStyleSheet("font-size: 12.5px;")
        outer.addWidget(self.meta)

        # The description folds away so the conversation gets the room.
        self.toggle_body = make_button("Hide description", "ghost", on_click=self._toggle_body)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setStyleSheet(
            f"color: {Gate.TEXT_2}; font-size: 13px; background: {Gate.PANEL}; "
            f"border: 1px solid {Gate.LINE}; border-radius: {Gate.RADIUS_MD}px; padding: 12px;")
        body_row = QHBoxLayout()
        conversation = QLabel("CONVERSATION")
        conversation.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: 11.5px; "
            f"letter-spacing: 1.6px;")
        body_row.addWidget(conversation)
        body_row.addStretch(1)
        body_row.addWidget(self.toggle_body)
        outer.addWidget(self.body)
        outer.addLayout(body_row)

        self.thread_area = QScrollArea()
        self.thread_area.setWidgetResizable(True)
        self.thread_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thread_area.setStyleSheet(
            f"QScrollArea {{ background: {Gate.PANEL}; border: 1px solid {Gate.LINE}; "
            f"border-radius: {Gate.RADIUS_MD}px; }}")
        self.thread_holder = QWidget()
        self.thread_holder.setStyleSheet("background: transparent;")
        self.thread_layout = QVBoxLayout(self.thread_holder)
        self.thread_layout.setContentsMargins(12, 12, 12, 12)
        self.thread_layout.setSpacing(10)
        self.thread_area.setWidget(self.thread_holder)
        outer.addWidget(self.thread_area, 1)

        self.reply = QPlainTextEdit()
        self.reply.setFixedHeight(72)
        outer.addWidget(self.reply)

        row = QHBoxLayout()
        row.setSpacing(Gate.SPACE_2)
        self.internal = QCheckBox("Internal note (IT only)")
        self.internal.setToolTip("Kept with the ticket for IT. The person who raised it never sees it.")
        row.addWidget(self.internal)
        # The requester's own actions on their ticket.
        self.btn_fixed = make_button("This is fixed", "secondary", on_click=self._confirm_fixed,
                                     tooltip="Close the ticket - IT's fix worked")
        self.btn_withdraw = make_button("Withdraw", "ghost", on_click=self._withdraw,
                                        tooltip="You no longer need help with this")
        self.btn_reopen = make_button("Still broken - reopen", "secondary", on_click=self._reopen,
                                      tooltip="Put the ticket back in IT's queue")
        for b in (self.btn_fixed, self.btn_withdraw, self.btn_reopen):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(make_button("Close", "ghost", on_click=self.accept))
        self.btn_send = make_button("Send reply", "primary", on_click=self._send,
                                    tooltip="Send (Ctrl+Enter)")
        row.addWidget(self.btn_send)
        outer.addLayout(row)

        # Ctrl+Enter sends from the reply box (plain Enter is a new line).
        self.reply.installEventFilter(self)

        self._render_ticket()
        self._load_thread()

        # The other side's replies appear while the thread is open: the change
        # feed when it runs, a timer when it does not. (The tab's own refresh
        # waits while a dialog is open, so the dialog watches for itself.)
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        try:
            from slate.gui.components.change_feed import ChangeFeed
            feed = ChangeFeed.instance()
            if feed is not None:
                feed.watch(self, ("it_tickets", "it_ticket_comments"), lambda _c: self._poll())
        except Exception as exc:
            logger.debug("Thread not on the change feed: %s", exc)

    def eventFilter(self, obj, event):
        if (obj is getattr(self, "reply", None) and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._send()
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ painting
    def _render_ticket(self):
        t = self.ticket
        status = normalise_status(t.get("status"))
        priority = sd.normalise_priority(t.get("priority"))
        self.heading.setText(sd.summary_of(t.get("description")))
        # Only the status word in the status colour and the priority in its
        # own; the whole line used to take the status colour.
        sep = f"<span style='color:{Gate.TEXT_DIM}'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        owner = people.display_name(t.get("assigned_to"))
        parts = [
            f"<span style='color:{Gate.TEXT_2}'>{_escape(t.get('category') or 'Other')}</span>",
            f"<span style='color:{_tone(priority_tone(priority))}'>{_escape(PRIORITY_LABEL[priority])}</span>",
            f"<span style='color:{_tone(status_tone(status))}'>{_escape(sd.display_status(status, self.side))}</span>",
            f"<span style='color:{Gate.TEXT_DIM}'>{_escape('With ' + owner if owner else 'Not yet picked up')}</span>",
        ]
        self.meta.setText(sep.join(parts))
        body = sd.body_of(t.get("description"))
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.toggle_body.setVisible(bool(body))

        # Reply box: worded for the side it is on.
        if self.side == "it":
            self.reply.setPlaceholderText("Reply to %s" % (
                people.display_name(t.get("submitted_by")) or "the requester"))
        else:
            self.reply.setPlaceholderText(
                "Add something for IT - what you tried, a shot or project name, an update.")
        closed_for_it = self.side == "it" and status == "Closed"
        self.reply.setEnabled(not closed_for_it)
        self.btn_send.setEnabled(not closed_for_it)
        if closed_for_it:
            self.reply.setPlaceholderText("Closed. Reopen it to reply.")
        self.internal.setVisible(self.side == "it" and not closed_for_it)

        requester = self.side == "requester"
        self.btn_fixed.setVisible(requester and status == "Resolved")
        self.btn_withdraw.setVisible(requester and sd.is_open(status))
        self.btn_reopen.setVisible(status in sd.CLOSED_STATUSES)
        self.btn_reopen.setText("Still broken - reopen" if requester else "Reopen")

    def _toggle_body(self):
        show = not self.body.isVisible()
        self.body.setVisible(show)
        self.toggle_body.setText("Hide description" if show else "Show description")

    def _clear_thread(self):
        while self.thread_layout.count():
            item = self.thread_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _load_thread(self):
        self._clear_thread()
        try:
            rows = self.repo.comments(self.ticket.get("id"), include_internal=self.side == "it")
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Ticket thread could not be read")
            note = QLabel("The conversation could not be read: %s" % exc)
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {Gate.BAD}; font-size: 12.5px; background: transparent;")
            self.thread_layout.addWidget(note)
            self.thread_layout.addStretch(1)
            return

        self._last_seen = (rows[-1].get("id") if rows else None,
                           normalise_status(self.ticket.get("status")))
        if not rows:
            blank = QLabel("Nothing yet. IT will reply here." if self.side != "it"
                           else "Nothing yet. Your reply goes to %s." % (
                               people.display_name(self.ticket.get("submitted_by")) or "the requester"))
            blank.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12.5px; background: transparent;")
            self.thread_layout.addWidget(blank)
        else:
            for row in rows:
                self.thread_layout.addWidget(self._bubble(row))
        self.thread_layout.addStretch(1)
        # Land on the newest message, not the oldest.
        QTimer.singleShot(0, self._scroll_to_end)
        self._scroll_to_end()

    def _scroll_to_end(self):
        try:
            self.thread_holder.adjustSize()
            bar = self.thread_area.verticalScrollBar()
            bar.setValue(bar.maximum())
        except RuntimeError:
            pass

    def _bubble(self, row) -> QFrame:
        if row.get("kind") == "event":
            line = QLabel("%s   ·   %s" % (row.get("comment_text") or "",
                                              format_datetime(row.get("timestamp"))))
            line.setWordWrap(True)
            line.setTextFormat(Qt.TextFormat.PlainText)
            line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            line.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11.5px; background: transparent;")
            return line

        mine = (row.get("author") or "").strip().lower() == self.username.lower()
        internal = bool(row.get("internal"))
        frame = QFrame()
        frame.setObjectName("bubble")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        background = Gate.WARN_SURFACE if internal else (Gate.RAISED if mine else Gate.GROUND)
        edge = Gate.WARN if internal else (Gate.ACCENT if mine else Gate.LINE)
        frame.setStyleSheet(f"""
            QFrame#bubble {{
                background-color: {background};
                border: 1px solid {Gate.LINE};
                border-left: 2px solid {edge};
                border-radius: {Gate.RADIUS_MD}px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(3)

        # The author's name rather than their login, and the studio's date
        # format rather than the raw database text.
        who_text = "You" if mine else (people.display_name(row.get("author")) or "IT")
        if internal:
            who_text += "   ·   Internal note"
        if row.get("timestamp"):
            who_text += "   ·   %s" % format_datetime(row.get("timestamp"))
        who = QLabel(who_text)
        who.setTextFormat(Qt.TextFormat.PlainText)
        who.setStyleSheet(
            f"color: {Gate.WARN if internal else Gate.TEXT_DIM}; font-size: 11.5px; "
            f"background: transparent; border: none;")
        layout.addWidget(who)
        layout.addWidget(_BubbleText(row.get("comment_text") or "", Gate.TEXT_2))
        return frame

    # ------------------------------------------------------------ live
    def _poll(self):
        """Reload when the other side wrote something or moved the ticket."""
        try:
            fresh = self.repo.get(self.ticket.get("id"))
            rows = self.repo.comments(self.ticket.get("id"), include_internal=self.side == "it")
        except DatabaseUnavailableError:
            return          # the tab behind says so on its next refresh
        except Exception as exc:
            logger.debug("Thread poll skipped: %s", exc)
            return
        if fresh is None:
            return
        seen = (rows[-1].get("id") if rows else None, normalise_status(fresh.get("status")))
        if seen != self._last_seen:
            self.ticket = fresh
            self._render_ticket()
            self._load_thread()

    def _after_change(self):
        self.changed_anything = True
        fresh = self.repo.get(self.ticket.get("id"))
        if fresh:
            self.ticket = fresh
        self._render_ticket()
        self._load_thread()

    # ------------------------------------------------------------ actions
    def _send(self):
        message = self.reply.toPlainText().strip()
        if not message or not self.btn_send.isEnabled():
            return
        try:
            if self.side == "it" and self.internal.isChecked():
                self.repo.add_note(self.ticket, self.username, message)
            else:
                self.repo.reply(self.ticket, self.username, message)
        except DatabaseUnavailableError:
            raise
        except TicketError as exc:
            feedback.warn(self, "Send reply", str(exc))
            return
        except Exception as exc:
            # A refused write: the reply stays in the box.
            logger.exception("Reply not saved")
            feedback.warn(self, "Send reply",
                          "Your reply was not saved. It is still in the box.\n\n%s" % exc)
            return
        self.reply.clear()
        self.internal.setChecked(False)
        self._after_change()

    def _run(self, title, action, *args):
        try:
            done = action(self.ticket, self.username, *args)
        except DatabaseUnavailableError:
            raise
        except TicketError as exc:
            feedback.warn(self, title, str(exc))
            return False
        except Exception as exc:
            logger.exception("%s failed", title)
            feedback.warn(self, title, "Nothing was changed.\n\n%s" % exc)
            return False
        self._after_change()
        return done

    def _confirm_fixed(self):
        if self._run("This is fixed", self.repo.confirm_fixed):
            feedback.toast(self, "Ticket #%s closed. Thanks for confirming." % self.ticket.get("id"),
                           "success")

    def _withdraw(self):
        if not feedback.confirm(self, "Withdraw ticket",
                                "Withdraw ticket #%s? IT will stop working on it." % self.ticket.get("id"),
                                yes_label="Withdraw", no_label="Keep it"):
            return
        self._run("Withdraw ticket", self.repo.withdraw, self.reply.toPlainText().strip())
        self.reply.clear()

    def _reopen(self):
        self._run("Reopen ticket", self.repo.reopen, self.reply.toPlainText().strip())
        self.reply.clear()


class MyTicketsView(QWidget):
    """The artist's side of Ticketing."""

    changed = Signal()

    COLUMNS = ["#", "Summary", "Category", "Priority", "Status", "With", "Raised"]
    # Whether this view owns the "IT Support" sidebar count (not when it sits
    # beside IT's queue, whose count that is).
    show_badge = True

    def __init__(self, username: str, db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.repo = TicketRepository(db_manager)
        self.db = self.repo.db
        self._rows = []
        self._all = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        header = QHBoxLayout()
        header.addWidget(page_title(
            "IT Support", "Report a problem, and follow what happens to it"), 1)
        header.addStretch(1)
        # Centred on the title block (the title keeps SPACE_3 below its text).
        report = QVBoxLayout()
        report.setContentsMargins(0, 0, 0, Gate.SPACE_3)
        report.addStretch(1)
        report.addWidget(make_button("Report a problem", "primary", on_click=self.raise_ticket))
        report.addStretch(1)
        header.addLayout(report)
        root.addLayout(header)

        # Tickets IT are waiting on: said at the top, with a way straight in.
        self.banner = QFrame()
        self.banner.setObjectName("waitingBanner")
        self.banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.banner.setStyleSheet(
            f"QFrame#waitingBanner {{ background: {Gate.WARN_SURFACE}; border: 1px solid {Gate.WARN}; "
            f"border-radius: {Gate.RADIUS_MD}px; }}")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(Gate.SPACE_3, Gate.SPACE_2, Gate.SPACE_3, Gate.SPACE_2)
        self.banner_text = QLabel("")
        self.banner_text.setStyleSheet(f"color: {Gate.TEXT}; background: transparent; border: none;")
        banner_row.addWidget(self.banner_text, 1)
        self.banner_open = make_button("Open", "secondary", on_click=self._open_waiting)
        banner_row.addWidget(self.banner_open)
        self.banner.hide()
        root.addWidget(self.banner)

        tools = QHBoxLayout()
        tools.setSpacing(Gate.SPACE_2)
        self.include_closed = QCheckBox("Include closed")
        self.include_closed.setToolTip("Show resolved and closed tickets as well")
        self.include_closed.toggled.connect(lambda _on: self._paint())
        tools.addWidget(self.include_closed)
        tools.addStretch(1)
        self.btn_open = make_button("Open", "secondary", on_click=self.open_selected,
                                    tooltip="Read the ticket and reply (Enter, or double-click)")
        tools.addWidget(self.btn_open)
        root.addLayout(tools)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        # The shared table style: the selected row keeps its font and padding
        # (text used to wrap onto two lines) and uses the one selection colour.
        style_table(self.table, {
            "#": "numeric", "Summary": "stretch", "Category": "contents",
            "Priority": "contents", "Status": "contents", "With": "contents",
            "Raised": "contents",
        }, multi_select=False)
        from slate.gui.components.table_tools import setup_table
        setup_table(self.table, multi_select=False)
        # Enter / Return and double-click both arrive as "activated" (connecting
        # doubleClicked as well would open the ticket twice).
        self.table.activated.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        root.addWidget(self.table)

        self.empty = EmptyState(
            "No tickets raised",
            "If something is broken - a workstation, a licence, the farm - tell IT here "
            "and you can follow what happens to it.",
            primary=("Report a problem", self.raise_ticket),
            glyph="ticket",
        )
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)
        # The table is as tall as its rows (no big black body under three
        # tickets); the space left over belongs to the page.
        self._spacer = QWidget()
        root.addWidget(self._spacer, 1)

        self.refresh()
        # IT picking the ticket up, replying or closing it shows without a restart.
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=30,
                                         topics=("it_tickets", "it_ticket_comments"))

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self, *_):
        try:
            self._all = self.repo.for_requester(self.username)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("My tickets could not be read")
            show_load_error(self, exc, retry=self.refresh, what="your tickets")
            return
        clear_state(self)
        self._paint()

    def _refill(self):
        """Kept for callers of the old name."""
        self.refresh()

    def _paint(self):
        rows = list(self._all)
        if not self.include_closed.isChecked():
            rows = [r for r in rows if sd.is_open(r.get("status"))]
        # Waiting on you first: those need this person, nobody else.
        rows.sort(key=lambda r: (normalise_status(r.get("status")) != sd.WAITING,
                                 -int(r.get("id") or 0)))
        self._rows = rows

        from slate.gui.components.table_tools import KeepSelection, make_item, clear_sort
        clear_sort(self.table)
        with KeepSelection(self.table):
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                status = normalise_status(row.get("status"))
                priority = sd.normalise_priority(row.get("priority"))
                summary = sd.summary_of(row.get("description"))
                cells = [
                    str(row.get("id") or ""),
                    summary,
                    row.get("category") or "",
                    PRIORITY_LABEL[priority],
                    sd.display_status(status, "requester"),
                    # Who has it, by name - it showed the engineer's login.
                    people.display_name(row.get("assigned_to")) or "Not yet picked up",
                    format_datetime(row.get("created_at")),
                ]
                sort_values = [row.get("id"), None, None, sd.priority_rank(priority), None, None,
                               str(row.get("created_at") or "")]
                for c, text in enumerate(cells):
                    item = make_item(text, sort_value=sort_values[c],
                                     key=row.get("id") if c == 0 else None,
                                     align=(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                                     if c in (3, 6) else None)
                    if c == 1:
                        item.setToolTip(summary)
                    if c == 3:
                        item.setForeground(QColor(_tone(priority_tone(priority))))
                    if c == 4:
                        item.setForeground(QColor(_tone(status_tone(status))))
                    self.table.setItem(r, c, item)

        hidden = len(self._all) - len(rows)
        if not rows and hidden:
            self.empty.set_message("Nothing open", "%d closed ticket%s - tick Include closed to see "
                                   "them." % (hidden, "" if hidden == 1 else "s"))
        elif not rows:
            self.empty.set_message(
                "No tickets raised",
                "If something is broken - a workstation, a licence, the farm - tell IT here "
                "and you can follow what happens to it.")
        self.empty.refresh()
        self._fit_table()
        self._show_waiting()
        self._sync_buttons()

    def _fit_table(self):
        if not self._rows:
            self.table.setMaximumHeight(16777215)
            self._spacer.hide()
            return
        header = self.table.horizontalHeader().height() or 32
        rows = sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))
        self.table.setMaximumHeight(header + rows + 2 * self.table.frameWidth() + 4)
        self._spacer.show()

    def _show_waiting(self):
        waiting = [r for r in self._all if normalise_status(r.get("status")) == sd.WAITING]
        if waiting:
            first = waiting[0]
            more = "" if len(waiting) == 1 else " (and %d more)" % (len(waiting) - 1)
            self.banner_text.setText("IT need your answer on #%s - %s%s" % (
                first.get("id"), sd.summary_of(first.get("description")), more))
            self.banner.show()
        else:
            self.banner.hide()
        if not self.show_badge:
            return
        try:
            from slate.gui.components.notification_center import set_tab_badge
            set_tab_badge(self, "IT Support", len(waiting),
                          "%d ticket%s waiting for your answer" % (len(waiting), "" if len(waiting) == 1 else "s")
                          if waiting else None)
        except Exception:
            pass

    def _sync_buttons(self, *_):
        self.btn_open.setEnabled(self._selected() is not None)

    def _selected(self):
        from slate.gui.components.table_tools import selected_keys
        keys = selected_keys(self.table)
        by_id = {r.get("id"): r for r in self._rows}
        return by_id.get(keys[0]) if keys else None

    # --------------------------------------------------------------- actions
    def raise_ticket(self):
        try:
            calendar = self.repo.calendar()
        except DatabaseUnavailableError:
            calendar = None
        dialog = RaiseTicketDialog(self, username=self.username, calendar=calendar)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            ticket_id = self.repo.raise_ticket(
                self.username, values["category"], values["summary"], values["detail"],
                values["impact"], values["urgency"], machine=values["machine"] or None)
        except DatabaseUnavailableError:
            raise
        except TicketError as exc:
            feedback.warn(self, "Report a problem", str(exc))
            return
        except Exception as exc:
            logger.exception("Ticket not saved")
            feedback.warn(self, "Report a problem", "The ticket was not saved.\n\n%s" % exc)
            return
        self.refresh()
        from slate.gui.components.table_tools import select_keys
        select_keys(self.table, [ticket_id])
        promise = describe_promise(values["priority"], calendar).split(" - ", 1)[-1]
        feedback.toast(self, "Ticket #%s sent. %s" % (ticket_id, promise), "success")
        self.changed.emit()

    def _open_waiting(self):
        waiting = [r for r in self._all if normalise_status(r.get("status")) == sd.WAITING]
        if waiting:
            self._open(waiting[0])

    def open_selected(self, *_):
        row = self._selected()
        if row is not None:
            self._open(row)

    def _open(self, row):
        dialog = TicketThreadDialog(row, self.db, self.username, self, side="requester", repo=self.repo)
        dialog.exec()
        self.refresh()
        if dialog.changed_anything:
            self.changed.emit()
