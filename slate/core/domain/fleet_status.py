"""
What a workstation's status report means: online, not responding, offline.

Every client writes <server>/LiveStatus/<PC>.json about every 30 seconds
(live_reporter.py). Live Ops, the fleet report and its Excel export all have to
turn the report's ``last_seen`` into a state, and they used to do it three
times with different words: a machine silent for two minutes was "Idle" on a
card (which reads as "the user is away"), and one silent for six minutes simply
vanished from Live Ops while the fleet report still counted it as offline.

This module is the one answer. It is plain Python so the worker threads and the
export can use it without Qt.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

# A client reports every 30 s: one missed report is noise, two are a machine
# that has stopped reporting, ten are a machine that is off.
ONLINE_SECONDS = 60
OFFLINE_SECONDS = 300

ONLINE = "online"
NOT_RESPONDING = "not_responding"
OFFLINE = "offline"
UNKNOWN = "unknown"

LABELS = {
    ONLINE: "Online",
    NOT_RESPONDING: "Not responding",
    OFFLINE: "Offline",
    UNKNOWN: "Unknown",
}

# The order states are listed in: what needs attention is not first, the
# working fleet is - but every report and screen uses the same order.
ORDER = {ONLINE: 0, NOT_RESPONDING: 1, OFFLINE: 2, UNKNOWN: 3}

# Disk usage thresholds, in percent full.
DISK_WARN = 80
DISK_BAD = 90


def last_seen_of(data) -> Optional[float]:
    """The report's last_seen as a number, or None when it is missing or junk."""
    try:
        value = (data or {}).get("last_seen")
    except AttributeError:
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def status_for(last_seen, now: Optional[float] = None) -> str:
    """ONLINE / NOT_RESPONDING / OFFLINE for a last_seen time, UNKNOWN for none."""
    if last_seen is None:
        return UNKNOWN
    try:
        seen = float(last_seen)
    except (TypeError, ValueError):
        return UNKNOWN
    age = max(0.0, (time.time() if now is None else now) - seen)
    if age < ONLINE_SECONDS:
        return ONLINE
    if age < OFFLINE_SECONDS:
        return NOT_RESPONDING
    return OFFLINE


def label(state: str) -> str:
    return LABELS.get(state, LABELS[UNKNOWN])


def age_text(last_seen, now: Optional[float] = None) -> str:
    """'just now', '3 min ago', '2 h 5 min ago', or the date for older reports."""
    if last_seen is None:
        return ""
    try:
        seen = float(last_seen)
    except (TypeError, ValueError):
        return ""
    seconds = int(max(0.0, (time.time() if now is None else now) - seen))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        hours, minutes = divmod(seconds // 60, 60)
        return f"{hours} h {minutes} min ago" if minutes else f"{hours} h ago"
    try:
        from .dates import format_datetime
        return format_datetime(datetime.fromtimestamp(seen))
    except (OverflowError, OSError, ValueError):
        return ""


def seen_at_text(last_seen) -> str:
    """The clock time of a report, '14:02', or '' when unknown."""
    if last_seen is None:
        return ""
    try:
        return datetime.fromtimestamp(float(last_seen)).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def disk_percent(value) -> Optional[float]:
    """
    A disk figure as a number. Clients have written 88, 88.4, '88%' and junk;
    one bad value used to raise inside the refresh loop and stop every card
    after it from updating.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def disk_level(percent: Optional[float]) -> str:
    """'ok' below 80 %, 'warn' from 80, 'bad' from 90, '' when unknown."""
    if percent is None:
        return ""
    if percent >= DISK_BAD:
        return "bad"
    if percent >= DISK_WARN:
        return "warn"
    return "ok"


def summarise(records, now: Optional[float] = None) -> dict:
    """Count reports per state; the four counts always add up to the total."""
    counts = {ONLINE: 0, NOT_RESPONDING: 0, OFFLINE: 0, UNKNOWN: 0}
    for data in records or ():
        counts[status_for(last_seen_of(data), now)] += 1
    counts["total"] = sum(counts[k] for k in (ONLINE, NOT_RESPONDING, OFFLINE, UNKNOWN))
    return counts
