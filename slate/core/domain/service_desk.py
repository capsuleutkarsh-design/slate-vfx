"""
The service desk's rules.

Priority is not a thing you pick. It is derived from two questions the person
raising a ticket can actually answer:

    impact    how much of the studio is affected
    urgency   can they carry on in the meantime

Letting people choose a priority directly produces a queue where everything is
Critical, which is the same as having no priority at all. The grid below is the
standard ITIL one.

Response and resolution are different promises, and only response is a promise
about attention: it is when a human has looked at it, not when it is fixed.

The clocks run on the studio's working hours (Settings > Studio Currency,
Rates & Hours; Mon-Sat 10:00-19:00 by default, studio holidays skipped). A
P1 is the exception and runs around the clock: a studio-wide outage at 23:00
is still an outage. The promise shown to the person raising a ticket is worded
from the same calendar the queue measures with, so the two cannot disagree -
they used to: the requester was told "3 business days" while the desk counted
72 wall-clock hours, nights and Sundays included.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ vocabulary

CATEGORIES = (
    "Workstation", "Software / Licence", "Network", "Storage",
    "Render Farm", "Peripherals", "Access / Account", "Other",
)

# Open to closed, in the order a ticket travels.
STATUSES = ("Open", "In Progress", "Waiting on You", "Resolved", "Closed")

OPEN_STATUSES = ("Open", "In Progress", "Waiting on You")
CLOSED_STATUSES = ("Resolved", "Closed")
WAITING = "Waiting on You"

PRIORITIES = ("P1", "P2", "P3", "P4")

PRIORITY_LABEL = {
    "P1": "P1 Critical",
    "P2": "P2 High",
    "P3": "P3 Medium",
    "P4": "P4 Low",
}

# The words the first version of the desk stored instead of P-codes (the
# column's default is still 'Medium' on older databases).
PRIORITY_WORDS = {
    "critical": "P1", "urgent": "P1", "highest": "P1",
    "high": "P2",
    "medium": "P3", "normal": "P3", "moderate": "P3",
    "low": "P4", "lowest": "P4", "minor": "P4",
}


# --------------------------------------------------------------------- impact
#
# Worded so an artist can answer without knowing anything about IT.

IMPACT = (
    ("High", "The whole studio, or a service everyone depends on"),
    ("Medium", "My department, or several people"),
    ("Low", "Only me"),
)

URGENCY = (
    ("High", "I cannot work at all - there is no way around it"),
    ("Medium", "I have a workaround, but it is costing me real time"),
    ("Low", "I can carry on. This can wait."),
)


# The ITIL grid. impact -> urgency -> priority.
MATRIX = {
    "High":   {"High": "P1", "Medium": "P2", "Low": "P2"},
    "Medium": {"High": "P2", "Medium": "P3", "Low": "P3"},
    "Low":    {"High": "P3", "Medium": "P3", "Low": "P4"},
}


def normalise_status(status: str) -> str:
    """
    Match a stored status to the canonical one.

    Never use .title() on these. "Waiting on You" title-cases to "Waiting On
    You", which matches nothing in STATUSES - so those tickets dropped out of
    the open list, out of the Open count, and out of their own status filter.
    They were still in the database, still waiting on somebody, and invisible
    on the one screen that exists to show them.

    Anything that is not a known status at all ("On Hold", typed in by some
    older tool) is read as Open. It used to be returned as it was, which put
    the ticket in no filter but "Everything" while its SLA clock kept running:
    still somebody's problem, and on nobody's list.
    """
    text = (status or "").strip()
    for known in STATUSES:
        if known.lower() == text.lower():
            return known
    return "Open"


def is_open(status: str) -> bool:
    """Whether a ticket in this state is still somebody's problem."""
    return normalise_status(status) in OPEN_STATUSES


def normalise_priority(priority) -> str:
    """
    P1-P4 from whatever is stored. Legacy words ('Medium', 'High') are mapped
    rather than shown as 'MEDIUM' and sorted after P4 while the SLA treated
    them as P3. Unknown values are P3, which is what the clock assumed anyway.
    """
    text = str(priority or "").strip()
    upper = text.upper()
    if upper in PRIORITIES:
        return upper
    return PRIORITY_WORDS.get(text.lower(), "P3")


