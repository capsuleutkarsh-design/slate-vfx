"""
Production numbers, computed from the shots already on screen.

Every shot carries bid days per department and a target date. Nothing in the
software ever read them, so the questions a coordinator is asked every morning
- who is overloaded, what is late, how far through is this show - had to be
answered by eye.

This is pure computation over a list of shots: no database, no Qt, so it can be
tested directly and reused anywhere.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from slate.core.domain import shot_status
from slate.core.domain.departments import load_departments


# The one vocabulary (shot_status): finished, not started, and cut from the show.
DONE_STATUSES = set(shot_status.DONE_STATUSES)
NOT_STARTED_STATUSES = set(shot_status.NOT_STARTED_STATUSES)
OMITTED_STATUSES = set(shot_status.OMITTED_STATUSES)


def _parse_date(value) -> Optional[date]:
    """The shared date reader: ISO with or without a time, day-first, '3 Oct 2026'."""
    from slate.core.domain.dates import parse_date
    return parse_date(value)


def _is_done(status) -> bool:
    return shot_status.is_done(status)


def _is_not_started(status) -> bool:
    return shot_status.is_not_started(status)


def _is_omitted(status) -> bool:
    return shot_status.is_omitted(status)


@dataclass
class DepartmentLoad:
    key: str
    label: str
    shots: int = 0
    bid_days: float = 0.0
    not_started: int = 0
    in_progress: int = 0
    done: int = 0

    @property
    def outstanding_days(self) -> float:
        """Bid days on work that is not finished."""
        return round(self._outstanding, 2)

    _outstanding: float = field(default=0.0, repr=False)

    @property
    def percent_done(self) -> float:
        if not self.shots:
            return 0.0
        return round((self.done / self.shots) * 100, 1)


@dataclass
class ArtistLoad:
    name: str
    shots: int = 0
    bid_days: float = 0.0
    outstanding_days: float = 0.0
    departments: Dict[str, float] = field(default_factory=dict)


@dataclass
class LateShot:
    shot_name: str
    reel: str
    target: date
    days_late: int
    status: str
    artist: str = ""


@dataclass
class ProductionSummary:
    total_shots: int = 0
    status_counts: Dict[str, int] = field(default_factory=dict)
    departments: List[DepartmentLoad] = field(default_factory=list)
    artists: List[ArtistLoad] = field(default_factory=list)
    late: List[LateShot] = field(default_factory=list)
    due_soon: List[LateShot] = field(default_factory=list)
    unassigned: List[str] = field(default_factory=list)
    total_bid_days: float = 0.0
    outstanding_bid_days: float = 0.0
    # Cut from the show: listed in the status counts, left out of everything else.
    omitted: int = 0

    @property
    def percent_complete(self) -> float:
        if not self.total_shots:
            return 0.0
        done = sum(count for status, count in self.status_counts.items()
                   if _is_done(status) and not _is_omitted(status))
        return round((done / self.total_shots) * 100, 1)


def build_summary(shots: Iterable, today: Optional[date] = None,
                  due_within_days: int = 7) -> ProductionSummary:
    """
    Roll a list of shots up into the numbers production asks for.

    `today` is injectable so the result is stable in tests.
    """
    today = today or date.today()
    summary = ProductionSummary()

    everything = [s for s in (shots or []) if s is not None]
    # Omitted shots are not work: they used to count as outstanding, overdue,
    # and in the "% approved" denominator.
    shots = [s for s in everything if not _is_omitted(getattr(s, "status", ""))]
    summary.omitted = len(everything) - len(shots)
    summary.total_shots = len(shots)
    if not everything:
        return summary

    status_counts: Dict[str, int] = defaultdict(int)
    dept_rows: Dict[str, DepartmentLoad] = {
        dept.key: DepartmentLoad(key=dept.key, label=dept.name)
        for dept in load_departments()
    }
    # By the name as people mean it: 'Priya' and 'priya ' are one person.
    artist_rows: Dict[str, ArtistLoad] = {}
    spellings: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for shot in everything:
        if _is_omitted(getattr(shot, "status", "")):
            status_counts[shot_status.label(shot.status)] += 1

    for shot in shots:
        status = shot_status.label(getattr(shot, "status", ""))
        status_counts[status] += 1
        # A finished shot has no work left in any department, whatever a
        # department row still says.
        shot_done = _is_done(getattr(shot, "status", ""))

        # --- per-department work -------------------------------------
        assigned_anywhere = False
        for key, row in dept_rows.items():
            dept = shot.dept(key) if hasattr(shot, "dept") else None
            if dept is None:
                continue

            artist = str(getattr(dept, "artist", "") or "").strip()
            dept_status = getattr(dept, "status", "")
            try:
                bid = float(getattr(dept, "bid_days", 0) or 0)
            except (TypeError, ValueError):
                bid = 0.0

            # A department counts as "in play" if someone is on it, it has a
            # status, or days have been bid against it - and is not N/A or
            # omitted (that work is not going to happen).
            if not artist and not str(dept_status or "").strip() and bid <= 0:
                continue
            if _is_omitted(dept_status):
                continue

            assigned_anywhere = assigned_anywhere or bool(artist)
            row.shots += 1
            row.bid_days += bid

            dept_done = shot_done or _is_done(dept_status)
            if dept_done:
                row.done += 1
            elif _is_not_started(dept_status):
                row.not_started += 1
                row._outstanding += bid
            else:
                row.in_progress += 1
                row._outstanding += bid

            if artist:
                person = " ".join(artist.split()).casefold()
                spellings[person][" ".join(artist.split())] += 1
                entry = artist_rows.setdefault(person, ArtistLoad(name=artist))
                entry.shots += 1
                entry.bid_days += bid
                entry.departments[key] = entry.departments.get(key, 0.0) + bid
                if not dept_done:
                    entry.outstanding_days += bid

        # Work nobody is on - finished shots need nobody. Named with the reel:
        # SH010 may be in two reels.
        if not shot_done and not assigned_anywhere and not str(
                getattr(shot, "assigned_artist", "") or "").strip():
            reel = str(getattr(shot, "reel_episode", "") or "")
            name = getattr(shot, "shot_name", "")
            summary.unassigned.append(f"{reel} / {name}" if reel and name else name)

        # --- dates ----------------------------------------------------
        target = _parse_date(getattr(shot, "target", None))
        if target and not _is_done(status):
            delta = (today - target).days
            entry = LateShot(
                shot_name=getattr(shot, "shot_name", ""),
                reel=getattr(shot, "reel_episode", "") or "",
                target=target,
                days_late=delta,
                status=status,
                artist=str(getattr(shot, "assigned_artist", "") or ""),
            )
            if delta > 0:
                summary.late.append(entry)
            elif -delta <= due_within_days:
                summary.due_soon.append(entry)

    summary.status_counts = dict(sorted(status_counts.items(),
                                        key=lambda kv: shot_status.order_key(
                                            "" if kv[0] == shot_status.NO_STATUS else kv[0])))

    summary.departments = [row for row in dept_rows.values() if row.shots]
    summary.departments.sort(key=lambda r: (-r.bid_days, r.label))

    for row in dept_rows.values():
        summary.total_bid_days += row.bid_days
        summary.outstanding_bid_days += row._outstanding
    summary.total_bid_days = round(summary.total_bid_days, 2)
    summary.outstanding_bid_days = round(summary.outstanding_bid_days, 2)

    for person, entry in artist_rows.items():
        entry.name = max(spellings[person].items(), key=lambda kv: (kv[1], kv[0]))[0]
    summary.artists = sorted(artist_rows.values(),
                             key=lambda a: (-a.outstanding_days, a.name))
    for artist in summary.artists:
        artist.bid_days = round(artist.bid_days, 2)
        artist.outstanding_days = round(artist.outstanding_days, 2)

    summary.late.sort(key=lambda e: -e.days_late)
    summary.due_soon.sort(key=lambda e: e.target)
    summary.unassigned = [name for name in summary.unassigned if name]

    return summary
