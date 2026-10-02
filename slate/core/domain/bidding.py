"""
What a job is worth bidding, and what became of the bid - as rules rather
than as numbers in a dialog.

The days-per-shot figures were literals inside the bidding dialog, which meant a
studio whose comp work runs heavier than the default had no way to say so short
of editing the code. They live here, and a studio overrides them in its
settings (studio_settings "bidding", pushed in by studio_policy).

A bid used to be one complexity and one day rate for a whole project, priced in
floats from spin boxes: editing re-priced it at the default rate, a margin of
99.99% made the price ten thousand times the cost and 100% silently priced at
cost, the budget column was float4 and lost cents, and the shot count was
whatever the tracker held that day. Now:

    BidLine           one line: label, shot (optional), department,
                      complexity, shots, days per shot, day rate - Decimal
    price_bid(...)    cost -> margin -> price -> discount -> tax -> total,
                      Decimal throughout, rounded half-up once per figure
    check_bid(...)    why a bid cannot be saved yet
    STATUSES          Draft, Sent, Won (stored 'Approved'), Lost (stored
                      'Rejected'), Superseded - and which changes are allowed
    pipeline(...)     open and won totals, per currency, latest revision only
    compare(...)      what changed between two revisions
    track(...)        a won bid against the dashboard: bid, planned, done and
                      actual days per department

This module imports nothing outside the standard library and its sibling
domain modules, for the same reason leave_policy does not: a cost model should
be readable and testable without a database or a dialog anywhere near it.
"""

from __future__ import annotations

import logging
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# Artist days per shot, by how hard the work is. UT's own figures.
DEFAULT_MULTIPLIERS = {
    "Simple": 1.5,
    "Medium": 3.0,
    "Hard": 7.0,
}

# The rate a studio bids an artist day at, before margin (dollars; rupee and
# other rates come from the studio's day_rates setting - see money.day_rate).
DEFAULT_DAY_RATE = 300.0

# What the studio wants to keep. Bidding at cost is how a facility goes under
# while every project comes in on budget.
DEFAULT_MARGIN_PERCENT = 20.0

# A margin above this is refused, and above the warning level questioned. At
# 99.99% the price was ten thousand times the cost.
DEFAULT_MAX_MARGIN_PERCENT = 80.0
MARGIN_WARNING_PERCENT = 50.0

COMPLEXITIES = tuple(DEFAULT_MULTIPLIERS)

_OVERRIDES = {}

CENT = Decimal("0.01")
HUNDRED = Decimal(100)


def set_overrides(overrides=None) -> None:
    """Install the studio's own figures. Anything unnamed keeps its default."""
    global _OVERRIDES
    _OVERRIDES = {k: v for k, v in dict(overrides or {}).items() if v is not None}


def overrides() -> dict:
    return dict(_OVERRIDES)


def multipliers() -> dict:
    """
    Artist days per shot, by complexity. The studio's figures are added to the
    defaults - or, when it saved its own complete table (the Bidding settings
    dialog does), replace them, so a complexity can also be removed.
    """
    supplied = _OVERRIDES.get("multipliers")
    complete = bool(_OVERRIDES.get("multipliers_complete")) and isinstance(supplied, dict) and supplied
    merged = {} if complete else dict(DEFAULT_MULTIPLIERS)
    if isinstance(supplied, dict):
        for name, value in supplied.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                merged[str(name).strip().title()] = number
    return merged or dict(DEFAULT_MULTIPLIERS)


def complexities() -> List[str]:
    """The complexities to offer, lightest first."""
    return [name for name, _v in sorted(multipliers().items(), key=lambda kv: (kv[1], kv[0]))]


def days_per_shot(complexity: str) -> float:
    """How many artist days one shot of this complexity is bid at."""
    table = multipliers()
    return float(table.get(str(complexity).strip().title(),
                           table.get("Medium", DEFAULT_MULTIPLIERS["Medium"])))


def day_rate() -> float:
    """
    The studio's dollar day rate. An explicit 0 or negative in the settings is
    a mistake, not a free day: it is logged and the default used.
    """
    value = _OVERRIDES.get("day_rate")
    if value in (None, ""):
        return DEFAULT_DAY_RATE
    try:
        number = float(value)
    except (TypeError, ValueError):
        logger.warning("Bidding day rate %r is not a number; using %s.", value, DEFAULT_DAY_RATE)
        return DEFAULT_DAY_RATE
    if number <= 0:
        logger.warning("Bidding day rate %r is not more than zero; using %s.", value, DEFAULT_DAY_RATE)
        return DEFAULT_DAY_RATE
    return number


