"""
Are we licensed, and are we paying for seats nobody uses?

Two different questions that a seat count alone cannot answer, and both of them
cost money in opposite directions.

    over-subscribed   more people working than seats bought. This is the one
                      that ends in an audit letter.
    under-used        seats bought that never all get used at once. Invisible
                      until somebody looks at the peak rather than the total,
                      and it is where a renewal gets cut.

The honest measure of a floating licence is peak concurrent use, not how many
people have it installed. A studio with 20 artists and 8 Nuke seats is fine if
never more than 8 are comping at the same moment - and the only way to know is
to have written the number down repeatedly. That is what licence_readings is:
a log of what the licence server said, so the renewal conversation is held with
evidence instead of instinct.
"""

from __future__ import annotations

from datetime import date, datetime


# A renewal that lands inside this window needs a decision now - purchasing and
# vendor paperwork do not turn round in a week.
RENEWAL_SOON_DAYS = 45

# Peak use this far below what was bought is the studio paying for air. Set
# generously: headroom is deliberate, and a licence used at 80% of its seats is
# correctly sized, not wasteful.
UNDER_USED_RATIO = 0.6

OVER = "Over-subscribed"
EXPIRED = "Expired"
SOON = "Renews soon"
UNDER = "Under-used"
OK = "Healthy"
UNKNOWN = "No readings"

# Worst first. A screen that sorts by this shows the audit risk above the
# housekeeping, which is the order somebody in IT actually works in.
SEVERITY = {OVER: 0, EXPIRED: 1, SOON: 2, UNDER: 3, UNKNOWN: 4, OK: 5}

TONE = {OVER: "BAD", EXPIRED: "BAD", SOON: "WARN",
        UNDER: "INFO", UNKNOWN: "IDLE", OK: "OK"}


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def days_until(expiry, today: date = None):
    """Days left on a licence. None when nobody recorded an expiry."""
    expiry = as_date(expiry)
    if not expiry:
        return None
    return (expiry - (today or date.today())).days


def utilisation(peak, seats) -> float:
    """Peak concurrent use as a fraction of what was bought."""
    seats = int(seats or 0)
    if seats <= 0:
        return 0.0
    return float(peak or 0) / float(seats)


def state(seats, peak, expiry, today: date = None) -> str:
    """
    One word for where this licence stands.

    Order matters: being short of seats outranks an expiry date, because an
    expiry is a diary entry and a shortfall is people unable to work.
    """
    seats = int(seats or 0)
    left = days_until(expiry, today)

    if peak is not None and seats and int(peak) > seats:
        return OVER
    if left is not None and left < 0:
        return EXPIRED
    if left is not None and left <= RENEWAL_SOON_DAYS:
        return SOON
    if peak is None:
        return UNKNOWN
    if seats and utilisation(peak, seats) < UNDER_USED_RATIO:
        return UNDER
    return OK


def tone(state_name: str) -> str:
    return TONE.get(state_name, "IDLE")


def describe(seats, peak, expiry, today: date = None) -> str:
    """
    The finding, in a sentence somebody can act on.

    Each one says what was observed and what it means for the renewal - a
    status word on its own tells IT nothing they can take to purchasing.
    """
    seats = int(seats or 0)
    left = days_until(expiry, today)
    name = state(seats, peak, expiry, today)

    if name == OVER:
        return ("Peak use reached %d against %d seats. That is %d more than we "
                "own - either seats are shared or we are out of compliance."
                % (int(peak), seats, int(peak) - seats))
    if name == EXPIRED:
        return "Expired %d day(s) ago. Anyone relying on it is already stuck." % abs(left)
    if name == SOON:
        # Only raise the idea of dropping seats when there are some to drop -
        # "0 could be dropped" reads as a bug, and a fully used licence wants
        # the opposite advice.
        spare = 0 if peak is None else max(0, seats - int(peak))
        if peak is None:
            detail = " Nothing has been measured, so there is no case for changing the seat count."
        elif spare:
            detail = (" Peak use has been %d of %d seats - %d could come off the renewal."
                      % (int(peak), seats, spare))
        else:
            detail = (" Every one of the %d seats has been in use at once, so renew "
                      "at least at this size." % seats)
        return "Renews in %d day(s) - decide before purchasing needs lead time.%s" % (left, detail)
    if name == UNDER:
        return ("Never more than %d of %d seats in use at once. %d seat(s) are "
                "being paid for and not worked with."
                % (int(peak), seats, seats - int(peak)))
    if name == UNKNOWN:
        return ("No usage has been recorded, so there is nothing to renew "
                "against except the invoice.")
    return "Peak use %d of %d seats. Sized about right." % (int(peak), seats)
