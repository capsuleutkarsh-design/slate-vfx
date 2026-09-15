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
"""

from __future__ import annotations

from datetime import datetime, timedelta


# ------------------------------------------------------------------ vocabulary

CATEGORIES = (
    "Workstation", "Software / Licence", "Network", "Storage",
    "Render Farm", "Peripherals", "Access / Account", "Other",
)

# Open to closed, in the order a ticket travels.
STATUSES = ("Open", "In Progress", "Waiting on You", "Resolved", "Closed")

OPEN_STATUSES = ("Open", "In Progress", "Waiting on You")

PRIORITIES = ("P1", "P2", "P3", "P4")

PRIORITY_LABEL = {
    "P1": "P1 Critical",
    "P2": "P2 High",
    "P3": "P3 Medium",
    "P4": "P4 Low",
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

    This is the same bug, and the same fix, as normalise_status in
    leave_policy. A status is a value from a known set, not a sentence to be
    capitalised.
    """
    text = (status or "").strip()
    for known in STATUSES:
        if known.lower() == text.lower():
            return known
    return text


def is_open(status: str) -> bool:
    """Whether a ticket in this state is still somebody's problem."""
    return normalise_status(status) in OPEN_STATUSES


def priority_for(impact: str, urgency: str) -> str:
    """The priority these two answers produce."""
    row = MATRIX.get(str(impact).strip().title(), MATRIX["Low"])
    return row.get(str(urgency).strip().title(), "P4")


# ------------------------------------------------------------------------ SLA
#
# Hours. Response is attention; resolution is a fix.

SLA_RESPONSE_HOURS = {"P1": 0.25, "P2": 0.5, "P3": 2.0, "P4": 4.0}
SLA_RESOLUTION_HOURS = {"P1": 4.0, "P2": 8.0, "P3": 72.0, "P4": 120.0}


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19])
    except Exception:
        return None


def response_due(created_at, priority: str):
    created = _as_datetime(created_at)
    if created is None:
        return None
    return created + timedelta(hours=SLA_RESPONSE_HOURS.get(priority, 4.0))


def resolution_due(created_at, priority: str):
    created = _as_datetime(created_at)
    if created is None:
        return None
    return created + timedelta(hours=SLA_RESOLUTION_HOURS.get(priority, 120.0))


def waiting_hours(ticket: dict, now: datetime = None) -> float:
    """
    How long this ticket has sat waiting on the person who raised it.

    Time IT spend waiting for an answer is not time IT are failing to fix
    something. The clock used to run straight through it, so a ticket parked for
    a week on "Waiting on You" breached while the queue was doing exactly what
    it should.
    """
    now = now or datetime.now()
    banked = 0.0
    try:
        banked = float(ticket.get("waiting_seconds") or 0) / 3600.0
    except (TypeError, ValueError):
        banked = 0.0

    since = _as_datetime(ticket.get("waiting_since"))
    if since is not None and normalise_status(ticket.get("status")) == "Waiting on You":
        banked += max(0.0, (now - since).total_seconds() / 3600.0)
    return banked


def sla_state(ticket: dict, now: datetime = None) -> dict:
    """
    Where this ticket stands against what was promised.

    Returns a state of 'met', 'at risk', 'breached' or 'closed', and the hours
    remaining - negative once it has gone past. A queue sorted on this shows
    what is about to go wrong rather than merely what is old.

    Time the ticket spent waiting on the person who raised it is added back, so
    the measure is of the time IT actually had it.
    """
    now = now or datetime.now()
    status = normalise_status(ticket.get("status")) or "Open"
    priority = (ticket.get("priority") or "P3").upper()
    if priority not in SLA_RESPONSE_HOURS:
        priority = "P3"

    if status in ("Resolved", "Closed"):
        return {"state": "closed", "hours_left": None, "against": ""}

    responded = _as_datetime(ticket.get("first_response_at"))
    if responded is None:
        due = response_due(ticket.get("created_at"), priority)
        against = "response"
    else:
        due = resolution_due(ticket.get("created_at"), priority)
        against = "resolution"

    if due is None:
        return {"state": "met", "hours_left": None, "against": against}

    hours_left = (due - now).total_seconds() / 3600.0 + waiting_hours(ticket, now)
    if hours_left < 0:
        state = "breached"
    elif hours_left < max(0.25, SLA_RESPONSE_HOURS.get(priority, 1) * 0.5):
        state = "at risk"
    else:
        state = "met"

    return {"state": state, "hours_left": hours_left, "against": against}


def sla_tone(state: str) -> str:
    """Gate token for an SLA state."""
    return {
        "breached": "BAD",
        "at risk": "WARN",
        "met": "OK",
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
        str(priority).upper(), "TEXT_DIM")


def priority_rank(priority: str) -> int:
    """Sort key - P1 first."""
    try:
        return PRIORITIES.index(str(priority).strip().upper())
    except ValueError:
        return len(PRIORITIES)


def describe_promise(priority: str) -> str:
    """What choosing this priority commits IT to, in words."""
    response = SLA_RESPONSE_HOURS.get(priority, 4.0)
    if response < 1:
        window = "%d minutes" % int(response * 60)
    else:
        window = "%g hour%s" % (response, "" if response == 1 else "s")
    resolution = SLA_RESOLUTION_HOURS.get(priority, 120.0)
    fix = "%g hours" % resolution if resolution < 24 else "%g business days" % (resolution / 24)
    return "%s - IT aim to respond within %s, and to fix it within %s." % (
        PRIORITY_LABEL.get(priority, priority), window, fix)
