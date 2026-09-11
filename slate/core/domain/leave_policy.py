"""
The rules a studio runs its leave by.

Nothing in here is hardcoded to one studio. The defaults are UT's own - a six
day week, two days accrued a month, no comp-off - but every value is settings
driven, because this ships to studios whose policies differ. The comp-off rules
in particular vary wildly: some studios grant a day for working a Sunday, some
grant half a day for a twelve hour shift and a full day past eighteen, and some
give none at all.

Three things live here and nowhere else:

    the calendar    which days are working days at all
    the count       how many days a request actually costs, sandwich included
    the accrual     how much leave a person has earned by a given date
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta


# --------------------------------------------------------------------- defaults
#
# Slate's own policy. A studio changes these in Settings; nothing below reads
# a literal.

DEFAULT_POLICY = {
    # 0 = Monday ... 6 = Sunday. UT works Monday to Saturday.
    "weekly_offs": [6],

    # Leave earned per month, credited on the last day of the month worked.
    "accrual_days_per_month": 2.0,

    # Carried into the new year; anything above this lapses.
    "carry_forward_cap": 12.0,

    # A lone working day between two non-working days, taken as leave, also
    # costs the days either side of it.
    "sandwich_rule": True,

    # Comp-off. Off by default because UT does not operate it.
    "comp_off_enabled": False,
    "comp_off_for_weekly_off": 1.0,      # days earned for working a Sunday
    "comp_off_for_holiday": 1.0,         # days earned for working a holiday
    "comp_off_hours_half": 12.0,         # this many hours in a day earns a half day
    "comp_off_hours_full": 18.0,         # this many earns a whole one
    "comp_off_expiry_days": 90,          # unused comp-off lapses after this

    # Granted by HR at the end of a project when nothing urgent is queued.
    # Not accrued, not capped - it is a decision, not an entitlement.
    "project_rest_enabled": True,
}


LEAVE_TYPES = ("Casual", "Sick", "Earned", "Comp Off", "Project Rest", "Unpaid")

# Which types draw down the accrued balance. The rest are granted or unpaid.
ACCRUED_TYPES = ("Casual", "Sick", "Earned")


def policy(overrides=None) -> dict:
    """The effective policy: defaults, with any studio settings on top."""
    merged = dict(DEFAULT_POLICY)
    if overrides:
        merged.update({k: v for k, v in overrides.items() if v is not None})
    return merged


# ------------------------------------------------------------------- calendar

def is_weekly_off(day: date, rules=None) -> bool:
    return day.weekday() in set(policy(rules)["weekly_offs"])


def is_working_day(day: date, holidays=None, rules=None) -> bool:
    """A day someone is expected in: not a weekly off, not a public holiday."""
    if is_weekly_off(day, rules):
        return False
    return day not in set(holidays or ())


def working_days_between(start: date, end: date, holidays=None, rules=None) -> list:
    days, cursor = [], start
    while cursor <= end:
        if is_working_day(cursor, holidays, rules):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


# ---------------------------------------------------------------- the sandwich

def sandwich_days(start: date, end: date, holidays=None, rules=None) -> list:
    """
    The non-working days a request swallows either side of itself.

    The rule as this studio states it: a holiday, then a working day, then
    another holiday - and the artist does not come in on that working day. All
    three are deducted, not one.

    It only bites when the request is bracketed on *both* sides. Taking a Friday
    off when Thursday was worked does not cost you the weekend.
    """
    rules = policy(rules)
    if not rules["sandwich_rule"]:
        return []

    holidays = set(holidays or ())

    before = start - timedelta(days=1)
    after = end + timedelta(days=1)
    if is_working_day(before, holidays, rules) or is_working_day(after, holidays, rules):
        return []

    absorbed = []

    cursor = before
    while not is_working_day(cursor, holidays, rules):
        absorbed.append(cursor)
        cursor -= timedelta(days=1)

    cursor = after
    while not is_working_day(cursor, holidays, rules):
        absorbed.append(cursor)
        cursor += timedelta(days=1)

    return sorted(absorbed)


def days_charged(start: date, end: date, holidays=None, rules=None,
                 half_day: bool = False) -> dict:
    """
    What a request actually costs.

    Returns the working days inside it, whatever the sandwich rule adds, and the
    total - so the artist can be shown *why* three days were deducted for one
    day away rather than just the number.
    """
    working = working_days_between(start, end, holidays, rules)
    absorbed = sandwich_days(start, end, holidays, rules) if working else []

    total = float(len(working) + len(absorbed))
    if half_day and total:
        total -= 0.5

    return {
        "working_days": working,
        "sandwich_days": absorbed,
        "total": total,
    }


# -------------------------------------------------------------------- accrual

def accrued_by(as_of: date, joined: date = None, rules=None) -> float:
    """
    Leave earned up to a date.

    Credited for each completed month, so somebody who joined in September has
    not earned a full year. A flat annual entitlement over-credits every new
    starter, which is the most common way these balances go wrong.
    """
    rules = policy(rules)
    rate = float(rules["accrual_days_per_month"])
    if rate <= 0:
        return 0.0

    start = joined or date(as_of.year, 1, 1)
    if as_of < start:
        return 0.0

    months = (as_of.year - start.year) * 12 + (as_of.month - start.month)

    # A month is completed on the joining day's anniversary, not on the last
    # day of the calendar month. Counting calendar months instead credited
    # somebody who joined on the 15th a full month's leave on the 31st - two
    # days earned for sixteen days worked, every time a new starter joined
    # part way through a month.
    last_day = calendar.monthrange(as_of.year, as_of.month)[1]
    if as_of.day < min(start.day, last_day):
        months -= 1

    return max(0.0, months * rate)


def carry_forward(closing_balance: float, rules=None) -> dict:
    """What survives the year end, and what is lost."""
    cap = float(policy(rules)["carry_forward_cap"])
    carried = min(max(0.0, closing_balance), cap)
    return {"carried": carried, "lapsed": max(0.0, closing_balance - carried)}


# ------------------------------------------------------------------- comp-off

def comp_off_earned(day: date, hours_worked: float, holidays=None, rules=None) -> dict:
    """
    What working this particular day earns back.

    Three ways to earn it, and a studio may operate any combination:
      - worked a weekly off
      - worked a public holiday
      - worked a long enough day

    Returns the days earned and the reason, so the ledger can say why.
    """
    rules = policy(rules)
    if not rules["comp_off_enabled"]:
        return {"days": 0.0, "reason": ""}

    holidays = set(holidays or ())
    hours = float(hours_worked or 0)

    if day in holidays:
        return {"days": float(rules["comp_off_for_holiday"]),
                "reason": "Worked a public holiday"}

    if is_weekly_off(day, rules):
        return {"days": float(rules["comp_off_for_weekly_off"]),
                "reason": "Worked a weekly off"}

    full = float(rules["comp_off_hours_full"])
    half = float(rules["comp_off_hours_half"])
    if full > 0 and hours >= full:
        return {"days": 1.0, "reason": "Worked %.1f hours" % hours}
    if half > 0 and hours >= half:
        return {"days": 0.5, "reason": "Worked %.1f hours" % hours}

    return {"days": 0.0, "reason": ""}


def comp_off_expires(earned_on: date, rules=None) -> date:
    days = int(policy(rules)["comp_off_expiry_days"])
    return earned_on + timedelta(days=days) if days > 0 else None


# ------------------------------------------------------------------- approval
#
# Supervisor first, then HR. A request is only spent once both have said yes.

APPROVAL_STAGES = ("Supervisor", "HR")

STATUS_PENDING_SUPERVISOR = "Pending Supervisor"
STATUS_PENDING_HR = "Pending HR"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_CANCELLED = "Cancelled"

LEAVE_STATUSES = (
    STATUS_PENDING_SUPERVISOR, STATUS_PENDING_HR,
    STATUS_APPROVED, STATUS_REJECTED, STATUS_CANCELLED,
)


def normalise_status(status: str) -> str:
    """
    Match a stored status to the canonical one.

    Never use .title() on these. "Pending HR" title-cases to "Pending Hr",
    which then matches nothing - so a request sitting with HR silently stopped
    counting as pending and vanished from the balance.
    """
    text = (status or "").strip()
    for known in LEAVE_STATUSES:
        if known.lower() == text.lower():
            return known
    return text


def next_status(current: str, stage: str, approved: bool) -> str:
    """Where a request moves when somebody decides on it."""
    if not approved:
        return STATUS_REJECTED
    if stage == "Supervisor":
        return STATUS_PENDING_HR
    if stage == "HR":
        return STATUS_APPROVED
    return current


def awaiting(status: str) -> str:
    """Who is holding this up."""
    return {
        STATUS_PENDING_SUPERVISOR: "Supervisor",
        STATUS_PENDING_HR: "HR",
    }.get(status, "")