def margin_percent() -> float:
    value = _OVERRIDES.get("margin_percent")
    if value is None:
        return DEFAULT_MARGIN_PERCENT
    try:
        return min(float(value), max_margin_percent())
    except (TypeError, ValueError):
        return DEFAULT_MARGIN_PERCENT


def max_margin_percent() -> float:
    value = _OVERRIDES.get("max_margin_percent")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_MARGIN_PERCENT
    return number if 1 <= number <= 95 else DEFAULT_MAX_MARGIN_PERCENT


def tax_label(currency_code: str = "") -> str:
    """'GST' on rupee bids, 'Tax' otherwise, unless the studio named it."""
    named = str(_OVERRIDES.get("tax_label") or "").strip()
    if str(currency_code or "").upper() == "INR":
        return named or "GST"
    return "Tax"


def estimate(shots: int, complexity: str, rate: float = None,
             margin: float = None) -> dict:
    """
    What a job of this size is worth bidding (one complexity, floats - kept for
    callers that only need a quick figure; bids are priced with price_bid).

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


# ------------------------------------------------------------------ numbers

def dec(value, default: Decimal = Decimal(0)) -> Decimal:
    """An exact number from whatever the database or a widget handed over."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return default


def money(value) -> Decimal:
    """Rounded to the paisa / cent, half away from zero."""
    return dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_days(value) -> str:
    """'791 days', '34.5 days', '1 day', '0 days'."""
    number = dec(value).quantize(CENT, rounding=ROUND_HALF_UP).normalize()
    text = f"{number:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {'day' if number == 1 else 'days'}"


def fmt_percent(value) -> str:
    """'20%', '12.5%', '12.25%' - a whole number without decimals."""
    number = dec(value).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{number:f}".rstrip("0").rstrip(".") if "." in f"{number:f}" else f"{number:f}"
    return f"{text}%"


# What things are called, the same in the editor, the table and the export.
WORDS = {
    "shots": "Shots", "complexity": "Complexity", "day_rate": "Day rate", "margin": "Margin",
    "days": "Artist days", "cost": "Cost", "price": "Price", "discount": "Discount",
    "tax": "Tax", "total": "Total", "client": "Client", "project": "Project",
    "revision": "Rev", "status": "Status",
}


# ------------------------------------------------------------------ statuses

DRAFT = "Draft"
SENT = "Sent"
WON = "Approved"           # stored as before; shown as "Won"
LOST = "Rejected"          # stored as before; shown as "Lost"
SUPERSEDED = "Superseded"

STATUSES = (DRAFT, SENT, WON, LOST, SUPERSEDED)
DECIDED = frozenset({WON, LOST})
STATUS_LABEL = {DRAFT: "Draft", SENT: "Sent", WON: "Won", LOST: "Lost", SUPERSEDED: "Superseded"}
STATUS_TONE = {DRAFT: "info", SENT: "accent", WON: "ok", LOST: "bad", SUPERSEDED: "idle"}

# Which status may follow which. Superseded is final: a newer revision exists.
TRANSITIONS = {
    DRAFT: {SENT, WON, LOST},
    SENT: {DRAFT, WON, LOST},
    WON: {DRAFT, LOST},
    LOST: {DRAFT, WON},
    SUPERSEDED: set(),
}


def normalise_status(value) -> str:
    text = str(value or "").strip()
    for stored, shown in STATUS_LABEL.items():
        if text.casefold() in (stored.casefold(), shown.casefold()):
            return stored
    return text or DRAFT


def status_label(value) -> str:
    """'Won' for 'Approved', 'Lost' for 'Rejected'."""
    stored = normalise_status(value)
    return STATUS_LABEL.get(stored, stored)


def status_tone(value) -> str:
    return STATUS_TONE.get(normalise_status(value), "idle")


def can_change(old, new) -> bool:
    return normalise_status(new) in TRANSITIONS.get(normalise_status(old), set())


