"""
What an attendance day means: hours, late, and the state it is in.

Four screens judged the same punches four ways. The hero card skipped Sundays
and holidays when counting late arrivals, the personal table did not, the team
grid did not, and the Excel export had its own copy again - so one person had
three different late counts on one screen. The export also worked out hours
without the overnight rule the grid used, so a 20:00-05:30 shift came out at
-14.5 hours on the sheet payroll reads.

These are the rules, once, with no Qt and no database, so the tab, the grid,
the export and the comp-off review all give the same answer:

    day_hours(entry)        hours worked, overnight and second sessions included
    is_late(day, in_time)   a late arrival on a day somebody was expected in
    day_state(...)          what one day is: present, late, working, missing
                            punch-out, missing punch-in, on leave, holiday,
                            weekly off, absent, short day, or not yet
    calculate_streak(...)   on-time days in a row, across month ends, with
                            leave and days off neutral

An entry is the dict CentralAttendance hands the screens:
{"in": "09:30", "out": "18:30", "wfh": bool, "auto_logout": bool,
 "missing_punch_out": bool, "sessions": [{"in": .., "out": ..}, ...], ...}
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from . import leave_policy as lp


# ------------------------------------------------------------------ states
#
# One name per state. The tab maps each to one colour and one label, and the
# legend is built from the same map - so the legend can never again describe
# colours the tables do not use.

PRESENT = "present"
LATE = "late"
WORKING = "working"                 # today, punched in, not out yet
MISSING_OUT = "missing_out"         # a past day with an in and no out
MISSING_IN = "missing_in"           # an out with no in
AUTO = "auto"                       # closed by the automatic punch-out
LEAVE = "leave"
HOLIDAY = "holiday"
WEEKLY_OFF = "weekly_off"
WORKED_OFF = "worked_off"           # punched in on a holiday or weekly off
ABSENT = "absent"                   # a past working day with nothing at all
SHORT = "short"                     # fewer hours than a standard day
FUTURE = "future"
NONE = ""

STATE_LABELS = {
    PRESENT: "Present",
    LATE: "Late",
    WORKING: "Working",
    MISSING_OUT: "Missing punch-out",
    MISSING_IN: "Missing punch-in",
    AUTO: "Auto punch-out",
    LEAVE: "On leave",
    HOLIDAY: "Holiday",
    WEEKLY_OFF: "Weekly off",
    WORKED_OFF: "Worked a day off",
    ABSENT: "Absent",
    SHORT: "Short day",
    FUTURE: "",
    NONE: "",
}


# ------------------------------------------------------------------- times

def parse_time(value):
    """A clock time from 'HH:MM', 'HH:MM:SS', a time or a datetime. None if not one."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text[:8] if fmt == "%H:%M:%S" else text[:5], fmt).time()
        except ValueError:
            continue
    return None


def hhmm(value) -> str:
    """'09:30' from any time value, '' for none."""
    parsed = parse_time(value)
    return parsed.strftime("%H:%M") if parsed else ""


def session_hours(in_time, out_time=None, now: datetime = None) -> float:
    """
    Hours between two clock times. An out time earlier than the in time means
    the shift crossed midnight. No out time: up to now (an open session).
    """
    start = parse_time(in_time)
    if start is None:
        return 0.0
    end = parse_time(out_time)
    if end is None:
        if now is None:
            return 0.0
        end = now.time()
    a = datetime.combine(date(2000, 1, 1), start)
    b = datetime.combine(date(2000, 1, 1), end)
    if b < a:
        b += timedelta(days=1)
    return max(0.0, (b - a).total_seconds() / 3600.0)


def sessions_of(entry) -> list:
    """
    The (in, out) sessions of a day.

    A day normally has one. Punching in again after punching out starts
    another (people punch out by mistake, or leave and come back), and those
    are kept in the day's metadata under "sessions". out is None for a
    session still open.
    """
    if not entry:
        return []
    stored = entry.get("sessions") or []
    out = []
    for s in stored:
        if isinstance(s, dict) and s.get("in"):
            out.append((s.get("in"), s.get("out") or None))
    if out:
        return out
    if entry.get("in"):
        return [(entry.get("in"), entry.get("out") or None)]
    return []


