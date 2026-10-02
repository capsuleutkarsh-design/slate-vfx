"""
The words the dashboard uses for where a shot is, said once.

Before this, every screen had its own list: the grid offered seven statuses,
the detail panel's department rows offered "WIP, Done, Approved, Pending" (so
RETAKE, SENT FOR REVIEW, YTS and OMIT were wiped to blank on the next save),
the board wrote "Ready" and "Final", the counters said REVIEW where the filter
said SENT FOR REVIEW, Add Shots had no READY or OMIT, and a blank status was
"UNKNOWN", "NO STATUS" or "(none)" depending on where you looked.

Everything that shows, offers, groups, filters or counts a shot status reads
it from here. Unknown values that are already stored are never coerced: the
pick lists keep them as an extra entry (``choices_with``), so opening and
saving a shot cannot change a status nobody touched.

Shot types and priorities are studio settings (studio_settings
"shot_types" / "shot_priorities"); the helpers here read them and fall back to
the packaged defaults when the database cannot be asked.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

# The workflow, in the order work moves through it. This is the order the
# filter, the counters, the board columns and Group by Status follow.
YTS = "YTS"
READY = "READY"
WIP = "WIP"
SENT_FOR_REVIEW = "SENT FOR REVIEW"
RETAKE = "RETAKE"
APPROVED = "APPROVED"
OMIT = "OMIT"

WORKFLOW: Tuple[str, ...] = (YTS, READY, WIP, SENT_FOR_REVIEW, RETAKE, APPROVED, OMIT)

# What a person reads for a shot with no status at all - one name everywhere.
NO_STATUS = "No status"

# What each status means, for tooltips.
DESCRIPTIONS = {
    YTS: "Yet to start - nobody has begun work",
    READY: "Ready to start - everything the artist needs is in place",
    WIP: "Work in progress",
    SENT_FOR_REVIEW: "Sent for review - waiting for a verdict",
    RETAKE: "Retake - the review asked for changes",
    APPROVED: "Approved",
    OMIT: "Omitted - cut from the show, not counted as outstanding",
}

# Spellings that mean one of the statuses above. Used only for matching
# (filters, counters, the board) - a stored value is never rewritten.
_ALIASES = {
    "READY TO START": READY,
    "IN PROGRESS": WIP,
    "IP": WIP,
    "REVIEW": SENT_FOR_REVIEW,
    "IN REVIEW": SENT_FOR_REVIEW,
    "SENT TO REVIEW": SENT_FOR_REVIEW,
    "OMITTED": OMIT,
    "NOT STARTED": YTS,
    "NOT_STARTED": YTS,
}

# Finished work: not outstanding, not late.
DONE_STATUSES = frozenset({APPROVED, "DONE", "COMPLETE", "COMPLETED", "FINAL", "DELIVERED"})
# Cut from the show: not counted at all (total, % approved, late, load).
OMITTED_STATUSES = frozenset({OMIT, "OMITTED", "N/A", "NA", "CUT"})
# Nobody has started.
NOT_STARTED_STATUSES = frozenset({"", YTS, "NOT STARTED", "TBD", READY})


def normalise(value) -> str:
    """A stored status as the dashboard compares it: trimmed, upper case."""
    return " ".join(str(value or "").split()).upper()


def canonical(value) -> str:
    """
    The workflow status a stored value means, for matching.

    'Ready' and 'READY' are one status; 'In Review' is SENT FOR REVIEW. A value
    that matches nothing comes back normalised as itself, never forced into
    the list. Blank stays blank.
    """
    text = normalise(value)
    if text in ("-", "NONE", "NULL"):
        return ""
    return _ALIASES.get(text, text)


def label(value) -> str:
    """What a person reads: the canonical name, or 'No status' for a blank."""
    text = canonical(value)
    return text if text else NO_STATUS


def describe(value) -> str:
    text = canonical(value)
    if not text:
        return "No status set yet"
    return DESCRIPTIONS.get(text, text)


def order_key(value) -> Tuple[int, str]:
    """Sort key putting statuses in workflow order, unknown ones after, blank last."""
    text = canonical(value)
    if not text:
        return (len(WORKFLOW) + 1, "")
    try:
        return (WORKFLOW.index(text), "")
    except ValueError:
        return (len(WORKFLOW), text)


def is_done(value) -> bool:
    return canonical(value) in DONE_STATUSES


def is_omitted(value) -> bool:
    return canonical(value) in OMITTED_STATUSES


def is_not_started(value) -> bool:
    return canonical(value) in NOT_STARTED_STATUSES


def choices(include_blank: bool = False) -> List[str]:
    """The statuses a pick list offers, in workflow order."""
    items = list(WORKFLOW)
    return ([""] + items) if include_blank else items


def choices_with(current, include_blank: bool = False,
                 allowed: Optional[Iterable[str]] = None) -> List[str]:
    """
    A pick list that can show `current` exactly as it is stored.

    The list is the workflow (or `allowed`, for somebody limited to some of
    it); a stored value outside it - "Done", "N/A", "CBB" - is added at the end
    so that opening a row and saving it never changes a status nobody touched.
    Matching is case-insensitive: a stored "Approved" selects APPROVED.
    """
    base = [s for s in (allowed if allowed is not None else WORKFLOW)]
    items = ([""] + base) if include_blank else list(base)
    text = str(current or "").strip()
    if text and canonical(text) not in {canonical(i) for i in items if i}:
        items.append(text)
    return items


def match_index(items: List[str], current) -> int:
    """Where `current` sits in `items`, matching the way a person would."""
    text = str(current or "").strip()
    if not text:
        return items.index("") if "" in items else -1
    for i, item in enumerate(items):
        if item == text:
            return i
    want = canonical(text)
    for i, item in enumerate(items):
        if item and canonical(item) == want:
            return i
    return -1


# ------------------------------------------------------------------ priority

_DEFAULT_PRIORITIES = [(0, "Urgent"), (1, "High"), (2, "Normal"), (3, "Low")]


def priorities() -> List[Tuple[int, str]]:
    """[(value, label)] from the studio setting, in order (0 = most urgent)."""
    try:
        from slate.core.infra.studio_settings import get_setting
        raw = get_setting("shot_priorities") or []
        found = []
        for entry in raw:
            if isinstance(entry, dict) and "value" in entry:
                found.append((int(entry["value"]), str(entry.get("label") or entry["value"])))
        if found:
            return sorted(found)
    except Exception:
        pass
    return list(_DEFAULT_PRIORITIES)


def priority_label(value) -> str:
    """'Urgent' for 0 and so on; the number itself when the studio has no name for it."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    for v, text in priorities():
        if v == number:
            return text
    return str(number)


