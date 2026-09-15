"""
What a shot costs to bid, as a rule rather than as three numbers in a dialog.

The days-per-shot figures were literals inside the bidding dialog, which meant a
studio whose comp work runs heavier than the default had no way to say so short
of editing the code. They live here, and a studio overrides them in settings.

This module imports nothing but the standard library, for the same reason
leave_policy does not: a cost model should be readable and testable without a
database or a dialog anywhere near it.
"""

from __future__ import annotations


# Artist days per shot, by how hard the work is. UT's own figures.
DEFAULT_MULTIPLIERS = {
    "Simple": 1.5,
    "Medium": 3.0,
    "Hard": 7.0,
}

# The rate a studio bids an artist day at, before margin.
DEFAULT_DAY_RATE = 300.0

# What the studio wants to keep. Bidding at cost is how a facility goes under
# while every project comes in on budget.
DEFAULT_MARGIN_PERCENT = 20.0

COMPLEXITIES = tuple(DEFAULT_MULTIPLIERS)

_OVERRIDES = {}


def set_overrides(overrides=None) -> None:
    """Install the studio's own figures. Anything unnamed keeps its default."""
    global _OVERRIDES
    _OVERRIDES = {k: v for k, v in dict(overrides or {}).items() if v is not None}


def multipliers() -> dict:
    """Artist days per shot, by complexity."""
    merged = dict(DEFAULT_MULTIPLIERS)
    supplied = _OVERRIDES.get("multipliers")
    if isinstance(supplied, dict):
        for name, value in supplied.items():
            try:
                merged[str(name).title()] = float(value)
            except (TypeError, ValueError):
                continue
    return merged


def days_per_shot(complexity: str) -> float:
    """How many artist days one shot of this complexity is bid at."""
    table = multipliers()
    return float(table.get(str(complexity).strip().title(),
                           table.get("Medium", DEFAULT_MULTIPLIERS["Medium"])))


def day_rate() -> float:
    try:
        return float(_OVERRIDES.get("day_rate") or 0) or DEFAULT_DAY_RATE
    except (TypeError, ValueError):
        return DEFAULT_DAY_RATE


def margin_percent() -> float:
    value = _OVERRIDES.get("margin_percent")
    if value is None:
        return DEFAULT_MARGIN_PERCENT
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_MARGIN_PERCENT


def estimate(shots: int, complexity: str, rate: float = None,
             margin: float = None) -> dict:
    """
    What a job of this size is worth bidding.

    Margin is the share of the price the studio keeps, so the price is the cost
    divided by what is left - not the cost plus the margin, which quietly bids
    under. At 20% those differ by four per cent of the whole job.
    """
    shots = max(0, int(shots or 0))
    rate = day_rate() if rate is None else float(rate)
    margin = margin_percent() if margin is None else float(margin)

    days = shots * days_per_shot(complexity)
    cost = days * rate
    if 0 <= margin < 100:
        price = cost / (1 - (margin / 100.0))
    else:
        # 100% margin or more has no arithmetic meaning here. Bidding at cost is
        # wrong but at least it is a number somebody can see is wrong.
        price = cost

    return {"days": days, "cost": cost, "price": price}
