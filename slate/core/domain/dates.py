"""
One way of showing dates and times, everywhere in Slate.

Every screen used to format dates for itself. A table printed the database's
ISO text (2026-10-03), the date picker next to it used the system locale
(03-10-2026), an edit dialog spelled the month out (3 October 2026), a
timestamp came out with microseconds (2026-09-17 23:02:49.236319), and the
calendar popup started its weeks on Sunday while the studio's weeks start on
Monday. Three formats in one dialog, for the same data.

The rules now:

    a date            3 Oct 2026          format_date(d)
    with its weekday  Sat 3 Oct 2026      format_date(d, weekday=True)
    a moment          17 Sep 2026, 23:02  format_datetime(dt)
    a time of day     23:02               format_time(t)
    how long ago      5 min ago           format_age(dt)
    weeks             start on Monday     WEEK_START, week_start(d)

Month names are written out here rather than taken from strftime, so the
format does not change with the Windows language a machine was set up in.

These return text for people. What goes into the database is still ISO
(date.isoformat()), and a table that sorts by a date column must sort by
date_sort_key(value), not by the text - "12 Sep" sorts before "3 Oct" as text.
The Qt side (date pickers, table cells) is in slate.gui.core.data_display.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional, Union

DateLike = Union[date, datetime, str, None]

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Monday, as datetime.weekday() counts (Monday = 0). The studio's week.
WEEK_START = 0

# The Qt display format that matches format_date(), for QDateEdit and friends.
QT_DATE_FORMAT = "d MMM yyyy"
QT_DATE_FORMAT_WEEKDAY = "ddd d MMM yyyy"
QT_DATETIME_FORMAT = "d MMM yyyy, HH:mm"
QT_TIME_FORMAT = "HH:mm"

# What a missing date reads as in a table, when the caller wants a mark.
MISSING = "—"  # an em dash


def as_date(value) -> Optional[date]:
    """
    A STORED date as a date, or None: a date, a datetime, or ISO text (what
    SQLite hands back). PostgreSQL returns date objects and SQLite returns
    text, so every comparison goes through this. ISO only, on purpose - a
    last day typed some other way must not quietly start counting.
    (parse_date below also reads what people type.)
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_date(value: DateLike) -> Optional[date]:
    """
    A date from whatever the database or a person handed over, or None.

    Accepts date / datetime objects, ISO text ('2026-10-03', with or without a
    time), the old picker's '03-10-2026' / '03/10/2026' (day first), and this
    module's own '3 Oct 2026' / 'Sat 3 Oct 2026'.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # ISO first - it is what the database stores.
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for sep in ("-", "/", "."):
        parts = text.split(sep)
        if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
            d, m, y = (int(p) for p in parts)
            if y < 100:
                y += 2000
            try:
                return date(y, m, d)
            except ValueError:
                return None
    words = text.replace(",", " ").split()
    if words and words[0][:3].title() in WEEKDAYS and len(words) == 4:
        words = words[1:]
    if len(words) == 3 and words[0].isdigit() and words[2].isdigit():
        month = words[1][:3].title()
        if month in MONTHS:
            try:
                return date(int(words[2]), MONTHS.index(month) + 1, int(words[0]))
            except ValueError:
                return None
    return None


def parse_datetime(value) -> Optional[datetime]:
    """A datetime from a datetime, a date (midnight) or ISO text, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    d = parse_date(text)
    return datetime(d.year, d.month, d.day) if d else None


def format_date(value: DateLike, *, weekday: bool = False, empty: str = "") -> str:
    """
    '3 Oct 2026' (or 'Sat 3 Oct 2026' with weekday=True).

    Text that is not a date is returned as it was rather than hidden, so a
    bad value in the database stays visible. None or '' gives `empty`.
    """
    d = parse_date(value)
    if d is None:
        text = "" if value is None else str(value).strip()
        return text or empty
    out = f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    if weekday:
        out = f"{WEEKDAYS[d.weekday()]} {out}"
    return out


def format_time(value, *, seconds: bool = False, empty: str = "") -> str:
    """'23:02' - 24-hour, as the studio's attendance and logs are kept."""
    if value is None or value == "":
        return empty
    if isinstance(value, datetime):
        t = value.time()
    elif isinstance(value, time):
        t = value
    else:
        text = str(value).strip()
        try:
            t = time.fromisoformat(text[:8] if len(text) >= 8 and text[2] == ":" else text)
        except ValueError:
            dt = parse_datetime(text)
            if dt is None:
                return text or empty
            t = dt.time()
    return t.strftime("%H:%M:%S" if seconds else "%H:%M")


def format_datetime(value, *, seconds: bool = False, weekday: bool = False,
                    empty: str = "") -> str:
    """
    '17 Sep 2026, 23:02'. Never microseconds - nobody reads those, and they
    made every timestamp column twice as wide as it needed to be.
    """
    dt = parse_datetime(value)
    if dt is None:
        text = "" if value is None else str(value).strip()
        return text or empty
    return f"{format_date(dt, weekday=weekday)}, {dt.strftime('%H:%M:%S' if seconds else '%H:%M')}"


def format_age(value, *, now: Optional[datetime] = None, empty: str = "") -> str:
    """
    How long ago, in the largest unit that fits: 'just now', '5 min ago',
    '3 h ago', 'yesterday', '4 days ago'; the date itself after a week.
    """
    dt = parse_datetime(value)
    if dt is None:
        return empty
    now = now or (datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now())
    seconds = (now - dt).total_seconds()
    if seconds < 0:
        return format_datetime(dt)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400 and now.date() == dt.date():
        return f"{int(seconds // 3600)} h ago"
    days = (now.date() - dt.date()).days
    if days <= 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return format_date(dt)


def format_range(start: DateLike, end: DateLike, *, empty: str = "") -> str:
    """'3 – 7 Oct 2026', '28 Sep – 2 Oct 2026', or the single date when equal."""
    a, b = parse_date(start), parse_date(end)
    if a is None and b is None:
        return empty
    if a is None or b is None or a == b:
        return format_date(a or b)
    if a.year == b.year and a.month == b.month:
        return f"{a.day} – {format_date(b)}"
    if a.year == b.year:
        return f"{a.day} {MONTHS[a.month - 1]} – {format_date(b)}"
    return f"{format_date(a)} – {format_date(b)}"


def date_sort_key(value) -> str:
    """
    What a table should sort a date column by: ISO text, which sorts in date
    order. Empty dates sort first.
    """
    dt = parse_datetime(value)
    return dt.isoformat() if dt else ""


def week_start(value: DateLike) -> Optional[date]:
    """The Monday on or before this date."""
    d = parse_date(value)
    if d is None:
        return None
    return d - timedelta(days=(d.weekday() - WEEK_START) % 7)


def to_iso(value: DateLike) -> Optional[str]:
    """The ISO text the database keeps, or None."""
    d = parse_date(value)
    return d.isoformat() if d else None
