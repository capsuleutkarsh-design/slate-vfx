"""
Everything the IT Support screens read and write.

Both sides of the desk - the requester's My tickets and IT's queue - used to
write their own UPDATE statements, each with its own idea of what a change
meant. Setting "Waiting on You" twice wiped the banked wait; reopening kept the
old resolved_at; a requester's reply left the ticket parked with its clock
stopped; nothing wrote a line in the conversation, and nobody was told
anything. The rules now live in service_desk (plan_status_change,
plan_after_reply) and are applied here, in one transaction per action, with
the line in the thread and the notification that go with them.

Notifications are sent through NotificationManager (the header bell) and never
stop the action that caused them.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Optional

from slate.core.domain import service_desk as sd

try:
    from .db_results import DatabaseUnavailableError
except ImportError:                                   # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

from .transaction import atomic

logger = logging.getLogger(__name__)

# Columns a planned change may write. Anything else is refused, so a change
# dict can never become an injection route.
_WRITABLE = {
    "status", "assigned_to", "first_response_at", "resolved_at", "waiting_since",
    "waiting_seconds", "priority", "response_met", "resolution_met", "resolution_hours",
}

TICKET_COLUMNS = (
    "id, submitted_by, category, description, status, priority, created_at, "
    "assigned_to, first_response_at, resolved_at, impact, urgency, waiting_since, "
    "waiting_seconds")
OPTIONAL_COLUMNS = ("raised_by", "response_met", "resolution_met", "resolution_hours")

SUMMARY_MAX = 120
NOTE_REQUIRED = sd.CLOSED_STATUSES


class TicketError(ValueError):
    """An action that was refused, with a sentence a person can act on."""


class TicketRepository:
    def __init__(self, db=None, calendar: sd.BusinessCalendar = None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db
        self._calendar = calendar
        self._columns = None

    # ------------------------------------------------------------- reading
    def calendar(self, refresh: bool = False) -> sd.BusinessCalendar:
        if self._calendar is None or refresh:
            self._calendar = sd.studio_calendar(self.db)
        return self._calendar

    def _select(self) -> str:
        if self._columns is None:
            try:
                from .migrations.workplace_schema import _column_exists
                extra = [c for c in OPTIONAL_COLUMNS if _column_exists(self.db, "it_tickets", c)]
            except Exception:
                extra = []
            self._columns = TICKET_COLUMNS + "".join(", " + c for c in extra)
        return self._columns

    def all(self) -> List[dict]:
        rows = self.db.execute_query(
            "SELECT %s FROM it_tickets ORDER BY id DESC" % self._select(), fetch="all")
        if rows is None:
            raise RuntimeError(self._why("The tickets could not be read."))
        return [dict(r) for r in rows]

    def for_requester(self, username: str) -> List[dict]:
        rows = self.db.execute_query(
            "SELECT %s FROM it_tickets WHERE LOWER(submitted_by) = LOWER(%%s) ORDER BY id DESC"
            % self._select(), (username,), fetch="all")
        if rows is None:
            raise RuntimeError(self._why("Your tickets could not be read."))
        return [dict(r) for r in rows]

    def get(self, ticket_id) -> Optional[dict]:
        row = self.db.execute_query(
            "SELECT %s FROM it_tickets WHERE id = %%s" % self._select(),
            (ticket_id,), fetch="one")
        return dict(row) if row else None

    def open_count(self) -> int:
        """
        How many tickets are still somebody's problem, by the desk's own idea
        of "open" (service_desk.is_open), whatever casing a status was stored
        in. Home's figure reads this, so the two never disagree.
        """
        rows = self.db.execute_query(
            "SELECT status, COUNT(*) AS c FROM it_tickets GROUP BY status", fetch="all")
        if rows is None:
            raise RuntimeError(self._why("The tickets could not be counted."))
        total = 0
        for row in rows:
            if isinstance(row, dict):
                status, count = row.get("status"), row.get("c")
            else:
                status, count = row[0], row[1]
            if sd.is_open(status):
                total += int(count or 0)
        return total

    def comments(self, ticket_id, include_internal: bool = False) -> List[dict]:
        """
        The conversation. Internal notes only for IT: the requester's query
        leaves them out in the database, not on screen.
        """
        has_internal = self._has("it_ticket_comments", "internal")
        has_kind = self._has("it_ticket_comments", "kind")
        columns = "id, author, comment_text, timestamp"
        columns += ", internal" if has_internal else ""
        columns += ", kind" if has_kind else ""
        sql = "SELECT %s FROM it_ticket_comments WHERE ticket_id = %%s" % columns
        if has_internal and not include_internal:
            sql += " AND (internal IS NULL OR internal = %s)"
            params = (ticket_id, self._false())
        else:
            params = (ticket_id,)
        rows = self.db.execute_query(sql + " ORDER BY id ASC", params, fetch="all")
        if rows is None:
            raise RuntimeError(self._why("The conversation could not be read."))
        out = []
        for r in rows:
            r = dict(r)
            r["internal"] = sd._truthy(r.get("internal"))
            r["kind"] = r.get("kind") or "reply"
            out.append(r)
        return out

    # ------------------------------------------------------------- raising
    def raise_ticket(self, submitted_by: str, category: str, summary: str, detail: str = "",
                     impact: str = "Low", urgency: str = "Low", raised_by: str = None,
                     machine: str = None) -> int:
        """
        A new ticket. Returns its number. raised_by is the IT person who logged
        it for somebody else (a phone call, a walk-up).
        """
        summary = (summary or "").strip()
        if not summary:
            raise TicketError("Give it a one-line summary so IT can triage it.")
        if len(summary) > SUMMARY_MAX:
            raise TicketError("Keep the summary to %d characters - the detail goes below it."
                              % SUMMARY_MAX)
        if category not in sd.CATEGORIES:
            raise TicketError("Choose what kind of problem it is.")
        submitted_by = (submitted_by or "").strip()
        if not submitted_by:
            raise TicketError("Say who the ticket is for.")
        detail = (detail or "").strip()
        if machine:
            detail = (detail + "\n\n" if detail else "") + "Machine: %s" % machine
        description = f"{summary}\n\n{detail}".strip() if detail else summary
        priority = sd.priority_for(impact, urgency)
        now = datetime.now().replace(microsecond=0)
        on_behalf = raised_by and raised_by.strip().lower() != submitted_by.lower()

        with atomic(self.db) as tx:
            columns = "submitted_by, category, description, status, priority, impact, urgency, created_at"
            values = [submitted_by, category, description, "Open", priority, impact, urgency, now]
            if on_behalf and self._has("it_tickets", "raised_by"):
                columns += ", raised_by"
                values.append(raised_by)
            result = tx.write("INSERT INTO it_tickets (%s) VALUES (%s) RETURNING id"
                              % (columns, ", ".join(["%s"] * len(values))), tuple(values))
            ticket_id = result.last_id
            if ticket_id is None:
                ticket_id = tx.value(
                    "SELECT MAX(id) AS id FROM it_tickets WHERE submitted_by = %s", (submitted_by,))
            if on_behalf:
                self._event(tx, ticket_id, raised_by,
                            "Logged by %s on %s's behalf." % (self._name(raised_by),
                                                              self._name(submitted_by)))

        if priority in ("P1", "P2"):
            self._notify(self.it_staff(exclude=raised_by),
                         "New %s ticket #%s from %s: %s" % (
                             sd.PRIORITY_LABEL[priority], ticket_id, self._name(submitted_by),
                             summary), "ticket")
        if on_behalf:
            self._notify([submitted_by], "%s logged ticket #%s for you: %s" % (
                self._name(raised_by), ticket_id, summary), "ticket")
        return int(ticket_id)

    # ------------------------------------------------------------- owning
    def assign(self, ticket: dict, to: Optional[str], by: str) -> bool:
        """
        Give the ticket to somebody (or nobody, to unassign). Picking a ticket
        up is responding to it: the response clock stops.
        """
        ticket = self._fresh(ticket)
        to = (to or "").strip() or None
        now = datetime.now().replace(microsecond=0)
        changes = {"assigned_to": to}
        if to:
            if ticket.get("first_response_at") is None:
                changes["first_response_at"] = now
            if sd.normalise_status(ticket.get("status")) == "Open":
                changes["status"] = "In Progress"
        if to:
            text = ("Picked up by %s." % self._name(to) if to.lower() == (by or "").lower()
                    else "Assigned to %s by %s." % (self._name(to), self._name(by)))
        else:
            text = "Unassigned by %s." % self._name(by)
        with atomic(self.db) as tx:
            self._apply(tx, ticket["id"], changes)
            self._event(tx, ticket["id"], by, text)
        if to and to.lower() != str(ticket.get("submitted_by") or "").lower():
            self._notify([ticket.get("submitted_by")],
                         "Ticket #%s was picked up by %s." % (ticket["id"], self._name(to)), "ticket")
            if to.lower() != (by or "").lower():
                self._notify([to], "%s gave you ticket #%s: %s" % (
                    self._name(by), ticket["id"], sd.summary_of(ticket.get("description"))),
                    "ticket")
        return True

    def mark_responded(self, ticket: dict, by: str, how: str = "by phone") -> bool:
        """A response that did not happen in writing - a call, a visit."""
        ticket = self._fresh(ticket)
        if ticket.get("first_response_at") is not None:
            return False
        with atomic(self.db) as tx:
            self._apply(tx, ticket["id"], {"first_response_at": datetime.now().replace(microsecond=0)})
            self._event(tx, ticket["id"], by, "%s responded %s." % (self._name(by), how))
        return True

    # ------------------------------------------------------------- status
    def set_status(self, ticket: dict, new_status: str, by: str, note: str = "") -> bool:
        """
        Move a ticket to a status, writing the line in its conversation.
        Resolved and Closed need a note for the requester. Returns False when
        the ticket was already in that status (nothing to do).
        """
        ticket = self._fresh(ticket)
        new = sd.normalise_status(new_status)
        note = (note or "").strip()
        if new in NOTE_REQUIRED and not note:
            raise TicketError("Say what was done - the person who raised it reads this.")
        changes = sd.plan_status_change(ticket, new, datetime.now().replace(microsecond=0),
                                        self.calendar())
        if changes is None:
            return False
        verb = {"Resolved": "Resolved", "Closed": "Closed", "Open": "Reopened",
                "In Progress": "Moved to In Progress",
                sd.WAITING: "Waiting on the requester"}.get(new, new)
        text = "%s by %s" % (verb, self._name(by)) + (": %s" % note if note else ".")
        with atomic(self.db) as tx:
            self._apply(tx, ticket["id"], changes)
            self._event(tx, ticket["id"], by, text)
        requester = ticket.get("submitted_by")
        if requester and str(requester).lower() != (by or "").lower():
            message = {
                "Resolved": "Ticket #%s is resolved: %s" % (ticket["id"], note),
                "Closed": "Ticket #%s is closed: %s" % (ticket["id"], note),
                sd.WAITING: "IT need your answer on ticket #%s." % ticket["id"],
            }.get(new)
            if message:
                self._notify([requester], message, "ticket")
        return True

    def change_priority(self, ticket: dict, priority: str, reason: str, by: str) -> bool:
        """IT re-prioritise with a reason, which is kept in the thread."""
        ticket = self._fresh(ticket)
        new = sd.normalise_priority(priority)
        reason = (reason or "").strip()
        if not reason:
            raise TicketError("Give a reason for the change - it is kept with the ticket.")
        old = sd.normalise_priority(ticket.get("priority"))
        if new == old:
            return False
        with atomic(self.db) as tx:
            self._apply(tx, ticket["id"], {"priority": new})
            self._event(tx, ticket["id"], by, "Priority changed from %s to %s by %s: %s" % (
                sd.PRIORITY_LABEL[old], sd.PRIORITY_LABEL[new], self._name(by), reason))
        return True

    # ------------------------------------------------------------- talking
    def reply(self, ticket: dict, author: str, text: str) -> dict:
        """
        A reply in the conversation, and what it does to the ticket (see
        service_desk.plan_after_reply). Returns the column changes made.
        """
        text = (text or "").strip()
        if not text:
            raise TicketError("Write something first.")
        ticket = self._fresh(ticket)
        now = datetime.now().replace(microsecond=0)
        changes, event = sd.plan_after_reply(ticket, author, now, self.calendar())
        with atomic(self.db) as tx:
            self._comment(tx, ticket["id"], author, text, now=now)
            if changes:
                self._apply(tx, ticket["id"], changes)
            if event:
                self._event(tx, ticket["id"], author, event)

        summary = sd.summary_of(ticket.get("description"))
        if sd.is_requester(ticket, author):
            owner = ticket.get("assigned_to")
            recipients = [owner] if owner else self.it_staff()
            self._notify(recipients, "%s replied on ticket #%s: %s" % (
                self._name(author), ticket["id"], summary), "ticket")
        else:
            self._notify([ticket.get("submitted_by")], "%s replied on ticket #%s." % (
                self._name(author), ticket["id"]), "ticket")
        return changes

    def add_note(self, ticket: dict, author: str, text: str) -> bool:
        """An internal note: IT only, never shown to the requester."""
        text = (text or "").strip()
        if not text:
            raise TicketError("Write the note first.")
        if not self._has("it_ticket_comments", "internal"):
            raise TicketError("This database cannot keep internal notes yet - "
                              "restart Slate once so it can update its tables.")
        with atomic(self.db) as tx:
            self._comment(tx, ticket["id"], author, text, internal=True, kind="note")
        return True

    # ------------------------------------------------- the requester's actions
    def confirm_fixed(self, ticket: dict, by: str) -> bool:
        """The requester says it is fixed: Resolved (or still open) -> Closed."""
        return self.set_status(ticket, "Closed", by, "Confirmed fixed by the requester.")

    def withdraw(self, ticket: dict, by: str, reason: str = "") -> bool:
        """The requester no longer needs help."""
        reason = (reason or "").strip() or "No longer needed."
        return self.set_status(ticket, "Closed", by, "Withdrawn by the requester - %s" % reason)

    def reopen(self, ticket: dict, by: str, reason: str = "") -> bool:
        """Still broken: back to Open, the old resolution cleared."""
        ticket = self._fresh(ticket)
        changes = sd.plan_status_change(ticket, "Open", datetime.now().replace(microsecond=0),
                                        self.calendar())
        if changes is None:
            return False
        text = "Reopened by %s%s" % (self._name(by), (": %s" % reason.strip()) if reason and reason.strip() else ".")
        with atomic(self.db) as tx:
            self._apply(tx, ticket["id"], changes)
            self._event(tx, ticket["id"], by, text)
        owner = ticket.get("assigned_to")
        self._notify([owner] if owner else self.it_staff(),
                     "Ticket #%s was reopened by %s." % (ticket["id"], self._name(by)), "ticket")
        return True

    # ------------------------------------------------------------- people
    def it_staff(self, exclude: str = None) -> List[str]:
        """Usernames of everybody who works the queue (the manage_it ability)."""
        try:
            rows = self.db.execute_query("SELECT username, roles FROM ut_users", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.warning("IT staff could not be read: %s", exc)
            return []
        from slate.core.domain.access import can
        from slate.core.domain import people
        from slate.core.domain.user_manager import UserManager
        try:
            listed = {p.username.lower() for p in people.people_for_picker(self.db)}
        except Exception:
            listed = None
        out = []
        for row in rows:
            row = dict(row)
            username = str(row.get("username") or "").strip()
            if not username or (exclude and username.lower() == exclude.lower()):
                continue
            if listed is not None and username.lower() not in listed:
                continue          # service accounts, leavers, deactivated
            if can(UserManager._parse_roles(row.get("roles")), "manage_it"):
                out.append(username)
        return sorted(out, key=str.lower)

    # ------------------------------------------------------------- helpers
    def _fresh(self, ticket: dict) -> dict:
        """The ticket as the database has it now (another IT person may have moved it)."""
        ticket_id = ticket.get("id") if isinstance(ticket, dict) else ticket
        current = self.get(ticket_id)
        if current is None:
            raise TicketError("Ticket #%s is no longer there - it may have been removed." % ticket_id)
        return current

    def _apply(self, tx, ticket_id, changes: dict):
        changes = {k: v for k, v in changes.items() if k in _WRITABLE}
        if not changes:
            return
        names = sorted(changes)
        values = [self._value(changes[n]) for n in names]
        tx.write("UPDATE it_tickets SET %s WHERE id = %%s"
                 % ", ".join("%s = %%s" % n for n in names),
                 tuple(values) + (ticket_id,), expect_rows=True)

    def _value(self, value):
        if isinstance(value, bool):
            return value if self._postgres() else int(value)
        return value

    def _comment(self, tx, ticket_id, author, text, internal=False, kind="reply", now=None):
        now = (now or datetime.now()).replace(microsecond=0)
        columns = ["ticket_id", "author", "comment_text", "timestamp"]
        values = [ticket_id, author, text, now.strftime("%Y-%m-%d %H:%M:%S")]
        if self._has("it_ticket_comments", "internal"):
            columns.append("internal")
            values.append(self._value(bool(internal)))
        if self._has("it_ticket_comments", "kind"):
            columns.append("kind")
            values.append(kind)
        tx.write("INSERT INTO it_ticket_comments (%s) VALUES (%s)"
                 % (", ".join(columns), ", ".join(["%s"] * len(values))), tuple(values))

    def _event(self, tx, ticket_id, author, text):
        self._comment(tx, ticket_id, author or "IT", text, kind="event")

    def _has(self, table, column) -> bool:
        cache = self.__dict__.setdefault("_has_cache", {})
        key = (table, column)
        if key not in cache:
            try:
                from .migrations.workplace_schema import _column_exists
                cache[key] = _column_exists(self.db, table, column)
            except Exception:
                cache[key] = False
        return cache[key]

    def _postgres(self) -> bool:
        from .migrations.registry import is_postgres
        return is_postgres(self.db)

    def _false(self):
        return False if self._postgres() else 0

    def _why(self, text: str) -> str:
        reason = ""
        try:
            reason = self.db.last_error() if hasattr(self.db, "last_error") else ""
        except Exception:
            reason = ""
        return "%s %s" % (text, reason) if reason else text

    @staticmethod
    def _name(username) -> str:
        if not username:
            return "IT"
        try:
            from slate.core.domain import people
            return people.display_name(username) or str(username)
        except Exception:
            return str(username)

    def _notify(self, recipients: Iterable, message: str, kind: str = "ticket") -> int:
        recipients = [r for r in (recipients or []) if r]
        if not recipients:
            return 0
        try:
            from slate.core.domain.notification_manager import NotificationManager
            return NotificationManager(self.db).notify(recipients, message, kind) or 0
        except DatabaseUnavailableError:
            return 0
        except Exception as exc:
            logger.warning("Ticket notification not sent (%s): %s", exc, message)
            return 0