def day_hours(entry, now: datetime = None, open_counts: bool = False) -> float:
    """
    Hours worked on a day: every session, overnight ones included.

    open_counts: count a session still open up to `now` (today's running
    session). A past day's open session is a missing punch-out and counts
    nothing - inventing hours for it is how a forgotten punch-out became a
    23-hour shift.
    """
    total = 0.0
    for start, end in sessions_of(entry):
        if end is None:
            if open_counts and now is not None:
                total += session_hours(start, None, now)
            continue
        total += session_hours(start, end)
    return round(total, 4)


# -------------------------------------------------------------------- late

def late_after(rules=None) -> time:
    hour, minute = lp.late_cutoff(rules)
    return time(hour, minute)


def is_late(day: date, in_time, holidays=None, rules=None) -> bool:
    """
    A late arrival: after the studio's cutoff, on a day somebody was expected
    in. Coming in at noon on a Sunday or a public holiday is not late - it is
    working a day off.
    """
    start = parse_time(in_time)
    if start is None or day is None:
        return False
    if not lp.is_working_day(day, holidays, rules):
        return False
    return start > late_after(rules)


# -------------------------------------------------------------------- state

def day_state(entry, day: date, today: date = None, holidays=None, leave=None,
              rules=None) -> str:
    """
    What one day is, as one state name (see STATE_LABELS).

    leave: the approved leave on that day ({"type": .., "days": ..}) or None.
    The order matters: a punch beats leave (the conflict is flagged
    separately), leave beats a holiday, a missing punch is shown as missing
    rather than as working.
    """
    today = today or date.today()
    entry = entry or {}
    has_in = bool(entry.get("in")) or any(s for s, _ in sessions_of(entry))
    has_out = bool(entry.get("out"))
    working_day = lp.is_working_day(day, holidays, rules)

    if has_in:
        if not working_day:
            if day == today and not has_out and _open_session(entry):
                return WORKING
            return WORKED_OFF
        if _open_session(entry):
            if day == today:
                return WORKING
            if day < today and not entry.get("auto_logout"):
                return MISSING_OUT
        if entry.get("missing_punch_out"):
            return MISSING_OUT
        if entry.get("auto_logout"):
            return AUTO
        if is_late(day, (sessions_of(entry) or [(entry.get("in"), None)])[0][0], holidays, rules):
            return LATE
        if is_short(entry, day, today, rules) and not leave:
            return SHORT
        return PRESENT
    if has_out:
        return MISSING_IN
    if leave:
        return LEAVE
    if day in set(holidays or ()):
        return HOLIDAY
    if lp.is_weekly_off(day, rules):
        return WEEKLY_OFF
    if day >= today:
        return FUTURE if day > today else NONE
    return ABSENT


def _open_session(entry) -> bool:
    sessions = sessions_of(entry)
    return bool(sessions) and sessions[-1][1] is None


def is_short(entry, day: date, today: date = None, rules=None) -> bool:
    """Fewer hours than a standard day, on a finished day with both punches."""
    today = today or date.today()
    if day >= today or not entry or _open_session(entry):
        return False
    hours = day_hours(entry)
    return 0 < hours < lp.standard_day_hours(rules)


# ------------------------------------------------------------------- streak

def calculate_streak(days: dict, today: date = None, holidays=None, leave_days=None,
                     rules=None, limit: int = 400) -> int:
    """
    On-time days in a row, counting back from yesterday.

    days: {date: entry} - any range; it is walked back across month ends (the
    streak used to reset on the 1st of every month). Weekly offs, holidays and
    approved leave are neutral: they neither count nor break it. An absence or
    a late arrival ends it.
    """
    today = today or date.today()
    holidays = set(holidays or ())
    leave_days = set(leave_days or ())
    streak = 0
    cursor = today - timedelta(days=1)
    for _ in range(limit):
        entry = days.get(cursor)
        start = None
        if entry:
            sessions = sessions_of(entry)
            start = sessions[0][0] if sessions else entry.get("in")
        if start:
            if not lp.is_working_day(cursor, holidays, rules):
                cursor -= timedelta(days=1)
                continue
            if is_late(cursor, start, holidays, rules):
                break
            streak += 1
        elif not lp.is_working_day(cursor, holidays, rules) or cursor in leave_days:
            pass
        else:
            break
        cursor -= timedelta(days=1)
    return streak