def priority_for(impact: str, urgency: str) -> str:
    """The priority these two answers produce."""
    row = MATRIX.get(str(impact).strip().title(), MATRIX["Low"])
    return row.get(str(urgency).strip().title(), "P4")


def display_status(status, side: str = "requester") -> str:
    """
    The status as each side should read it. "Waiting on You" is worded for the
    person who raised the ticket; on the IT desk "you" is the wrong person, so
    it reads "Waiting on requester" there. The stored value never changes.
    """
    status = normalise_status(status)
    if status == WAITING:
        return "Waiting on requester" if side == "it" else "Waiting on you"
    return status


def summary_of(description) -> str:
    """
    The ticket's one-line heading: its first non-empty line.

    The first line was taken blindly, so a description starting with a blank
    line gave an empty heading - and an empty description raised IndexError,
    so that ticket could not be opened at all.
    """
    for line in str(description or "").splitlines():
        if line.strip():
            return line.strip()
    return "Untitled ticket"


def body_of(description) -> str:
    """Everything after the summary line."""
    lines = str(description or "").splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            return "\n".join(lines[index + 1:]).strip()
    return ""


# ------------------------------------------------------------------- calendar

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _parse_time(text, fallback: time) -> time:
    try:
        hour, minute = str(text).split(":")[:2]
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class BusinessCalendar:
    """
    When the studio is working, for counting SLA time.

    days: 0 = Monday ... 6 = Sunday. holidays: studio-wide holiday dates.
    """

    start: time = time(10, 0)
    end: time = time(19, 0)
    days: frozenset = frozenset({0, 1, 2, 3, 4, 5})
    holidays: frozenset = field(default_factory=frozenset)

    @property
    def day_hours(self) -> float:
        span = (datetime.combine(date.min, self.end)
                - datetime.combine(date.min, self.start)).total_seconds() / 3600.0
        return span if span > 0 else 24.0

    def is_working_day(self, day: date) -> bool:
        return day.weekday() in self.days and day not in self.holidays

    def _window(self, day: date):
        return datetime.combine(day, self.start), datetime.combine(day, self.end)

    def working_hours_between(self, start: datetime, end: datetime) -> float:
        """Working hours from start to end (0 when end is not after start)."""
        if start is None or end is None or end <= start:
            return 0.0
        if not self.days:
            return (end - start).total_seconds() / 3600.0
        total = 0.0
        day = start.date()
        last = end.date()
        while day <= last:
            if self.is_working_day(day):
                opens, closes = self._window(day)
                lo, hi = max(opens, start), min(closes, end)
                if hi > lo:
                    total += (hi - lo).total_seconds()
            day += timedelta(days=1)
        return total / 3600.0

    def add_working_hours(self, start: datetime, hours: float) -> datetime:
        """The moment `hours` working hours after start."""
        if start is None:
            return None
        if not self.days:
            return start + timedelta(hours=hours)
        remaining = max(0.0, float(hours)) * 3600.0
        cursor = start
        for _ in range(3700):                      # ten years of days, at most
            day = cursor.date()
            if self.is_working_day(day):
                opens, closes = self._window(day)
                if cursor < opens:
                    cursor = opens
                if cursor < closes:
                    available = (closes - cursor).total_seconds()
                    if remaining <= available:
                        return cursor + timedelta(seconds=remaining)
                    remaining -= available
            cursor = datetime.combine(day + timedelta(days=1), time(0, 0))
        return cursor

    # The clock a ticket of a given priority runs on.
    def clock_hours(self, start, end, around_the_clock: bool) -> float:
        if start is None or end is None:
            return 0.0
        if around_the_clock:
            return max(0.0, (end - start).total_seconds() / 3600.0)
        return self.working_hours_between(start, end)

    def add_clock(self, start, hours: float, around_the_clock: bool):
        if start is None:
            return None
        if around_the_clock:
            return start + timedelta(hours=hours)
        return self.add_working_hours(start, hours)

    def describe(self) -> str:
        """'Mon-Sat, 10:00-19:00' - the hours in words."""
        days = sorted(self.days)
        if not days:
            return "every day"
        if days == list(range(days[0], days[-1] + 1)) and len(days) > 2:
            span = "%s–%s" % (DAY_NAMES[days[0]], DAY_NAMES[days[-1]])
        else:
            span = ", ".join(DAY_NAMES[d] for d in days)
        return "%s, %s–%s" % (span, self.start.strftime("%H:%M"), self.end.strftime("%H:%M"))


