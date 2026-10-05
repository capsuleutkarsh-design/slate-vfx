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

import math
from datetime import date
from decimal import Decimal
from typing import Optional


# A renewal that lands inside this window needs a decision now - purchasing and
# vendor paperwork do not turn round in a week. The studio can change it
# (studio setting "licence_renewal_days"); this is the default.
RENEWAL_SOON_DAYS = 45

# Peak use this far below what was bought is the studio paying for air. Set
# generously: headroom is deliberate, and a licence used at 80% of its seats is
# correctly sized, not wasteful.
UNDER_USED_RATIO = 0.6

# When suggesting a smaller renewal, keep this much above the peak.
HEADROOM = 0.2

OVER = "Over-subscribed"
EXPIRED = "Expired"
SOON = "Renews soon"
UNDER = "Under-used"
OK = "Healthy"
UNKNOWN = "No readings"
# Measured before, but not inside the peak window: not the same as never.
STALE = "Not measured lately"

# Worst first. A screen that sorts by this shows the audit risk above the
# housekeeping, which is the order somebody in IT actually works in.
SEVERITY = {OVER: 0, EXPIRED: 1, SOON: 2, UNDER: 3, STALE: 4, UNKNOWN: 4, OK: 5}

TONE = {OVER: "BAD", EXPIRED: "BAD", SOON: "WARN",
        UNDER: "INFO", STALE: "IDLE", UNKNOWN: "IDLE", OK: "OK"}


def _register_setting():
    """The renewal window as a studio-wide setting (Settings, studio cards)."""
    try:
        from slate.core.infra.studio_settings import register_key
    except Exception:                                   # pragma: no cover
        return

    def check(value):
        days = int(value)
        if not 1 <= days <= 365:
            raise ValueError("The renewal window is between 1 and 365 days.")
        return days

    register_key("licence_renewal_days", RENEWAL_SOON_DAYS, check)


_register_setting()


def renewal_window(db=None) -> int:
    """The studio's renewal window in days (default 45)."""
    try:
        from slate.core.infra.studio_settings import get_setting
        return int(get_setting("licence_renewal_days", RENEWAL_SOON_DAYS, db=db) or RENEWAL_SOON_DAYS)
    except Exception:
        return RENEWAL_SOON_DAYS


from .dates import as_date  # noqa: E402  (the shared stored-date reader; importable from here)


def days_until(expiry, today: date = None):
    """Days left on a licence. None when nobody recorded an expiry."""
    expiry = as_date(expiry)
    if not expiry:
        return None
    return (expiry - (today or date.today())).days


def utilisation(peak, seats) -> Optional[float]:
    """
    Peak concurrent use as a fraction of what was bought. None when no seats
    were bought - 3 in use of 0 is not "0% used".
    """
    seats = int(seats or 0)
    if seats <= 0:
        return None
    return float(peak or 0) / float(seats)


def plural(count, word: str, many: str = None) -> str:
    """'1 seat', '3 seats'."""
    count = int(count)
    return "%d %s" % (count, word if count == 1 else (many or word + "s"))


def renewal_phrase(left) -> str:
    """'Renews today' / 'tomorrow' / 'in 31 days'; 'Expired yesterday' / '10 days ago'."""
    if left is None:
        return "No expiry"
    left = int(left)
    if left == 0:
        return "Renews today"
    if left == 1:
        return "Renews tomorrow"
    if left > 1:
        return "Renews in %d days" % left
    if left == -1:
        return "Expired yesterday"
    return "Expired %d days ago" % abs(left)


def is_renewal_due(left, renewal_days: int = RENEWAL_SOON_DAYS) -> bool:
    """Expired, or renewing inside the window - whatever else is wrong with it."""
    return left is not None and left <= renewal_days


def state(seats, peak, expiry, today: date = None, renewal_days: int = RENEWAL_SOON_DAYS,
          last_days: int = None) -> str:
    """
    One word for where this licence stands.

    Order matters: being short of seats outranks an expiry date, because an
    expiry is a diary entry and a shortfall is people unable to work. A peak
    above the seats bought is over-subscribed even when nothing was bought:
    3 in use against 0 seats was reported Healthy.
    """
    seats = int(seats or 0)
    left = days_until(expiry, today)

    if peak is not None and int(peak) > seats:
        return OVER
    if left is not None and left < 0:
        return EXPIRED
    if left is not None and left <= renewal_days:
        return SOON
    if peak is None:
        return UNKNOWN if last_days is None else STALE
    use = utilisation(peak, seats)
    if use is not None and use < UNDER_USED_RATIO:
        return UNDER
    return OK


