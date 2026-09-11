"""
Who is allowed to do what.

Write access used to be the literal set {"supervisor", "developer", "admin"}
copied into four different files, which meant a coordinator - the person the
dashboard is actually for - could not edit anything, and adding a role meant
editing code in several places.

The sets live in ``slate/data/access.json``. Role names are compared
case-insensitively.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Set


def _package_data_path(filename: str) -> Path:
    """
    Locate a file in slate/data, in a source tree or a frozen build.

    Mirrors how templates.json is found: importlib.resources first, falling
    back to a path relative to this module.
    """
    try:
        from importlib.resources import files
        candidate = files("slate.data").joinpath(filename)
        path = Path(str(candidate))
        if path.exists():
            return path
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "data" / filename

ACCESS_FILE = _package_data_path("access.json")


_DEFAULTS = {
    # May edit shots, statuses and assignments on the dashboard.
    "dashboard_write": [
        "admin", "developer", "supervisor", "coordinator", "lead",
    ],
    # May import from and export to the project Excel backup.
    "excel_sync": [
        "admin", "developer", "supervisor", "coordinator", "lead",
    ],
    # May force-save over another user's concurrent edit.
    "force_save": [
        "admin", "developer", "supervisor",
    ],
    # May set the status of a department row they are personally assigned to,
    # and nothing else. This is the one field where the artist is the only
    # person who actually knows the answer.
    "artist_own_status": [
        "artist", "lead", "coordinator", "supervisor", "developer", "admin",
    ],
    # What somebody with only artist_own_status may set. Approved, Retake and
    # Omit are verdicts other people give; an artist approving their own work
    # is not a status change, it is a review that never happened.
    "artist_statuses": [
        "YTS", "WIP", "READY", "SENT FOR REVIEW",
    ],
    # Roles in dashboard_write that may edit only their own department's
    # columns - a roto lead runs roto, not comp.
    "department_scoped": [
        "lead",
    ],
}


_cache = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    data = dict(_DEFAULTS)
    try:
        if ACCESS_FILE.exists():
            with open(ACCESS_FILE, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            for key in _DEFAULTS:
                value = loaded.get(key)
                if isinstance(value, list) and value:
                    data[key] = value
    except Exception as exc:
        logging.warning("Could not read access.json (%s); using defaults", exc)

    _cache = {key: {str(r).strip().lower() for r in value if str(r).strip()}
              for key, value in data.items()}
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def _normalize(roles) -> Set[str]:
    if roles is None:
        return set()
    if isinstance(roles, str):
        roles = [roles]
    return {str(r).strip().lower() for r in roles if str(r).strip()}


def roles_for(action: str) -> Set[str]:
    return set(_load().get(action, set()))


def _allowed(action: str, roles: Iterable) -> bool:
    return bool(_normalize(roles) & roles_for(action))


def can_edit_dashboard(roles) -> bool:
    """Edit shots, statuses and assignments."""
    return _allowed("dashboard_write", roles)


def can_use_excel(roles) -> bool:
    """Import from and export to the project Excel backup."""
    return _allowed("excel_sync", roles)


def can_force_save(roles) -> bool:
    """Overwrite another user's concurrent edit."""
    return _allowed("force_save", roles)


def can_edit_own_status(roles) -> bool:
    """Set the status of a department row this person is assigned to."""
    return _allowed("artist_own_status", roles)


def artist_statuses() -> Set[str]:
    """The statuses an artist may set, upper-cased as the dashboard shows them."""
    return {s.upper() for s in _load().get("artist_statuses", set())}


def can_set_status(roles, status) -> bool:
    """
    Whether these roles may set this particular status.

    Full dashboard rights can set anything. Anybody else is limited to the
    states of their own work - a verdict is somebody else's to give.
    """
    if can_edit_dashboard(roles):
        return True
    return str(status or "").strip().upper() in artist_statuses()


def is_department_scoped(roles) -> bool:
    """
    Whether these roles are confined to their own department.

    A scoped role still has dashboard_write, but only for the columns of the
    department on their job title. Anything with a wider role alongside
    (supervisor, admin) is not scoped.
    """
    normalized = _normalize(roles)
    if not normalized & roles_for("department_scoped"):
        return False
    wider = roles_for("dashboard_write") - roles_for("department_scoped")
    return not (normalized & wider)


def is_offline_fallback() -> bool:
    """
    True when the app has fallen back to a local database because the central
    one was unreachable.

    This is not the same as a studio deliberately running on SQLite. It means
    edits made here would never reach anybody else, which is why writing is
    blocked while it is true.
    """
    try:
        from slate.core.infra.database_manager import database_manager
        status = database_manager.get_runtime_status() or {}
    except Exception:
        return False

    return (str(status.get("active_mode", "")).lower() == "sqlite"
            and bool(status.get("fallback_used", False)))


class OfflineError(RuntimeError):
    """Raised when a write is attempted while the central database is unreachable."""