def priority_value(text) -> Optional[int]:
    """The number behind a priority as typed or picked ('High', '1', '1 (High)'), or None."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    head = raw.split()[0].rstrip(".:)")
    if head.lstrip("-").isdigit():
        number = int(head)
        return number if number in {v for v, _ in priorities()} else None
    for v, name in priorities():
        if name.lower() == raw.lower():
            return v
    return None


# ----------------------------------------------------------------- shot type

_DEFAULT_SHOT_TYPES = ["Prep", "2D Comp", "2.5D Comp", "CG Comp", "AI Shot", "Roto", "DMP"]


def shot_types() -> List[str]:
    """The studio's shot types (studio setting 'shot_types')."""
    try:
        from slate.core.infra.studio_settings import get_setting
        raw = get_setting("shot_types") or []
        names = [str(t).strip() for t in raw if str(t).strip()]
        if names:
            return names
    except Exception:
        pass
    return list(_DEFAULT_SHOT_TYPES)


def shot_types_with(current, include_blank: bool = True) -> List[str]:
    """The shot type list plus a stored value that is not on it ('Roto only')."""
    items = ([""] if include_blank else []) + shot_types()
    text = str(current or "").strip()
    if text and text.lower() not in {i.lower() for i in items}:
        items.append(text)
    return items
