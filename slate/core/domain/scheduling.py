"""
The rules of the production schedule, without a database or a widget.

Scheduling used to keep its rules inside the tab: the status list was three
strings in a combo, "overdue" compared date *text* (so '15/10/2026' was
earlier than '2026-09-30'), and shifting a milestone walked its dependents
recursively, one committed UPDATE at a time - a long chain hit Python's
recursion limit after 982 rows and left the plan half moved, a milestone with
no dates crashed the click, and completed milestones were moved along with
everything else.

Everything that decides something lives here now, as plain functions over
Milestone records, so it can be tested on its own:

    STATUSES / STATUS_TONE        one list, with the colour each status wears
    Milestone.from_row(row)       a database row (either backend) as dates
    WorkCalendar                  weekly offs + studio holidays
    plan_shift(...)               what a shift would move, skip and break
    check_milestone(...)          why a milestone cannot be saved as typed
    summary(...)                  the counts on the cards
    timeline maths                date <-> x, ruler ticks, rows, arrows
    people_plan(...)              who is on what, leave, over-allocation

Imports nothing beyond the standard library, the shared date helper and
the dashboard's status words (shot_status).
"""

from __future__ import annotations

import math
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import shot_status
from .dates import format_date, format_range, parse_date


# ------------------------------------------------------------------ statuses

SCHEDULED = "Scheduled"
IN_PROGRESS = "In Progress"
ON_HOLD = "On Hold"
BLOCKED = "Blocked"
COMPLETED = "Completed"
CANCELLED = "Cancelled"

# Every status a milestone can be given, in the order people meet them. The
# dialog, the filters, the colours and the cards all read this one tuple; it
# used to be three strings typed into a combo, so 'On Hold' rows that already
# existed could be neither set nor filtered and had no colour.
STATUSES = (SCHEDULED, IN_PROGRESS, ON_HOLD, BLOCKED, COMPLETED, CANCELLED)

# Finished one way or the other: never overdue, never moved by a shift unless
# asked, and not "open" work.
CLOSED = frozenset({COMPLETED, CANCELLED})

# The tone each status is drawn in (table_style.set_cell_status / Gate tokens).
STATUS_TONE = {
    SCHEDULED: "info",
    IN_PROGRESS: "warn",
    ON_HOLD: "idle",
    BLOCKED: "bad",
    COMPLETED: "ok",
    CANCELLED: "idle",
}
OVERDUE_TONE = "bad"

# A milestone name is a line in a table and a label on a bar. 500 characters
# were accepted and broke the table.
MAX_NAME = 120


def normalise_status(value) -> str:
    """A stored status as one of STATUSES when it is one (any case), else as stored."""
    text = str(value or "").strip()
    for known in STATUSES:
        if known.casefold() == text.casefold():
            return known
    return text


def status_tone(status) -> str:
    """The tone for a status; anything unknown (old values) is 'idle'."""
    return STATUS_TONE.get(normalise_status(status), "idle")


def is_closed(status) -> bool:
    return normalise_status(status) in CLOSED


# ------------------------------------------------------------------ records