def is_editable(status) -> bool:
    """Only a draft is changed in place; anything sent or decided gets a new revision."""
    return normalise_status(status) == DRAFT


def decision_refusal(new_status, *, can_approve: bool, superuser: bool,
                     creator: str = "", me: str = "") -> str:
    """
    Why this person may not record a Won/Lost (or reopen a decided bid), or ''.
    Nobody decides their own bid except Admin and Developer (FIX_PLAN).
    """
    if not can_approve and not superuser:
        return "Marking a bid Won or Lost needs the 'Approve bids' ability."
    if not superuser and creator and me and creator.casefold() == me.casefold():
        return "You made this bid, so somebody else has to decide it."
    return ""


# ------------------------------------------------------------------ lines

class BidError(ValueError):
    """A bid or a figure that cannot be used. str() is the sentence to show."""


@dataclass
class BidLine:
    label: str = ""
    department: str = ""
    complexity: str = ""
    shot_count: int = 1
    days_per_shot: Decimal = Decimal(0)
    day_rate: Decimal = Decimal(0)
    shot_name: str = ""
    reel: str = ""
    notes: str = ""
    id: Optional[int] = None
    position: int = 0

    @property
    def days(self) -> Decimal:
        return (Decimal(int(self.shot_count or 0)) * dec(self.days_per_shot)).quantize(
            CENT, rounding=ROUND_HALF_UP)

    @property
    def cost(self) -> Decimal:
        return money(self.days * dec(self.day_rate))

    @classmethod
    def from_row(cls, row) -> "BidLine":
        row = dict(row or {})
        try:
            count = int(dec(row.get("shot_count"), Decimal(1)))
        except (TypeError, ValueError):
            count = 1
        return cls(label=str(row.get("label") or ""), department=str(row.get("department") or ""),
                   complexity=str(row.get("complexity") or ""), shot_count=count,
                   days_per_shot=dec(row.get("days_per_shot")), day_rate=dec(row.get("day_rate")),
                   shot_name=str(row.get("shot_name") or ""), reel=str(row.get("reel") or ""),
                   notes=str(row.get("notes") or ""), id=row.get("id"),
                   position=int(row.get("position") or 0))

    def key(self) -> Tuple[str, str, str]:
        """What makes two lines of two revisions 'the same line'."""
        return (" ".join(self.label.split()).casefold(), self.department.casefold(),
                self.shot_name.casefold())


def legacy_line(header: dict) -> BidLine:
    """
    A bid from before line items, as one line - so it opens with the figures it
    was saved with. Its day rate is the one implied by the stored cost (the
    rate was never stored, so editing used to re-price it at the default).
    """
    shots = int(dec(header.get("shot_count")))
    days = dec(header.get("estimated_days"))
    cost = dec(header.get("estimated_cost"))
    rate = dec(header.get("day_rate"))
    if rate <= 0 and days > 0 and cost > 0:
        rate = money(cost / days)
    per_shot = (days / shots).quantize(CENT, rounding=ROUND_HALF_UP) if shots else days
    count = shots if shots else 1
    return BidLine(label="All shots" if shots else "Whole job", complexity=str(header.get("complexity") or ""),
                   shot_count=count, days_per_shot=per_shot, day_rate=rate)


@dataclass
class BidTotals:
    days: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    margin_percent: Decimal = Decimal(0)
    margin_amount: Decimal = Decimal(0)     # what the margin earns: price - cost
    price: Decimal = Decimal(0)
    discount_percent: Decimal = Decimal(0)
    discount_amount: Decimal = Decimal(0)
    taxable: Decimal = Decimal(0)           # price after discount: the studio's revenue
    tax_percent: Decimal = Decimal(0)
    tax_amount: Decimal = Decimal(0)
    total: Decimal = Decimal(0)             # what the client pays
    shots: int = 0