DEFAULT_CALENDAR = BusinessCalendar()


def calendar_from(working_hours: Optional[dict], holidays: Iterable = ()) -> BusinessCalendar:
    """A calendar from the studio_settings 'working_hours' value and holiday dates."""
    hours = working_hours or {}
    start = _parse_time(hours.get("start"), DEFAULT_CALENDAR.start)
    end = _parse_time(hours.get("end"), DEFAULT_CALENDAR.end)
    try:
        days = frozenset(int(d) for d in (hours.get("days") or DEFAULT_CALENDAR.days)
                         if 0 <= int(d) <= 6)
    except (TypeError, ValueError):
        days = DEFAULT_CALENDAR.days
    if end <= start:
        start, end = DEFAULT_CALENDAR.start, DEFAULT_CALENDAR.end
    clean = set()
    for value in holidays or ():
        if isinstance(value, datetime):
            clean.add(value.date())
        elif isinstance(value, date):
            clean.add(value)
        else:
            try:
                clean.add(datetime.fromisoformat(str(value)[:10]).date())
            except ValueError:
                continue
    return BusinessCalendar(start, end, days or DEFAULT_CALENDAR.days, frozenset(clean))


def studio_calendar(db=None) -> BusinessCalendar:
    """
    The studio's working hours and studio-wide holidays, from the database.

    A holiday that belongs to one office only does not stop the desk: IT
    answer the whole studio. An unreachable database still raises, so the
    screen can say so; anything else falls back to the default hours.
    """
    try:
        from slate.core.infra.db_results import DatabaseUnavailableError
    except ImportError:                                   # pragma: no cover
        DatabaseUnavailableError = ConnectionError
    try:
        from slate.core.infra.studio_settings import get_setting
        hours = get_setting("working_hours", db=db)
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning("Working hours not read, using the defaults: %s", exc)
        hours = None
    holidays = []
    try:
        if db is None:
            from slate.core.infra.database_manager import database_manager as db
        rows = db.execute_query(
            "SELECT holiday_date FROM holiday_calendar "
            "WHERE location IS NULL OR location = '' OR LOWER(location) = 'all'",
            fetch="all") or []
        holidays = [dict(r).get("holiday_date") for r in rows]
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning("Studio holidays not read for the SLA clock: %s", exc)
    return calendar_from(hours, holidays)


# ------------------------------------------------------------------------ SLA
#
# Response is attention; resolution is a fix. P1 runs around the clock; the
# rest count working hours. Resolution for P3/P4 is promised in working days,
# so it follows the studio's day length rather than a fixed 24 hours.

SLA_RESPONSE_HOURS = {"P1": 0.25, "P2": 0.5, "P3": 2.0, "P4": 4.0}
SLA_RESOLUTION = {"P1": (4, "hours"), "P2": (8, "hours"), "P3": (3, "days"), "P4": (5, "days")}
# The same budgets in hours on the default calendar (9-hour days).
SLA_RESOLUTION_HOURS = {
    p: (n * DEFAULT_CALENDAR.day_hours if unit == "days" else float(n))
    for p, (n, unit) in SLA_RESOLUTION.items()
}

# How much of a clock may be left before a ticket counts as at risk: the last
# quarter of a response window, the last fifth of a fix window. It used to be
# half the RESPONSE window for both, so a P3 fix was "at risk" only in its
# final hour of 72.
AT_RISK_FRACTION = {"response": 0.25, "resolution": 0.2}


def around_the_clock(priority) -> bool:
    """P1 is measured day and night; everything else in working hours."""
    return normalise_priority(priority) == "P1"


def budget_hours(priority, against: str, calendar: BusinessCalendar = None) -> float:
    calendar = calendar or DEFAULT_CALENDAR
    priority = normalise_priority(priority)
    if against == "response":
        return SLA_RESPONSE_HOURS.get(priority, 4.0)
    amount, unit = SLA_RESOLUTION.get(priority, (5, "days"))
    return amount * calendar.day_hours if unit == "days" else float(amount)


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0))
    try:
        return datetime.fromisoformat(str(value).strip().replace("T", " ")[:19])
    except Exception:
        return None


