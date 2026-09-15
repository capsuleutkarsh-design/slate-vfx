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

    # ------------------------------------------------------------ the day
    #
    # Attendance used to carry its own copy of both of these. A studio that
    # moved its start time changed it in one place and not the other, and the
    # late count and the leave calendar then described different studios.

    # After this, an arrival is late. "HH:MM", on a 24 hour clock.
    "late_cutoff": "10:45",

    # The length of a standard working day. Anything past it is overtime.
    "standard_day_hours": 9.0,
}


# What this studio has changed, pushed in at start up. This module imports
# nothing but the standard library on purpose - the rules must be readable
# without a database, a config file or Qt - so the settings come to it rather
# than it reaching out for them. See core/infra/studio_policy.py.
_OVERRIDES = {}


def set_overrides(overrides=None) -> None:
    """Install the studio's own settings. Anything unnamed keeps its default."""
    global _OVERRIDES
    _OVERRIDES = {key: value for key, value
                  in dict(overrides or {}).items() if value is not None}


def overrides() -> dict:
    """What the studio has changed, for a screen that wants to show it."""
    return dict(_OVERRIDES)


LEAVE_TYPES = ("Casual", "Sick", "Earned", "Comp Off", "Project Rest", "Unpaid")

# Which types draw down the accrued balance. The rest are granted or unpaid.
ACCRUED_TYPES = ("Casual", "Sick", "Earned")


def policy(rules=None) -> dict:
    """
    The effective policy: the defaults, then this studio's settings, then
    anything the caller passes for this one calculation.
    """
    merged = dict(DEFAULT_POLICY)
    merged.update(_OVERRIDES)
    if rules:
        merged.update({k: v for k, v in rules.items() if v is not None})
    return merged


def late_cutoff(rules=None):
    """The hour and minute after which an arrival is late."""
    raw = str(policy(rules).get("late_cutoff") or "").strip()
    hour, _, minute = raw.partition(":")
    try:
        return int(hour), int(minute or 0)
    except ValueError:
        # A studio has typed something that is not a time. Refusing to guess
        # would leave attendance with no cutoff at all, so fall back to the
        # default rather than to no rule.
        return 10, 45


def standard_day_hours(rules=None) -> float:
    """How long a normal day is. Past this is overtime."""
    try:
        return float(policy(rules).get("standard_day_hours") or 0) or 9.0
    except (TypeError, ValueError):
        return 9.0


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

    Credited for each whole calendar month the person was present for, on the
    last day of that month. Two rules, both of which matter:

    A month the person did not work all of is not credited. Somebody who joined
    on the 15th has not earned January, and crediting them two days for sixteen
    days worked over-credits every new starter - which is the commonest way
    these balances go wrong.

    A month that has finished is credited. This is what the anniversary rule
    that used to be here got wrong at the year end: counting from the joining
    day meant somebody present from 1 January had completed only eleven
    anniversaries by 31 December, so closing the year lapsed a balance that was
    two days short. Everybody lost December, every year, and the loss was
    invisible because the accrued figure and the closing figure agreed with
    each other.

    Crediting at month end also means the whole studio accrues on the same day,
    which is the version a person can be told without a worked example.
    """
    rules = policy(rules)
    rate = float(rules["accrual_days_per_month"])
    if rate <= 0:
        return 0.0

    start = joined or date(as_of.year, 1, 1)
    if as_of < start:
        return 0.0

    # The first month they were present for the whole of.
    first_year, first_month = start.year, start.month
    if start.day != 1:
        first_month += 1
        if first_month > 12:
            first_month, first_year = 1, first_year + 1

    # The last month that has actually finished. A month part way through is
    # not credited until its last day.
    last_year, last_month = as_of.year, as_of.month
    if as_of.day < calendar.monthrange(as_of.year, as_of.month)[1]:
        last_month -= 1
        if last_month < 1:
            last_month, last_year = 12, last_year - 1

    months = (last_year - first_year) * 12 + (last_month - first_month) + 1
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