def _decimal(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class Milestone:
    id: Optional[int] = None
    project_code: str = ""
    name: str = ""
    start: Optional[date] = None
    end: Optional[date] = None
    status: str = SCHEDULED
    depends_on_id: Optional[int] = None
    owner: str = ""
    department: str = ""
    effort_days: Optional[Decimal] = None
    completed_on: Optional[date] = None
    # The original text of dates that could not be read when the columns
    # became real dates, so nothing typed is ever lost.
    legacy_dates: str = ""
    project_name: str = ""
    archived: bool = False          # its project is no longer active
    created_by: str = ""
    created_at: Optional[datetime] = None
    updated_by: str = ""
    updated_at: Optional[datetime] = None
    # What the database held for the dates, for a row whose dates do not read
    # as dates ('None', '31/02/2026'): shown as "Invalid date", never as today.
    start_text: str = ""
    end_text: str = ""

    @classmethod
    def from_row(cls, row) -> "Milestone":
        row = dict(row or {})

        def _id(value):
            try:
                return int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                return None

        def _when(value):
            if value in (None, ""):
                return None
            if isinstance(value, datetime):
                return value
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return None

        active = row.get("project_active")
        archived = False
        if active is not None and str(active).strip() != "":
            try:
                archived = int(active) == 0
            except (TypeError, ValueError):
                archived = str(active).strip().lower() in ("false", "f", "no")
        start_raw, end_raw = row.get("start_date"), row.get("end_date")
        return cls(
            id=_id(row.get("id")),
            project_code=str(row.get("project_code") or "").strip(),
            name=str(row.get("milestone") or "").strip(),
            start=parse_date(start_raw),
            end=parse_date(end_raw),
            status=normalise_status(row.get("status")) or "",
            depends_on_id=_id(row.get("depends_on_id")),
            owner=str(row.get("owner") or "").strip(),
            department=str(row.get("department") or "").strip(),
            effort_days=_decimal(row.get("effort_days")),
            completed_on=parse_date(row.get("completed_on")),
            legacy_dates=str(row.get("legacy_dates") or ""),
            project_name=str(row.get("project_name") or "").strip(),
            archived=archived,
            created_by=str(row.get("created_by") or ""),
            created_at=_when(row.get("created_at")),
            updated_by=str(row.get("updated_by") or ""),
            updated_at=_when(row.get("updated_at")),
            start_text="" if start_raw is None else str(start_raw),
            end_text="" if end_raw is None else str(end_raw),
        )

    @property
    def is_open(self) -> bool:
        return not is_closed(self.status)

    @property
    def has_dates(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def dates_reversed(self) -> bool:
        return self.has_dates and self.end < self.start


def is_overdue(m: Milestone, today: Optional[date] = None) -> bool:
    """
    Open and its end date has passed. A row whose dates do not read is never
    overdue, and neither is one of an archived project - that is not work
    anybody is late on.
    """
    today = today or date.today()
    return m.is_open and not m.archived and m.end is not None and m.end < today


def days_late(m: Milestone, today: Optional[date] = None) -> int:
    today = today or date.today()
    return (today - m.end).days if is_overdue(m, today) else 0


def plural(count, singular: str, plural_form: Optional[str] = None) -> str:
    """'1 day', '3 days', '1 dependent milestone'."""
    if isinstance(count, Decimal):
        number = count.normalize()
        shown = f"{number:f}"
        is_one = number == 1
    else:
        shown, is_one = str(count), count == 1
    word = singular if is_one else (plural_form or singular + "s")
    return f"{shown} {word}"


def index(milestones: Iterable[Milestone]) -> Dict[int, Milestone]:
    return {m.id: m for m in milestones if m.id is not None}


def dependency_label(m: Milestone, by_id: Dict[int, Milestone]) -> Tuple[str, str]:
    """
    What the Depends On column says, and its tone ('' / 'warn').

    'Roto & prep (ends 10 Oct 2026)' - the name alone was ambiguous when two
    milestones share it. A link to a milestone that no longer exists said
    'None' and hid that it was broken.
    """
    if not m.depends_on_id:
        return "", ""
    parent = by_id.get(m.depends_on_id)
    if parent is None:
        return f"Missing (#{m.depends_on_id})", "warn"
    ends = f" (ends {format_date(parent.end)})" if parent.end else " (no end date)"
    if parent.project_code.casefold() != m.project_code.casefold():
        return f"Other project: {parent.project_code} / {parent.name}{ends}", "warn"
    return f"{parent.name}{ends}", ""


# ------------------------------------------------------------------ calendar

class WorkCalendar:
    """
    Which days are worked: not a weekly off, not a studio holiday.

    weekly_offs uses date.weekday() numbers (Monday = 0); the studio's come
    from the attendance policy. holidays is {date: name}.
    """

    def __init__(self, weekly_offs: Iterable[int] = (6,), holidays=None):
        self.weekly_offs: Set[int] = {int(d) for d in (weekly_offs or ())}
        if len(self.weekly_offs) >= 7:
            self.weekly_offs = {6}          # a calendar with no working day cannot schedule
        if isinstance(holidays, dict):
            self.holidays: Dict[date, str] = {parse_date(k): str(v or "Holiday")
                                              for k, v in holidays.items() if parse_date(k)}
        else:
            self.holidays = {parse_date(d): "Holiday" for d in (holidays or ()) if parse_date(d)}

    def is_working(self, day: date) -> bool:
        return day.weekday() not in self.weekly_offs and day not in self.holidays

    def why_not_working(self, day: Optional[date]) -> str:
        """'Saturday' / 'Diwali (holiday)' / '' for a working day."""
        if day is None or self.is_working(day):
            return ""
        if day in self.holidays:
            return f"{self.holidays[day]} (holiday)"
        return day.strftime("%A")

    def add_working_days(self, day: date, count: int) -> date:
        """
        Move by count working days. +1 from a Friday is the Monday (with a
        Sunday-only weekly off: the Saturday); a holiday in between is
        stepped over. 0 returns the day itself.
        """
        if day is None or not count:
            return day
        step = 1 if count > 0 else -1
        remaining = abs(int(count))
        cursor = day
        guard = 0
        while remaining:
            cursor += timedelta(days=step)
            guard += 1
            if self.is_working(cursor):
                remaining -= 1
            if guard > 366 * 50:            # a calendar with no working days at all
                break
        return cursor

    def working_days(self, start: Optional[date], end: Optional[date]) -> int:
        """Working days from start to end inclusive (0 when either is missing or reversed)."""
        if start is None or end is None or end < start:
            return 0
        total, cursor = 0, start
        while cursor <= end:
            if self.is_working(cursor):
                total += 1
            cursor += timedelta(days=1)
        return total

    def span_days(self, start: date, end: date) -> List[date]:
        days, cursor = [], start
        while cursor <= end:
            if self.is_working(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return days


# ------------------------------------------------------------------ checking

class ScheduleError(ValueError):
    """A milestone that cannot be saved as given. str() is the sentence to show."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


def _same_name(a: str, b: str) -> bool:
    return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()


def check_milestone(m: Milestone, others: Iterable[Milestone]) -> List[ScheduleError]:
    """
    Every reason this milestone cannot be saved, in form order. Empty when it
    can. `others` is the schedule as it stands (the milestone itself, when it
    is being edited, is ignored by id).
    """
    problems: List[ScheduleError] = []
    others = [o for o in others if o.id is None or o.id != m.id]
    by_id = index(others)

    if not m.project_code:
        problems.append(ScheduleError("Choose the project this milestone belongs to.", "project"))
    name = " ".join(m.name.split())
    if not name:
        problems.append(ScheduleError("Give the milestone a name.", "name"))
    elif len(name) > MAX_NAME:
        problems.append(ScheduleError(
            f"Keep the name to {MAX_NAME} characters (it has {len(name)}).", "name"))
    elif m.project_code and any(
            o.project_code.casefold() == m.project_code.casefold() and _same_name(o.name, name)
            for o in others):
        problems.append(ScheduleError(
            f"A milestone called \"{name}\" already exists in {m.project_code}.", "name"))

    if m.start is None or m.end is None:
        problems.append(ScheduleError("Give the milestone a start and an end date.", "dates"))
    elif m.end < m.start:
        problems.append(ScheduleError(
            "The end date is before the start date, so this milestone would finish "
            "before it began.", "end"))

    if m.depends_on_id:
        parent = by_id.get(m.depends_on_id)
        if m.id is not None and m.depends_on_id == m.id:
            problems.append(ScheduleError("A milestone cannot depend on itself.", "depends"))
        elif parent is None:
            problems.append(ScheduleError(
                "The milestone it depends on no longer exists. Choose another, or none.",
                "depends"))
        elif parent.project_code.casefold() != m.project_code.casefold():
            # Cross-project dependencies are refused (FIX_PLAN): the timeline
            # groups by project, and a shift of one project silently moving
            # another's work is exactly what went wrong.
            problems.append(ScheduleError(
                "That dependency is in another project. A milestone can only wait on "
                "milestones of its own project.", "depends"))
        else:
            if m.id is not None and _creates_cycle(m.id, m.depends_on_id, by_id):
                problems.append(ScheduleError(
                    f"\"{parent.name}\" already waits on this milestone, so it cannot also "
                    "come first.", "depends"))
            elif parent.end is not None and m.start is not None and m.start <= parent.end:
                problems.append(ScheduleError(
                    f"It starts before \"{parent.name}\" ends ({format_date(parent.end)}). "
                    "Start it after that, or choose another dependency.", "start"))

    # The other direction: moving a milestone to another project would leave
    # whatever waits on it depending across projects.
    if m.id is not None and m.project_code:
        waiting = [o for o in others if o.depends_on_id == m.id
                   and o.project_code.casefold() != m.project_code.casefold()]
        if waiting:
            names = ", ".join(f"\"{o.name}\"" for o in waiting[:3]) + (" …" if len(waiting) > 3 else "")
            problems.append(ScheduleError(
                f"{names} in {waiting[0].project_code} {'waits' if len(waiting) == 1 else 'wait'} on "
                "this milestone, so it cannot move to another project. Change "
                f"{'its' if len(waiting) == 1 else 'their'} dependency first.", "project"))
    return problems


def _creates_cycle(milestone_id: int, parent_id: int, by_id: Dict[int, Milestone]) -> bool:
    """Would making milestone_id wait on parent_id close a loop?"""
    seen = set()
    cursor = parent_id
    while cursor is not None and cursor not in seen:
        if cursor == milestone_id:
            return True
        seen.add(cursor)
        parent = by_id.get(cursor)
        cursor = parent.depends_on_id if parent else None
    return False


def completion_warning(m: Milestone, by_id: Dict[int, Milestone]) -> str:
    """
    Marking this Completed while what it waits on is still open: the sentence
    to confirm, or '' when there is nothing to warn about.
    """
    parent = by_id.get(m.depends_on_id) if m.depends_on_id else None
    if parent is None or not parent.is_open:
        return ""
    return f"\"{m.name}\" waits on \"{parent.name}\", which is not complete yet."


# ------------------------------------------------------------------ shifting

WORKING_DAYS = "working"
CALENDAR_DAYS = "calendar"
PUSH = "push"          # dependents move only as far as they have to
SAME = "same"          # everything moves by the same amount


@dataclass
class Move:
    id: int
    name: str
    old_start: date
    old_end: date
    new_start: date
    new_end: date

    @property
    def changed(self) -> bool:
        return (self.old_start, self.old_end) != (self.new_start, self.new_end)


@dataclass
class ShiftPlan:
    root_id: int
    delta: int
    unit: str = WORKING_DAYS
    mode: str = PUSH
    root_to: Optional[Tuple[date, date]] = None     # exact new dates (a dragged bar)
    moves: List[Move] = field(default_factory=list)
    skipped: List[Tuple[int, str, str]] = field(default_factory=list)      # id, name, reason
    conflicts: List[Tuple[int, str, str]] = field(default_factory=list)    # id, name, what

    @property
    def moved(self) -> List[Move]:
        return [m for m in self.moves if m.changed]

    def changes(self) -> List[Tuple[int, date, date]]:
        return [(m.id, m.new_start, m.new_end) for m in self.moved]

    def message(self, root_name: str = "", preview: bool = False) -> str:
        """
        'Moved "Weekend delivery" and 1 dependent milestone 1 working day later.'
        Built from what will really happen; 'Success' was shown when nothing
        had moved. preview=True says it in the future tense, for the dialog.
        """
        moved = self.moved
        verb = "Moves" if preview else "Moved"
        unit_word = "working day" if self.unit == WORKING_DAYS else "day"
        amount = plural(abs(self.delta), unit_word)
        direction = "later" if self.delta > 0 else "earlier"
        if not moved:
            text = "Nothing would move" if preview else "Nothing moved"
        else:
            root_move = next((m for m in moved if m.id == self.root_id), None)
            root_moved = root_move is not None
            dependents = len(moved) - (1 if root_moved else 0)
            followers = plural(dependents, "dependent milestone")
            if root_moved and self.root_to is not None:
                text = _dragged_text(root_move, root_name, verb)
                if dependents:
                    text += f"; {followers} {'move' if preview else 'moved'} to follow it"
            elif self.mode == SAME:
                if root_moved:
                    text = f"{verb} \"{root_name or moved[0].name}\""
                    if dependents:
                        text += f" and {followers}"
                else:
                    text = f"{verb} {followers}"
                text += f" {amount} {direction}"
            elif root_moved:
                text = f"{verb} \"{root_name or moved[0].name}\" {amount} {direction}"
                if dependents:
                    text += f"; {followers} {'move' if preview else 'moved'} to follow it"
            else:
                text = f"{verb} {followers} to follow it"
        if self.skipped:
            reasons = "; ".join(f"\"{name}\" ({why})" for _id, name, why in self.skipped[:5])
            more = f" and {len(self.skipped) - 5} more" if len(self.skipped) > 5 else ""
            text += f". {'Staying' if preview else 'Not moved'}: {reasons}{more}"
        return text + "."


def _dragged_text(move: Move, name: str, verb: str) -> str:
    """
    A bar dragged or stretched on the timeline, in calendar days (what the
    hand did): 'Moves "Comp" to 2 Oct – 24 Oct 2026 (7 days later)', or
    'Moves the end of "Comp" to 14 Oct 2026 (3 days earlier)' when only one
    edge moved.
    """
    name = name or move.name
    if move.new_start == move.old_start:
        what, old, new = "the end of ", move.old_end, move.new_end
    elif move.new_end == move.old_end:
        what, old, new = "the start of ", move.old_start, move.new_start
    else:
        what, old, new = "", move.old_start, move.new_start
    days = (new - old).days
    where = format_date(new) if what else format_range(move.new_start, move.new_end)
    return (f"{verb} {what}\"{name}\" to {where} "
            f"({plural(abs(days), 'day')} {'later' if days > 0 else 'earlier'})")


def _children_map(milestones: Iterable[Milestone]) -> Dict[int, List[Milestone]]:
    children: Dict[int, List[Milestone]] = defaultdict(list)
    for m in milestones:
        if m.depends_on_id is not None:
            children[m.depends_on_id].append(m)
    for kids in children.values():
        kids.sort(key=lambda k: (k.start or date.max, k.id or 0))
    return children


def plan_shift(milestones: Sequence[Milestone], root_id: int, delta: int, *,
               unit: str = WORKING_DAYS, mode: str = PUSH,
               include_completed: bool = False,
               calendar: Optional[WorkCalendar] = None,
               root_to: Optional[Tuple[date, date]] = None) -> ShiftPlan:
    """
    What shifting root_id by delta would do, without doing it.

    Walks the dependents breadth-first over one in-memory list (no recursion,
    no query per milestone - a 5,000 long chain plans in milliseconds).

    unit   WORKING_DAYS: weekends and studio holidays are stepped over
           CALENDAR_DAYS: every day counts
    mode   PUSH: the milestone moves by delta; anything waiting on it moves
           only as far as it must (to start the working day after its
           dependency ends) - slack is kept
           SAME: the milestone and everything after it move by delta
    Completed and cancelled milestones stay where they are unless
    include_completed. Milestones without readable dates are skipped and
    reported. root_to gives the milestone exact new dates instead (a bar
    dragged or stretched on the timeline); what follows is then pushed as
    above. Conflicts (something starting on or before the end of what it
    waits on) are reported, not silently written.
    """
    calendar = calendar or WorkCalendar()
    plan = ShiftPlan(root_id=root_id, delta=int(delta), unit=unit, mode=mode, root_to=root_to)
    by_id = index(milestones)
    root = by_id.get(root_id)
    if root is None:
        plan.skipped.append((root_id, f"#{root_id}", "no longer exists"))
        return plan
    children = _children_map(milestones)

    def shift_date(day: date, amount: int) -> date:
        if unit == CALENDAR_DAYS:
            return day + timedelta(days=amount)
        return calendar.add_working_days(day, amount)

    def length_working(m: Milestone) -> int:
        return max(calendar.working_days(m.start, m.end), 1)

    new_dates: Dict[int, Tuple[date, date]] = {}
    order: List[int] = []
    visited = {root_id}
    queue = deque([root_id])
    while queue:
        current_id = queue.popleft()
        m = by_id[current_id]
        order.append(current_id)
        for child in children.get(current_id, ()):
            if child.id is not None and child.id not in visited:
                visited.add(child.id)
                queue.append(child.id)

    for current_id in order:
        m = by_id[current_id]
        if not m.has_dates:
            plan.skipped.append((m.id, m.name, "no dates"))
            continue
        if not m.is_open and not include_completed:
            plan.skipped.append((m.id, m.name, f"{m.status.lower()} - kept where it is"))
            continue
        if current_id == root_id and root_to is not None:
            new_start, new_end = root_to
            if (new_start, new_end) == (m.start, m.end):
                continue
        elif current_id == root_id or mode == SAME:
            if not plan.delta:
                continue
            new_start, new_end = shift_date(m.start, plan.delta), shift_date(m.end, plan.delta)
        else:
            parent = by_id.get(m.depends_on_id)
            parent_end = new_dates.get(parent.id, (None, parent.end))[1] if parent else None
            if parent_end is None:
                continue
            if unit == CALENDAR_DAYS:
                earliest = parent_end + timedelta(days=1)
            else:
                earliest = calendar.add_working_days(parent_end, 1)
            if m.start > parent_end:
                continue                      # already after it: slack kept
            if unit == CALENDAR_DAYS:
                offset = (earliest - m.start).days
                new_start, new_end = earliest, m.end + timedelta(days=offset)
            else:
                new_start = earliest
                new_end = calendar.add_working_days(new_start, length_working(m) - 1)
        new_dates[current_id] = (new_start, new_end)
        plan.moves.append(Move(m.id, m.name, m.start, m.end, new_start, new_end))

    # Conflicts in the schedule as it would be: everything moved, against
    # what it waits on; and everything waiting on something moved.
    def dates_of(x: Milestone):
        return new_dates.get(x.id, (x.start, x.end))

    affected = set(new_dates)
    to_check = set(affected)
    for moved_id in affected:
        for child in children.get(moved_id, ()):
            to_check.add(child.id)
    for check_id in sorted(i for i in to_check if i is not None):
        m = by_id.get(check_id)
        if m is None or not m.is_open or not m.depends_on_id:
            continue
        parent = by_id.get(m.depends_on_id)
        if parent is None:
            continue
        start, _end = dates_of(m)
        _pstart, pend = dates_of(parent)
        if start is not None and pend is not None and start <= pend:
            plan.conflicts.append((m.id, m.name,
                                   f"would start {format_date(start)}, before \"{parent.name}\" "
                                   f"ends {format_date(pend)}"))
    return plan


# ------------------------------------------------------------------ summary

@dataclass
class ScheduleSummary:
    projects: int = 0
    scheduled: int = 0
    in_progress: int = 0
    paused: int = 0          # On Hold + Blocked
    overdue: int = 0
    completed: int = 0
    cancelled: int = 0
    total: int = 0


def summary(milestones: Iterable[Milestone], today: Optional[date] = None) -> ScheduleSummary:
    """
    The cards, counted from the status column. An overdue milestone counts as
    Overdue only - it used to be inside 'In Progress' with every Scheduled, On
    Hold and blank row. A blank status is work not started: Scheduled.

    'Projects' is projects with open milestones in what is shown (the studio's
    active-project count belongs on the dashboard). Milestones of archived
    projects count in the total only.
    """
    today = today or date.today()
    out = ScheduleSummary()
    projects = set()
    for m in milestones:
        out.total += 1
        if m.archived:
            continue           # shown on request, but not the studio's work any more
        status = normalise_status(m.status)
        if status == COMPLETED:
            out.completed += 1
            continue
        if status == CANCELLED:
            out.cancelled += 1
            continue
        if m.project_code:
            projects.add(m.project_code.casefold())
        if is_overdue(m, today):
            out.overdue += 1
        elif status == IN_PROGRESS:
            out.in_progress += 1
        elif status in (ON_HOLD, BLOCKED):
            out.paused += 1
        else:
            out.scheduled += 1
    out.projects = len(projects)
    return out


# ------------------------------------------------------------------ timeline

ZOOM_DAY = "Day"
ZOOM_WEEK = "Week"
ZOOM_MONTH = "Month"
ZOOMS = OrderedDict(((ZOOM_DAY, 30.0), (ZOOM_WEEK, 10.0), (ZOOM_MONTH, 3.0)))   # px per day


@dataclass(frozen=True)
class TimeScale:
    """Dates to x positions and back, for one zoom."""
    origin: date
    px_per_day: float

    def x(self, day: date) -> float:
        return (day - self.origin).days * self.px_per_day

    def x_end(self, day: date) -> float:
        """The right edge of a day (a bar ending on day covers all of it)."""
        return self.x(day) + self.px_per_day

    def width(self, start: date, end: date) -> float:
        return max(self.x_end(end) - self.x(start), self.px_per_day)


def timeline_range(milestones: Iterable[Milestone], today: Optional[date] = None,
                   pad_days: int = 7) -> Tuple[date, date]:
    """First and last day the timeline draws: every bar, today, and a margin."""
    today = today or date.today()
    days = [today]
    for m in milestones:
        if m.start:
            days.append(m.start)
        if m.end:
            days.append(m.end)
    first, last = min(days), max(days)
    # Start on a Monday so weeks line up with the ruler.
    first = first - timedelta(days=pad_days)
    first -= timedelta(days=first.weekday())
    return first, last + timedelta(days=pad_days)


def ruler_ticks(first: date, last: date, zoom: str) -> List[Tuple[date, str, bool]]:
    """
    (day, label, major) marks for the ruler. Day zoom: every day, Mondays
    major. Week: every Monday, month starts major. Month: every month start.
    """
    ticks: List[Tuple[date, str, bool]] = []
    cursor = first
    while cursor <= last:
        if zoom == ZOOM_DAY:
            # The day number only: 'We 26' is wider than a 30 px day and ran
            # into its neighbours. The weekday is in each day's tooltip.
            ticks.append((cursor, str(cursor.day), cursor.weekday() == 0))
        elif zoom == ZOOM_WEEK:
            if cursor.weekday() == 0:
                ticks.append((cursor, f"{cursor.day} {cursor.strftime('%b')}", cursor.day <= 7))
        else:
            if cursor.day == 1:
                ticks.append((cursor, cursor.strftime("%b %Y"), cursor.month == 1))
        cursor += timedelta(days=1)
    return ticks


def month_bands(first: date, last: date) -> List[Tuple[date, date, str]]:
    """(start, end, 'Oct 2026') for each month the timeline crosses - the ruler's top line."""
    bands = []
    cursor = first
    while cursor <= last:
        month_end = (date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
                     - timedelta(days=1))
        bands.append((cursor, min(month_end, last), cursor.strftime("%b %Y")))
        cursor = month_end + timedelta(days=1)
    return bands


def pack_rows(intervals: Sequence[Tuple[date, date]]) -> List[int]:
    """
    Sub-row for each interval so overlapping bars do not sit on top of each
    other (greedy, in start order). Returns one row number per interval.
    """
    order = sorted(range(len(intervals)), key=lambda i: (intervals[i][0], intervals[i][1]))
    row_ends: List[date] = []
    rows = [0] * len(intervals)
    for i in order:
        start, end = intervals[i]
        for r, last_end in enumerate(row_ends):
            if start > last_end:
                rows[i] = r
                row_ends[r] = end
                break
        else:
            rows[i] = len(row_ends)
            row_ends.append(end)
    return rows


def arrow_route(from_right: float, from_y: float, to_left: float, to_y: float,
                gap: float = 8.0) -> List[Tuple[float, float]]:
    """
    A finish-to-start arrow from the end of one bar to the start of another:
    out to the right, down/up, and in. When the second bar starts before the
    first ends (a violated dependency) the route doubles back.
    """
    out_x = from_right + gap
    in_x = to_left - gap
    if in_x >= out_x:
        return [(from_right, from_y), (out_x, from_y), (out_x, to_y), (to_left, to_y)]
    mid_y = (from_y + to_y) / 2.0
    return [(from_right, from_y), (out_x, from_y), (out_x, mid_y), (in_x, mid_y),
            (in_x, to_y), (to_left, to_y)]


# ------------------------------------------------------------------ people

@dataclass
class WorkItem:
    person: str                  # username (or the name stored, when unknown)
    label: str
    start: date
    end: date
    days: Decimal                # effort: bid days / effort days
    kind: str = "task"           # "task" (dashboard) | "milestone"
    project_code: str = ""
    ref: object = None           # milestone id / (project, shot, department)

    def load_per_day(self, calendar: WorkCalendar) -> Decimal:
        span = calendar.working_days(self.start, self.end)
        if span <= 0 or not self.days:
            return Decimal(0)
        return Decimal(self.days) / Decimal(span)


@dataclass
class Away:
    person: str
    start: date
    end: date
    reason: str = "Leave"


@dataclass
class PeopleConflict:
    person: str
    kind: str          # "leave" | "overload"
    start: date
    end: date
    text: str


@dataclass
class PeoplePlan:
    people: List[str] = field(default_factory=list)
    items: Dict[str, List[WorkItem]] = field(default_factory=dict)
    away: Dict[str, List[Away]] = field(default_factory=dict)
    conflicts: List[PeopleConflict] = field(default_factory=list)
    skipped_tasks: int = 0           # dashboard work with no target date or no bid days


def task_items(tasks: Iterable[dict], calendar: WorkCalendar,
               resolve=lambda name: name) -> Tuple[List[WorkItem], int]:
    """
    Dashboard assignments as bars: each task ends on its target date and spans
    its bid days in working days. Read-only - the dashboard owns these records.
    Returns (items, skipped) where skipped counts tasks that cannot be placed.
    """
    items: List[WorkItem] = []
    skipped = 0
    for t in tasks:
        artist = str(t.get("artist") or t.get("artist_name") or "").strip()
        if not artist:
            continue
        # The dashboard's own words for finished and cut work (shot_status).
        if shot_status.is_done(t.get("status")) or shot_status.is_omitted(t.get("status")):
            continue
        end = parse_date(t.get("target_date") or t.get("target"))
        days = _decimal(t.get("bid_days")) or Decimal(0)
        if end is None or days <= 0:
            skipped += 1
            continue
        span = max(int(math.ceil(float(days))), 1)
        end = end if calendar.is_working(end) else calendar.add_working_days(end, -1)
        start = calendar.add_working_days(end, -(span - 1)) if span > 1 else end
        shot = str(t.get("shot_name") or "").strip()
        department = str(t.get("department") or "").strip()
        label = " ".join(x for x in (shot, department) if x) or "Shot work"
        items.append(WorkItem(person=resolve(artist), label=label, start=start, end=end,
                              days=days, kind="task",
                              project_code=str(t.get("project_code") or ""),
                              ref=(t.get("project_code"), shot, department)))
    return items, skipped


def milestone_items(milestones: Iterable[Milestone]) -> List[WorkItem]:
    """Open milestones that have an owner, as bars on that person's lane."""
    out = []
    for m in milestones:
        if not m.owner or not m.has_dates or m.dates_reversed or not m.is_open:
            continue
        out.append(WorkItem(person=m.owner, label=m.name, start=m.start, end=m.end,
                            days=m.effort_days or Decimal(0), kind="milestone",
                            project_code=m.project_code, ref=m.id))
    return out


def _ranges(days: List[date]) -> List[Tuple[date, date]]:
    """Consecutive runs: [1,2,3,7] -> [(1,3),(7,7)] (by calendar day)."""
    runs: List[Tuple[date, date]] = []
    for d in sorted(days):
        if runs and (d - runs[-1][1]).days <= 1:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def people_plan(items: Iterable[WorkItem], away: Iterable[Away],
                calendar: WorkCalendar, people: Iterable[str] = ()) -> PeoplePlan:
    """
    One lane per person: their work, their approved leave, and the problems -
    work on a day they are away, and more than one day of work booked on a
    single working day (over-allocation). Pending leave is not passed in: it
    has not been decided, as the Help says.
    """
    plan = PeoplePlan()
    by_person: Dict[str, List[WorkItem]] = defaultdict(list)
    for item in items:
        by_person[item.person].append(item)
    away_by: Dict[str, List[Away]] = defaultdict(list)
    for a in away:
        away_by[a.person].append(a)
    names = set(by_person) | set(p for p in people if p)
    names |= {p for p in away_by if p in by_person or p in set(people)}
    plan.people = sorted(names, key=lambda p: p.casefold())
    for person in plan.people:
        work = sorted(by_person.get(person, []), key=lambda i: (i.start, i.end, i.label))
        plan.items[person] = work
        plan.away[person] = sorted(away_by.get(person, []), key=lambda a: a.start)

        # Work on a day the person is away.
        away_days: Set[date] = set()
        for a in plan.away[person]:
            away_days.update(calendar.span_days(a.start, a.end))
        for item in work:
            clash = [d for d in calendar.span_days(item.start, item.end) if d in away_days]
            for start, end in _ranges(clash):
                plan.conflicts.append(PeopleConflict(
                    person, "leave", start, end,
                    f"{item.label} is planned while they are away ({format_range(start, end)})"))

        # More than a day of work booked on one working day.
        load: Dict[date, Decimal] = defaultdict(Decimal)
        for item in work:
            per_day = item.load_per_day(calendar)
            if per_day <= 0:
                continue
            for d in calendar.span_days(item.start, item.end):
                load[d] += per_day
        heavy = [d for d, value in load.items() if value > Decimal("1.0001")]
        for start, end in _ranges(heavy):
            peak = max(load[d] for d in heavy if start <= d <= end)
            plan.conflicts.append(PeopleConflict(
                person, "overload", start, end,
                f"{peak.quantize(Decimal('0.1'))} days of work booked per day "
                f"({format_range(start, end)})"))
    plan.conflicts.sort(key=lambda c: (c.person.casefold(), c.start, c.kind))
    return plan


def leave_overlap(owner: str, start: Optional[date], end: Optional[date],
                  away: Iterable[Away], calendar: Optional[WorkCalendar] = None) -> List[Away]:
    """Approved leave of owner that falls inside start..end (for the dialog warning)."""
    if not owner or start is None or end is None:
        return []
    return [a for a in away
            if a.person.casefold() == owner.casefold() and a.start <= end and a.end >= start]