def response_due(created_at, priority: str, calendar: BusinessCalendar = None):
    created = _as_datetime(created_at)
    if created is None:
        return None
    calendar = calendar or DEFAULT_CALENDAR
    return calendar.add_clock(created, budget_hours(priority, "response", calendar),
                              around_the_clock(priority))


def resolution_due(created_at, priority: str, calendar: BusinessCalendar = None):
    created = _as_datetime(created_at)
    if created is None:
        return None
    calendar = calendar or DEFAULT_CALENDAR
    return calendar.add_clock(created, budget_hours(priority, "resolution", calendar),
                              around_the_clock(priority))


def waiting_hours(ticket: dict, now: datetime = None, calendar: BusinessCalendar = None) -> float:
    """
    How long this ticket has sat waiting on the person who raised it, in the
    units of its own clock (working hours, or plain hours for a P1).

    Time IT spend waiting for an answer is not time IT are failing to fix
    something. The clock used to run straight through it, so a ticket parked for
    a week on "Waiting on You" breached while the queue was doing exactly what
    it should.
    """
    now = now or datetime.now()
    calendar = calendar or DEFAULT_CALENDAR
    banked = 0.0
    try:
        banked = float(ticket.get("waiting_seconds") or 0) / 3600.0
    except (TypeError, ValueError):
        banked = 0.0

    since = _as_datetime(ticket.get("waiting_since"))
    if since is not None and normalise_status(ticket.get("status")) == WAITING:
        banked += calendar.clock_hours(since, now, around_the_clock(ticket.get("priority")))
    return banked


def sla_state(ticket: dict, now: datetime = None, calendar: BusinessCalendar = None) -> dict:
    """
    Where this ticket stands against what was promised.

    Returns a state of 'met', 'at risk', 'breached', 'paused' (waiting on the
    requester - the clock is stopped) or 'closed', the hours left on the
    ticket's clock (negative once it has gone past), which promise is being
    measured, and when it falls due.

    Time the ticket spent waiting on the person who raised it is added back, so
    the measure is of the time IT actually had it.
    """
    now = now or datetime.now()
    calendar = calendar or DEFAULT_CALENDAR
    status = normalise_status(ticket.get("status"))
    priority = normalise_priority(ticket.get("priority"))
    clock = around_the_clock(priority)

    if status in CLOSED_STATUSES:
        return {"state": "closed", "hours_left": None, "against": "", "due": None,
                "around_the_clock": clock}

    against = "response" if _as_datetime(ticket.get("first_response_at")) is None else "resolution"
    created = _as_datetime(ticket.get("created_at"))
    if created is None:
        return {"state": "paused" if status == WAITING else "met", "hours_left": None,
                "against": against, "due": None, "around_the_clock": clock}

    budget = budget_hours(priority, against, calendar)
    waited = waiting_hours(ticket, now, calendar)
    elapsed = calendar.clock_hours(created, now, clock) - waited
    hours_left = budget - elapsed
    due = calendar.add_clock(created, budget + waited, clock)

    if status == WAITING:
        state = "paused"
    elif hours_left < 0:
        state = "breached"
    elif hours_left < budget * AT_RISK_FRACTION.get(against, 0.2):
        state = "at risk"
    else:
        state = "met"
    return {"state": state, "hours_left": hours_left, "against": against, "due": due,
            "around_the_clock": clock}


# Queue order: what is about to go wrong first.
SLA_ORDER = {"breached": 0, "at risk": 1, "met": 2, "paused": 3, "closed": 4}