def price_bid(lines: Iterable[BidLine], margin, discount=0, tax=0) -> BidTotals:
    """
    The bid's figures, exactly:

        cost      = sum of line costs (days x rate, each to the paisa)
        price     = cost / (1 - margin)       - margin is a share of the price
        discount  = discount% of price
        taxable   = price - discount
        tax       = tax% of taxable (GST on rupee bids)
        total     = taxable + tax

    A margin of 100% or more has no meaning and is refused (it used to price
    at cost); a negative one too.
    """
    lines = list(lines)
    m, d, t = dec(margin), dec(discount), dec(tax)
    if m < 0 or m >= HUNDRED:
        raise BidError("The margin must be at least 0% and below 100%.")
    if not 0 <= d <= HUNDRED:
        raise BidError("The discount must be between 0% and 100%.")
    if not 0 <= t <= HUNDRED:
        raise BidError("The tax must be between 0% and 100%.")
    out = BidTotals(margin_percent=m, discount_percent=d, tax_percent=t)
    for line in lines:
        out.days += line.days
        out.cost += line.cost
        out.shots += int(line.shot_count or 0)
    out.price = money(out.cost / (1 - m / HUNDRED))
    out.margin_amount = out.price - out.cost
    out.discount_amount = money(out.price * d / HUNDRED)
    out.taxable = out.price - out.discount_amount
    out.tax_amount = money(out.taxable * t / HUNDRED)
    out.total = out.taxable + out.tax_amount
    return out


def check_bid(project_code: str, lines: Sequence[BidLine], margin, discount=0, tax=0,
              max_margin: Optional[float] = None) -> List[str]:
    """Every reason the bid cannot be saved yet, in the order a person fixes them."""
    problems = []
    if not str(project_code or "").strip():
        problems.append("Choose the project this bid is for.")
    if not lines:
        problems.append("Add at least one line with days.")
    for number, line in enumerate(lines, start=1):
        name = line.label or line.shot_name or f"Line {number}"
        if not (line.label or line.shot_name).strip():
            problems.append(f"Line {number} needs a description.")
        if int(line.shot_count or 0) <= 0:
            problems.append(f"\"{name}\" needs at least one shot.")
        if dec(line.days_per_shot) <= 0:
            problems.append(f"\"{name}\" needs days per shot.")
        if dec(line.day_rate) <= 0:
            problems.append(f"\"{name}\" needs a day rate above zero.")
    limit = Decimal(str(max_margin if max_margin is not None else max_margin_percent()))
    if dec(margin) > limit:
        problems.append(f"The margin is above the studio's limit of {fmt_percent(limit)}.")
    try:
        totals = price_bid(lines, margin, discount, tax)
        if lines and totals.total <= 0:
            problems.append("The bid comes to nothing - check the days and rates.")
    except BidError as exc:
        problems.append(str(exc))
    return problems


# ------------------------------------------------------------------ pipeline

@dataclass
class Pipeline:
    open_totals: "OrderedDict[str, Decimal]" = field(default_factory=OrderedDict)
    won_totals: "OrderedDict[str, Decimal]" = field(default_factory=OrderedDict)
    open_count: int = 0
    won_count: int = 0
    bids: int = 0


def _sum_by_currency(pairs) -> "OrderedDict[str, Decimal]":
    from .money import sum_by_currency
    return sum_by_currency(pairs)


def pipeline(bids: Iterable[dict]) -> Pipeline:
    """
    Open work (Draft + Sent) and won work (Won), per currency - rupees and
    dollars are never added together.

    Only the latest revision of a bid counts (older ones are Superseded), and
    archived bids not at all. Open: only the newest open bid per project - ten
    drafts for one job are ten versions of one price, not ten jobs (one absurd
    99.99% draft made up $357M of a $380M "pipeline"). Won: every won bid, as
    extra scope on a project is real money.
    """
    out = Pipeline()
    newest_open: Dict[str, dict] = {}
    won = []
    for bid in bids:
        if bid.get("archived_at"):
            continue
        status = normalise_status(bid.get("status"))
        if status == SUPERSEDED:
            continue
        out.bids += 1
        if status == WON:
            won.append(bid)
        elif status in (DRAFT, SENT):
            key = str(bid.get("project_code") or "").casefold()
            current = newest_open.get(key)
            if current is None or _newer(bid, current):
                newest_open[key] = bid
    out.open_count, out.won_count = len(newest_open), len(won)
    out.open_totals = _sum_by_currency((b.get("estimated_budget"), b.get("currency") or "USD")
                                       for b in newest_open.values())
    out.won_totals = _sum_by_currency((b.get("estimated_budget"), b.get("currency") or "USD")
                                      for b in won)
    return out


