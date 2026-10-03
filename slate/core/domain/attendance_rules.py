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
    arrived_late(...)       the one late count: hero, tables and export alike
    month_summary(...)      a person's month, counted once for every screen
    expected_window(...)    joining date to last day: nobody is absent outside it
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


def sessions_problem(sessions, overnight: bool = False) -> str:
    """
    Why a day's typed sessions [(in, out), ...] cannot be saved, or ''.

    One check for the Edit punch dialog and for CentralAttendance.update_record.
    Only the last session may be open, sessions may not overlap, and 'Ends
    next day' is only for a last out time that is not after its in time - ticked
    on a 09:00-18:00 day it showed '18:00 (+1)' while the hours said 9.
    """
    sessions = [(str(a or "").strip(), str(b or "").strip()) for a, b in (sessions or [])]
    if not any(a or b for a, b in sessions):
        return "Enter an in time, an out time or both."
    many = len(sessions) > 1
    previous_out = None
    for n, (start, end) in enumerate(sessions, 1):
        last = n == len(sessions)
        t_in, t_out = parse_time(start), parse_time(end)
        if many and t_in is None:
            return "Session %d needs an in time." % n
        if not last and t_out is None:
            return "Session %d needs an out time - only the last session can still be open." % n
        if previous_out is not None and t_in is not None and t_in < previous_out:
            return "Session %d starts before session %d ends." % (n, n - 1)
        if t_in is not None and t_out is not None:
            if last and overnight:
                if t_out >= t_in:
                    return ("Untick 'Ends next day': the out time is not before the in "
                            "time, so the shift did not run past midnight.")
            elif t_out <= t_in:
                if not last:
                    return "Session %d ends before it starts." % n
                return ("The out time is before the in time. Tick 'Ends next day' if the "
                        "shift really ran past midnight.")
        previous_out = t_out
    if overnight and not (parse_time(sessions[-1][0]) and parse_time(sessions[-1][1])):
        return "'Ends next day' needs both an in and an out time."
    return ""


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


def arrived_late(entry, day: date, holidays=None, leave=None, rules=None) -> bool:
    """
    Whether this day counts as a late arrival - the one count the hero card,
    both tables and the export all use. They used to disagree: the hero
    counted a late arrival on a day the automatic punch-out closed, the grid
    and the export did not.

    A day of leave is never late: somebody on a first-half leave who comes in
    after lunch is on time, and a punch on a full day of leave is a conflict
    for HR, not a late mark. Second-half leave starts after the morning, so
    the morning arrival is judged as usual.
    """
    sessions = sessions_of(entry)
    if not sessions:
        return False
    if leave and leave.get("half") != lp.half_day_label("second"):
        return False
    return is_late(day, sessions[0][0], holidays, rules)


def _as_day(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def expected_window(record=None, service: bool = False) -> tuple:
    """
    (first, last): the days somebody is expected in - from their joining
    date to their last day (or the day the account was switched off).

    Outside it nobody is absent: the grid knew about joining dates but not
    last days, and the export knew about neither, so a leaver was absent for
    every day after leaving and a new joiner for every day before joining on
    the sheet payroll reads. A service account (admin, tester) is never
    expected in.
    """
    if service:
        return date.max, date.min
    record = record or {}
    first = _as_day(record.get("joined_on")) or date.min
    ends = [d for d in (_as_day(record.get("last_day")), _as_day(record.get("deactivated_on"))) if d]
    return first, (min(ends) if ends else date.max)


# -------------------------------------------------------------------- state

def day_state(entry, day: date, today: date = None, holidays=None, leave=None,
              rules=None, expected=None) -> str:
    """
    What one day is, as one state name (see STATE_LABELS).

    leave: the approved leave on that day ({"type": .., "days": ..}) or None.
    expected: (first, last) from expected_window; a day outside it with no
    punch is nothing, not an absence.
    The order matters: a punch beats leave (the conflict is flagged
    separately), leave beats a holiday, a missing punch is shown as missing
    rather than as working - on a day off too: a forgotten punch-out on a
    Sunday read as a worked day with 0 hours and no flag.
    """
    today = today or date.today()
    entry = entry or {}
    has_in = bool(entry.get("in")) or any(s for s, _ in sessions_of(entry))
    has_out = bool(entry.get("out"))
    working_day = lp.is_working_day(day, holidays, rules)

    if has_in:
        if _open_session(entry):
            if day == today:
                return WORKING
            if day < today and not entry.get("auto_logout"):
                return MISSING_OUT
        if entry.get("missing_punch_out"):
            return MISSING_OUT
        if entry.get("auto_logout"):
            return AUTO
        if not working_day:
            return WORKED_OFF
        if arrived_late(entry, day, holidays, leave, rules):
            return LATE
        if is_short(entry, day, today, rules) and not leave:
            return SHORT
        return PRESENT
    if has_out:
        return MISSING_IN
    if expected and not (expected[0] <= day <= expected[1]):
        return NONE
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


# -------------------------------------------------------------------- month

def month_summary(log, year: int, month: int, holidays=None, leave=None, expected=None,
                  now: datetime = None, today: date = None, rules=None) -> dict:
    """
    One person's month, as every screen counts it: the hero card, the
    personal table, the team grid and the Excel export all read this, so the
    sheet payroll reads cannot disagree with the grid HR checked.

    log: {"01": entry, ...}; leave: {date: {"type", "half", "days"}};
    expected: (first, last) from expected_window.
    Returns {"days": [{"day", "entry", "state", "hours", "late", "leave"}],
    "present", "late", "absent", "leave", "missing", "hours", "wfh", "open_today"}.
    """
    import calendar
    now = now or datetime.now()
    today = today or now.date()
    leave = leave or {}
    out = {"days": [], "present": 0, "late": 0, "absent": 0, "leave": 0.0,
           "missing": 0, "hours": 0.0, "wfh": 0, "open_today": False}
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        day = date(year, month, d)
        entry = log.get(f"{d:02d}") or {}
        day_leave = leave.get(day)
        state = day_state(entry, day, today, holidays, day_leave, rules, expected)
        hours = day_hours(entry, now, open_counts=(day == today))
        late = arrived_late(entry, day, holidays, day_leave, rules)
        sessions = sessions_of(entry)
        if sessions:
            out["present"] += 1
            out["wfh"] += bool(entry.get("wfh"))
            if day == today and sessions[-1][1] is None:
                out["open_today"] = True
        out["late"] += late
        out["absent"] += state == ABSENT
        out["missing"] += state in (MISSING_OUT, MISSING_IN)
        if day_leave and not (expected and not expected[0] <= day <= expected[1]):
            out["leave"] += float(day_leave.get("days") or 1.0)
        out["hours"] += hours
        out["days"].append({"day": day, "entry": entry, "state": state, "hours": hours,
                            "late": late, "leave": day_leave})
    out["hours"] = round(out["hours"], 2)
    return out


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