def format_duration(hours: float, around_the_clock: bool = True,
                    calendar: BusinessCalendar = None) -> str:
    """'45 min', '3 h', '2 d 4 h'. Days are working days on a working-hours clock."""
    calendar = calendar or DEFAULT_CALENDAR
    hours = abs(float(hours or 0))
    if hours < 1:
        return "%d min" % max(1, round(hours * 60))
    day = 24.0 if around_the_clock else calendar.day_hours
    if hours < day:
        return "%d h" % round(hours)
    days = int(hours // day)
    rest = round(hours - days * day)
    if rest >= day:
        days, rest = days + 1, 0
    return "%d d %d h" % (days, rest) if rest else "%d d" % days


def sla_text(state: dict, calendar: BusinessCalendar = None) -> str:
    """
    The SLA cell, in words: '3 h left to fix', '29 d overdue - no reply yet',
    'Paused - waiting on requester'. It was '699.5h over (response)'.
    """
    name = (state or {}).get("state")
    if name == "closed" or not state:
        return "-"
    if name == "paused":
        return "Paused - waiting on requester"
    left = state.get("hours_left")
    if left is None:
        return "-"
    span = format_duration(left, state.get("around_the_clock", True), calendar)
    if state.get("against") == "response":
        return "%s left to reply" % span if left >= 0 else "%s overdue - no reply yet" % span
    return "%s left to fix" % span if left >= 0 else "%s overdue - not fixed yet" % span


def sla_tone(state: str) -> str:
    """Gate token for an SLA state."""
    return {
        "breached": "BAD",
        "at risk": "WARN",
        "met": "OK",
        "paused": "IDLE",
        "closed": "IDLE",
    }.get(state, "TEXT_DIM")


def status_tone(status: str) -> str:
    s = normalise_status(status).lower()
    if s in ("resolved", "closed"):
        return "OK"
    if s == "waiting on you":
        return "WARN"
    if s in ("open", "in progress"):
        return "INFO"
    return "TEXT_DIM"


def priority_tone(priority: str) -> str:
    return {"P1": "BAD", "P2": "WARN", "P3": "INFO", "P4": "TEXT_DIM"}.get(
        normalise_priority(priority), "TEXT_DIM")


def priority_rank(priority: str) -> int:
    """Sort key - P1 first. Legacy words rank with their P-code."""
    return PRIORITIES.index(normalise_priority(priority))


def _hours_phrase(hours: float, working: bool) -> str:
    unit = "working " if working else ""
    if hours < 1:
        return "%d %sminutes" % (int(round(hours * 60)), unit)
    return "%g %shour%s" % (hours, unit, "" if hours == 1 else "s")


def describe_promise(priority: str, calendar: BusinessCalendar = None) -> str:
    """
    What choosing this priority commits IT to, in words - worded from the same
    calendar the desk measures with.
    """
    calendar = calendar or DEFAULT_CALENDAR
    priority = normalise_priority(priority)
    label = PRIORITY_LABEL.get(priority, priority)
    response = SLA_RESPONSE_HOURS.get(priority, 4.0)
    amount, unit = SLA_RESOLUTION.get(priority, (5, "days"))
    if around_the_clock(priority):
        fix = _hours_phrase(amount * (24 if unit == "days" else 1), False)
        return ("%s - IT aim to respond within %s and to fix it within %s, day or night."
                % (label, _hours_phrase(response, False), fix))
    if unit == "days":
        fix = "%d working day%s" % (amount, "" if amount == 1 else "s")
    else:
        fix = _hours_phrase(amount, True)
    return ("%s - IT aim to respond within %s and to fix it within %s (working hours: %s)."
            % (label, _hours_phrase(response, True), fix, calendar.describe()))


# ------------------------------------------------------------- transitions
#
# Every change a ticket goes through, as the columns it changes. The screens
# used to write their own UPDATEs, each with its own idea of what a status
# change meant - setting Waiting twice wiped the banked wait, reopening kept
# the old resolved_at, and a requester's reply left the ticket "Waiting on
# You" with the clock stopped. One function now, used by both sides.

def outcome(ticket: dict, resolved_at: datetime, calendar: BusinessCalendar = None) -> dict:
    """
    Whether the promises were kept, stored on the ticket when it is resolved,
    so a report can say so afterwards (closed rows used to show only '-').
    """
    calendar = calendar or DEFAULT_CALENDAR
    priority = normalise_priority(ticket.get("priority"))
    clock = around_the_clock(priority)
    created = _as_datetime(ticket.get("created_at"))
    if created is None:
        return {"response_met": None, "resolution_met": None, "resolution_hours": None}
    responded = _as_datetime(ticket.get("first_response_at")) or resolved_at
    waited = waiting_hours(ticket, resolved_at, calendar)
    to_respond = calendar.clock_hours(created, responded, clock)
    to_fix = max(0.0, calendar.clock_hours(created, resolved_at, clock) - waited)
    return {
        "response_met": to_respond <= budget_hours(priority, "response", calendar),
        "resolution_met": to_fix <= budget_hours(priority, "resolution", calendar),
        "resolution_hours": round(to_fix, 2),
    }


def plan_status_change(ticket: dict, new_status: str, now: datetime = None,
                       calendar: BusinessCalendar = None) -> Optional[dict]:
    """
    The column changes for moving this ticket to new_status, or None when it is
    already there (setting Waiting on a ticket already waiting used to reset
    waiting_since to now and lose the time banked so far).
    """
    now = now or datetime.now()
    calendar = calendar or DEFAULT_CALENDAR
    current = normalise_status(ticket.get("status"))
    new = normalise_status(new_status)
    if new == current:
        return None
    changes = {"status": new}
    if current == WAITING:
        # Un-parking banks however long it was parked.
        changes["waiting_seconds"] = int(round(waiting_hours(ticket, now, calendar) * 3600))
        changes["waiting_since"] = None
    if new == WAITING:
        changes["waiting_since"] = now
    if new in CLOSED_STATUSES:
        if current not in CLOSED_STATUSES:
            changes["resolved_at"] = now
            merged = dict(ticket)
            merged.update(changes)
            merged["status"] = current          # measured up to now
            changes.update(outcome(merged, now, calendar))
    else:
        # Back to an open state: the old resolution no longer stands, so a
        # report of time-to-fix does not read the first, failed attempt.
        changes["resolved_at"] = None
        changes["response_met"] = None
        changes["resolution_met"] = None
        changes["resolution_hours"] = None
    return changes


def is_requester(ticket: dict, username: str) -> bool:
    return (str(ticket.get("submitted_by") or "").strip().lower()
            == str(username or "").strip().lower() != "")


def plan_after_reply(ticket: dict, author: str, now: datetime = None,
                     calendar: BusinessCalendar = None):
    """
    What a reply does to the ticket: (column changes, event line or "").

    From the person who raised it: a ticket waiting on them goes back to IT
    (In Progress, the wait banked); a resolved or closed one is reopened - a
    "still broken" after closure used to vanish from the queue.

    From IT: the first reply is the response; on an unassigned Open ticket it
    also picks the ticket up for whoever replied.
    """
    now = now or datetime.now()
    current = normalise_status(ticket.get("status"))
    if is_requester(ticket, author):
        if current == WAITING:
            return plan_status_change(ticket, "In Progress", now, calendar) or {}, ""
        if current in CLOSED_STATUSES:
            return (plan_status_change(ticket, "Open", now, calendar) or {},
                    "Reopened by the requester's reply.")
        return {}, ""
    changes = {}
    if _as_datetime(ticket.get("first_response_at")) is None:
        changes["first_response_at"] = now
    if current == "Open" and not str(ticket.get("assigned_to") or "").strip():
        changes["assigned_to"] = author
        changes["status"] = "In Progress"
    return changes, ""


# ------------------------------------------------------------------ reports

def sla_report(tickets: Iterable[dict], year: int, month: int) -> list:
    """
    One line per priority for tickets resolved in this month: how many, how
    many kept their promises, and the median working time to fix.
    """
    rows = {}
    for t in tickets:
        resolved = _as_datetime(t.get("resolved_at"))
        if resolved is None or (resolved.year, resolved.month) != (year, month):
            continue
        p = normalise_priority(t.get("priority"))
        line = rows.setdefault(p, {"priority": p, "count": 0, "response_met": 0,
                                   "resolution_met": 0, "hours": []})
        line["count"] += 1
        if _truthy(t.get("response_met")):
            line["response_met"] += 1
        if _truthy(t.get("resolution_met")):
            line["resolution_met"] += 1
        try:
            if t.get("resolution_hours") is not None:
                line["hours"].append(float(t.get("resolution_hours")))
        except (TypeError, ValueError):
            pass
    out = []
    for p in PRIORITIES:
        line = rows.get(p)
        if not line:
            continue
        count = line["count"]
        out.append({
            "priority": p,
            "count": count,
            "response_pct": round(100.0 * line["response_met"] / count) if count else None,
            "resolution_pct": round(100.0 * line["resolution_met"] / count) if count else None,
            "median_hours": round(median(line["hours"]), 1) if line["hours"] else None,
        })
    return out


def _truthy(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes")
    return bool(value)