def tone(state_name: str) -> str:
    return TONE.get(state_name, "IDLE")


def money_text(amount, currency=None) -> str:
    """A yearly cost as people read it: no '.00' on a whole amount ('₹35,00,000')."""
    from slate.core.domain.money import format_money, to_decimal
    value = to_decimal(amount)
    return format_money(value, currency, decimals=0 if value == value.to_integral_value() else 2)


def keep_seats(peak) -> int:
    """A renewal size that leaves headroom above the peak."""
    return max(1, int(math.ceil(int(peak) * (1 + HEADROOM))))


def seat_cost(annual_cost, seats) -> Optional[Decimal]:
    """What one seat costs a year, or None without a cost."""
    from slate.core.domain.money import to_decimal
    seats = int(seats or 0)
    if annual_cost in (None, "") or seats <= 0:
        return None
    try:
        return to_decimal(annual_cost) / Decimal(seats)
    except Exception:
        return None


def spare_cost(annual_cost, seats, peak) -> Optional[Decimal]:
    """The yearly cost of the seats above the peak, or None."""
    per_seat = seat_cost(annual_cost, seats)
    if per_seat is None or peak is None:
        return None
    spare = max(0, int(seats or 0) - int(peak))
    from slate.core.domain.money import quantize
    return quantize(per_seat * spare)


def describe(seats, peak, expiry, today: date = None, renewal_days: int = RENEWAL_SOON_DAYS,
             spare_cost_text: str = "", last_days: int = None) -> str:
    """
    The finding, in a sentence somebody can act on.

    Each one says what was observed and what it means for the renewal - a
    status word on its own tells IT nothing they can take to purchasing.
    spare_cost_text ("₹1,20,000") adds what the unused seats cost a year.
    """
    seats = int(seats or 0)
    left = days_until(expiry, today)
    name = state(seats, peak, expiry, today, renewal_days, last_days)
    # A licence measured before, just not inside the window (last_days: how
    # long ago the newest reading is). It used to read "No usage has been
    # recorded" - false, and the same for every licence once readings lapse.
    lapsed = ("Nothing measured in this window - the last reading was %s ago."
              % plural(last_days, "day")) if last_days is not None else ""
    renewal = ""
    if left is not None and left <= renewal_days:
        renewal = " %s - decide before purchasing needs lead time." % renewal_phrase(left)
    cost = (" That is about %s a year." % spare_cost_text) if spare_cost_text else ""

    if name == OVER:
        return ("Peak use reached %d against %s. That is %d more than we own - either "
                "seats are shared or we are out of compliance.%s"
                % (int(peak), plural(seats, "seat"), int(peak) - seats, renewal))
    if name == EXPIRED:
        return ("%s. Anyone relying on it is already stuck." % renewal_phrase(left))
    if name == SOON:
        if peak is None:
            detail = (" " + lapsed + " Take a fresh one before deciding." if lapsed else
                      " Nothing has been measured, so there is no case for changing the seat count.")
        else:
            use = utilisation(peak, seats) or 0.0
            spare = max(0, seats - int(peak))
            if use < UNDER_USED_RATIO and spare:
                keep = min(seats, keep_seats(peak))
                detail = (" Peak use has been %d of %s - renewing %s would still leave "
                          "headroom.%s" % (int(peak), plural(seats, "seat"), plural(keep, "seat"), cost))
            elif spare:
                detail = (" Peak use has been %d of %s - sized about right, so renew at this size."
                          % (int(peak), plural(seats, "seat")))
            else:
                # Only raise the idea of dropping seats when there are some to
                # drop - "0 could be dropped" reads as a bug, and a fully used
                # licence wants the opposite advice.
                detail = (" Every one of the %s has been in use at once, so renew "
                          "at least at this size." % plural(seats, "seat"))
        return "%s - decide before purchasing needs lead time.%s" % (renewal_phrase(left), detail)
    if name == UNDER:
        spare = seats - int(peak)
        return ("Never more than %d of %s in use at once. %s %s being paid for and not "
                "worked with.%s" % (int(peak), plural(seats, "seat"), plural(spare, "seat"),
                                    "is" if spare == 1 else "are", cost))
    if name == STALE:
        return lapsed + " Record usage again so the peak means something."
    if name == UNKNOWN:
        return ("No usage has been recorded, so there is nothing to renew "
                "against except the invoice.")
    return "Peak use %d of %s. Sized about right." % (int(peak), plural(seats, "seat"))