def _newer(a: dict, b: dict) -> bool:
    def stamp(x):
        return (str(x.get("created_at") or ""), int(x.get("id") or 0))
    return stamp(a) > stamp(b)


# ------------------------------------------------------------------ revisions

@dataclass
class LineChange:
    kind: str                      # "added" | "removed" | "changed"
    label: str
    before: Optional[BidLine] = None
    after: Optional[BidLine] = None
    fields: List[str] = field(default_factory=list)

    @property
    def cost_delta(self) -> Decimal:
        return (self.after.cost if self.after else 0) - (self.before.cost if self.before else 0)


_COMPARED = (("department", "Department"), ("complexity", "Complexity"), ("shot_count", "Shots"),
             ("days_per_shot", "Days per shot"), ("day_rate", "Day rate"))


def compare(before: Sequence[BidLine], after: Sequence[BidLine]) -> List[LineChange]:
    """
    What changed from one revision's lines to the next: lines added, removed
    and changed (with which fields). Lines are matched by description,
    department and shot, in order, so two identical lines pair up one to one.
    """
    pool: Dict[tuple, List[BidLine]] = defaultdict(list)
    for line in before:
        pool[line.key()].append(line)
    changes: List[LineChange] = []
    for line in after:
        candidates = pool.get(line.key())
        if candidates:
            old = candidates.pop(0)
            diff = [label for attr, label in _COMPARED
                    if (dec(getattr(old, attr)) if attr not in ("department", "complexity")
                        else getattr(old, attr)) !=
                    (dec(getattr(line, attr)) if attr not in ("department", "complexity")
                     else getattr(line, attr))]
            if diff:
                changes.append(LineChange("changed", line.label or line.shot_name, old, line, diff))
        else:
            changes.append(LineChange("added", line.label or line.shot_name, None, line))
    for leftovers in pool.values():
        for old in leftovers:
            changes.append(LineChange("removed", old.label or old.shot_name, old, None))
    return changes


# ------------------------------------------------------------------ tracking

DONE_TASK_STATUSES = {"APPROVED", "DONE", "COMPLETE", "COMPLETED", "FINAL"}
OMITTED_SHOT_STATUSES = {"OMIT", "OMITTED", "CANCELLED", "CANCELED"}


@dataclass
class DepartmentTrack:
    department: str
    bid_days: Decimal = Decimal(0)
    bid_cost: Decimal = Decimal(0)
    planned_days: Decimal = Decimal(0)     # bid days on the dashboard's tasks
    done_days: Decimal = Decimal(0)        # of those, tasks in a done status
    actual_days: Decimal = Decimal(0)      # what was really spent, where recorded
    rate: Decimal = Decimal(0)             # the bid's average day rate for it

    @property
    def planned_cost(self) -> Decimal:
        return money(self.planned_days * self.rate)

    @property
    def done_cost(self) -> Decimal:
        return money(self.done_days * self.rate)

    @property
    def actual_cost(self) -> Decimal:
        return money(self.actual_days * self.rate)

    @property
    def remaining_days(self) -> Decimal:
        return self.bid_days - self.done_days

    @property
    def variance_days(self) -> Decimal:
        """Planned minus bid: above zero means more work is planned than was sold."""
        return self.planned_days - self.bid_days

    @property
    def burn_percent(self) -> Optional[Decimal]:
        """Done days as a share of bid days (None when nothing was bid)."""
        if self.bid_days <= 0:
            return None
        return (self.done_days * HUNDRED / self.bid_days).quantize(Decimal("0.1"),
                                                                    rounding=ROUND_HALF_UP)

    @property
    def over(self) -> bool:
        return self.planned_days > self.bid_days or self.actual_days > self.bid_days


@dataclass
class Tracking:
    departments: List[DepartmentTrack] = field(default_factory=list)
    bid_days: Decimal = Decimal(0)
    planned_days: Decimal = Decimal(0)
    done_days: Decimal = Decimal(0)
    actual_days: Decimal = Decimal(0)
    bid_cost: Decimal = Decimal(0)
    planned_cost: Decimal = Decimal(0)
    done_cost: Decimal = Decimal(0)
    actual_cost: Decimal = Decimal(0)
    bid_not_on_tracker: List[str] = field(default_factory=list)
    tracker_not_in_bid: List[str] = field(default_factory=list)
    actual_recorded: bool = False


