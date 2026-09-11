"""Attendance computation helpers extracted from AttendanceTab."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta


def calculate_hours(in_time: str, out_time: str = "", now_ref: datetime | None = None) -> float:
    """Calculate worked hours from HH:MM strings."""
    if not in_time:
        return 0.0
    try:
        start_dt = datetime.strptime(in_time, "%H:%M")
        if out_time:
            end_dt = datetime.strptime(out_time, "%H:%M")
        else:
            ref = now_ref or datetime.now()
            end_dt = datetime.strptime(ref.strftime("%H:%M"), "%H:%M")

        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        return max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)
    except (ValueError, TypeError, AttributeError):
        return 0.0


def calculate_streak(
    user_log,
    year: int,
    month: int,
    cutoff_hour: int,
    cutoff_minute: int,
    now_ref: datetime | None = None,
) -> int:
    """Calculate consecutive non-late attendance streak in current month."""
    streak = 0
    now = now_ref or datetime.now()
    today = now.day
    for day in range(today - 1, 0, -1):
        key = f"{day:02d}"
        if key in user_log:
            entry = user_log[key]
            t_in = entry.get("in")
            if t_in:
                try:
                    h, m = map(int, t_in.split(":"))
                    if h < cutoff_hour or (h == cutoff_hour and m <= cutoff_minute):
                        streak += 1
                    else:
                        break
                except (ValueError, AttributeError) as exc:
                    logging.warning("Streak calculation error for day %s: %s", day, exc)
                    break
            else:
                dt = datetime(year, month, day)
                if dt.weekday() == 6:
                    continue
                break
        else:
            dt = datetime(year, month, day)
            if dt.weekday() == 6:
                continue
            break
    return streak

