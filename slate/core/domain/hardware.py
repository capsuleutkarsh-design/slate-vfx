"""
The machine inventory's vocabulary and small rules.

Kept out of the screen so the Live Ops sync, the dialogs, the one-time data
repair and the tests all agree on what a status is and how an empty value is
shown.
"""

from __future__ import annotations

import re
from typing import Optional

# In service: who has it decides between these two (the loan ledger, never a
# dropdown - see status_for_service).
AVAILABLE = "Available"
ACTIVE = "Active"
REPAIR = "Repair"
# End of life. Reached only from Edit, and only for a machine nobody has.
RETIRED = "Retired"
LOST = "Lost"
DISPOSED = "Disposed"

STATUSES = (AVAILABLE, ACTIVE, REPAIR, RETIRED, LOST, DISPOSED)
END_OF_LIFE = (RETIRED, LOST, DISPOSED)

# What Add offers: a new machine is never "Active" - nobody has it yet.
ADD_STATUSES = (AVAILABLE, REPAIR)
# What Edit offers. "In service" resolves to Active or Available from the
# ledger, so the status can never contradict who holds the machine.
IN_SERVICE = "In service"
EDIT_STATUSES = (IN_SERVICE, REPAIR, RETIRED, LOST, DISPOSED)

TYPES = ("Workstation", "Render node", "Laptop", "Monitor", "Tablet", "Other")

# Status -> table tone (slate.gui.core.table_style.set_cell_status kinds).
# Colour by meaning: in use is fine, free is information, repair needs
# attention, end of life is idle. Available used to be the accent colour,
# which the product keeps for its one primary action.
TONE = {ACTIVE: "ok", AVAILABLE: "info", REPAIR: "warn",
        RETIRED: "idle", LOST: "idle", DISPOSED: "idle"}

MISSING = "—"          # an em dash: one way to show "nothing recorded"

# Values older code wrote for "nothing": 'N/A', 'None', and the RAM text the
# Live Ops sync built from a missing number ('None GB', ' GB').
JUNK = ("n/a", "none", "null", "gb", "none gb", "null gb", "n/a gb")

# A computer name: a DNS label is at most 63 characters (Windows itself
# stops at 15), letters, digits, '-', '_' and '.'.
NAME_MAX = 63
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalise_status(value) -> str:
    """The canonical status for a stored one ('active' -> 'Active'); '' stays ''."""
    text = str(value or "").strip()
    for known in STATUSES:
        if known.lower() == text.lower():
            return known
    return text


def is_end_of_life(status) -> bool:
    return normalise_status(status) in END_OF_LIFE


# What can never be handed to anybody: in for repair, or at the end of its life.
NOT_ISSUABLE = (REPAIR,) + END_OF_LIFE


def can_be_issued(status) -> bool:
    """The one rule for "can this machine be issued?" (Hardware, Joining & Leaving)."""
    return normalise_status(status) not in NOT_ISSUABLE


def issuable_sql(column: str = "status") -> str:
    """The same rule as a WHERE fragment (case-insensitive, NULL counts as issuable)."""
    words = ", ".join("'%s'" % s.lower() for s in NOT_ISSUABLE)
    return "LOWER(TRIM(COALESCE(%s, ''))) NOT IN (%s)" % (column, words)


def status_for_service(held: bool) -> str:
    """In service: Active when somebody holds it on the ledger, else Available."""
    return ACTIVE if held else AVAILABLE


def cell(value) -> str:
    """A cell's text: the value, or an em dash for None / '' / 'N/A' / 'None'."""
    if value is None:
        return MISSING
    text = str(value).strip()
    if not text or text.lower() in JUNK:
        return MISSING
    return text


def blank_to_none(value) -> Optional[str]:
    """What to store for an optional text field: None rather than '' or 'N/A'."""
    text = str(value or "").strip()
    if not text or text.lower() in JUNK:
        return None
    return text


def name_problem(name) -> str:
    """Why this machine name cannot be used, or '' when it can."""
    text = str(name or "").strip()
    if not text:
        return "Give the machine a name."
    if len(text) > NAME_MAX:
        return "A machine name is at most %d characters." % NAME_MAX
    if not _NAME_RE.match(text):
        return ("Use letters, digits, '-', '_' or '.' only - the name the machine "
                "has on the network.")
    return ""


def gb_text(value) -> str:
    """
    '64 GB' from a number of gigabytes, or '' when there is no number.

    Live Ops reports with RAM_GB null used to be written as 'None GB', and a
    report with no RAM_GB at all as ' GB'.
    """
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = float(str(value).strip().upper().replace("GB", "").strip())
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    return "%d GB" % round(number) if abs(number - round(number)) < 0.05 else "%.1f GB" % number


def gb_value(text) -> int:
    """The number of GB in '64 GB' / '2 TB NVMe' / '512', for a spin box; 0 if none."""
    raw = str(text or "").strip().upper()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB)?", raw)
    if not match:
        return 0
    number = float(match.group(1))
    if match.group(2) == "TB":
        number *= 1024
    return int(round(number))


def storage_text(drives) -> str:
    """Total capacity of a Live Ops 'Drives' list, or '' when nothing is a number."""
    total = 0.0
    for drive in drives or []:
        try:
            total += float((drive or {}).get("Capacity_GB") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
    return gb_text(total) if total > 0 else ""