UNASSIGNED = "(no department)"


def track(lines: Sequence[BidLine], tasks: Iterable[dict],
          tracker_shots: Iterable[Tuple[str, str, str]] = (), default_rate=0) -> Tracking:
    """
    A won bid against what the dashboard holds for its project, per department:

        bid days       from the bid's lines
        planned days   bid_days on the dashboard's tasks (what was planned)
        done days      bid_days of tasks in a done status (what was delivered)
        actual days    actual_days on tasks, where somebody recorded them

    Money is days at the bid's own rate for that department (its lines' cost /
    days), in the bid's currency. A department planned or spent above its bid
    is flagged. Shot coverage lists shots bid but not on the tracker, and
    tracker shots the bid never priced (omitted ones excluded).
    """
    by_dept: Dict[str, DepartmentTrack] = OrderedDict()

    def entry(dept: str) -> DepartmentTrack:
        key = (dept or "").strip().lower() or UNASSIGNED
        if key not in by_dept:
            by_dept[key] = DepartmentTrack(key)
        return by_dept[key]

    for line in lines:
        e = entry(line.department)
        e.bid_days += line.days
        e.bid_cost += line.cost
    for e in by_dept.values():
        e.rate = money(e.bid_cost / e.bid_days) if e.bid_days else dec(default_rate)
    out = Tracking()
    for task in tasks:
        dept = str(task.get("department") or "").strip().lower()
        days = dec(task.get("bid_days"))
        actual = task.get("actual_days")
        if dept not in by_dept and days <= 0 and actual in (None, ""):
            continue                       # a department nobody bid or planned
        e = entry(dept)
        if e.rate <= 0:
            e.rate = dec(default_rate)
        e.planned_days += days
        if str(task.get("status") or "").strip().upper() in DONE_TASK_STATUSES:
            e.done_days += days
        if actual not in (None, ""):
            out.actual_recorded = True
            e.actual_days += dec(actual)
    out.departments = sorted(by_dept.values(), key=lambda e: (-e.bid_days, e.department))
    for e in out.departments:
        out.bid_days += e.bid_days
        out.planned_days += e.planned_days
        out.done_days += e.done_days
        out.actual_days += e.actual_days
        out.bid_cost += e.bid_cost
        out.planned_cost += e.planned_cost
        out.done_cost += e.done_cost
        out.actual_cost += e.actual_cost

    def shot_key(reel, shot):
        return (str(reel or "").strip().casefold(), str(shot or "").strip().casefold())

    bid_shots = OrderedDict()
    for line in lines:
        if line.shot_name.strip():
            bid_shots.setdefault(shot_key(line.reel, line.shot_name), line.shot_name.strip())
    tracker = OrderedDict()
    for reel, shot, status in tracker_shots:
        if str(status or "").strip().upper() in OMITTED_SHOT_STATUSES:
            continue
        tracker.setdefault(shot_key(reel, shot), str(shot))
    names_on_tracker = {k[1] for k in tracker}
    names_in_bid = {k[1] for k in bid_shots}
    # A bid line without a reel matches the shot in any reel.
    out.bid_not_on_tracker = [name for key, name in bid_shots.items()
                              if key not in tracker and not (not key[0] and key[1] in names_on_tracker)]
    if bid_shots:
        out.tracker_not_in_bid = [name for key, name in tracker.items()
                                  if key not in bid_shots and key[1] not in names_in_bid]
    return out


def shots_to_create(lines: Sequence[BidLine]) -> "OrderedDict[Tuple[str, str], Dict[str, Decimal]]":
    """
    The shots a won bid names, with the bid days of each department:
    {(reel, shot): {department: days}}. Lines without a shot name are group
    lines ('Roto - 40 shots') and name no shot.
    """
    out: "OrderedDict[Tuple[str, str], Dict[str, Decimal]]" = OrderedDict()
    for line in lines:
        shot = line.shot_name.strip()
        if not shot:
            continue
        key = (line.reel.strip(), shot)
        depts = out.setdefault(key, {})
        dept = (line.department or "comp").strip().lower()
        per_shot = dec(line.days_per_shot)
        depts[dept] = depts.get(dept, Decimal(0)) + per_shot
    return out
