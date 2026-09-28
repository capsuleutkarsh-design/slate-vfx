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
    # ------------------------------------------------------------ workplace
    # These used to be five separate literal lists in five widgets, and they
    # disagreed: the same HR person was an approver in Leave, refused by
    # Users & Roles, and a plain artist in Attendance. One table, one answer.
    "manage_leave": [
        "hr", "human resources", "developer", "admin",
    ],
    "manage_it": [
        "it", "it support", "developer", "admin",
    ],
    "manage_users": [
        "hr", "human resources", "admin", "developer", "supervisor",
    ],
    "view_team_attendance": [
        "hr", "human resources", "supervisor", "developer", "admin",
    ],
    "ingest_stock": [
        "admin", "lead", "supervisor", "developer", "dev",
    ],
    "wipe_fleet_caches": [
        "admin", "developer",
    ],
    # Works the first (supervisor) stage of the leave queue. This was the
    # literal {"supervisor", "lead"} inside the Leave tab.
    "approve_leave": [
        "supervisor", "lead",
    ],
    # Changes what roles may open and do, on the Permissions screen.
    "manage_permissions": [
        "admin", "developer", "it", "hr", "human resources",
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
    global _cache, _db_cache
    _cache = None
    _db_cache = None


# Abilities ticked on a role in the Permissions screen live with the role in
# ut_roles ("can:dashboard_write", ...). They are read here, so every screen
# that asks can(...) honours them - a new role needs ticks, not code. Kept for
# a short while: roles change rarely and this is asked for every table cell.
_DB_TTL_SECONDS = 30.0
_db_cache = None
_db_cache_at = 0.0


def _role_abilities() -> dict:
    """{role name (lower-case): set of abilities} from ut_roles; empty if unreachable."""
    global _db_cache, _db_cache_at
    import time
    now = time.monotonic()
    if _db_cache is not None and now - _db_cache_at < _DB_TTL_SECONDS:
        return _db_cache
    from .permissions_catalog import ABILITY_KEYS, RESTRICTIONS, abilities_in, has_all
    result = {}
    try:
        from slate.core.infra.database_manager import database_manager
        rows = database_manager.execute_query(
            "SELECT role_name, permissions FROM ut_roles", fetch="all") or []
        for row in rows:
            try:
                perms = json.loads(row["permissions"] or "[]")
            except Exception:
                continue
            found = abilities_in(perms)
            if has_all(perms):
                found |= set(ABILITY_KEYS) - RESTRICTIONS
            result[str(row["role_name"]).strip().lower()] = found
    except Exception as exc:
        logging.debug("Role abilities not read from the database: %s", exc)
    _db_cache, _db_cache_at = result, now
    return result


def _normalize(roles) -> Set[str]:
    if roles is None:
        return set()
    if isinstance(roles, str):
        roles = [roles]
    return {str(r).strip().lower() for r in roles if str(r).strip()}


def roles_for(action: str) -> Set[str]:
    return set(_load().get(action, set()))


def _abilities_of(role: str) -> Set[str]:
    """What one role may do: access.json by name, plus the role's own ticks."""
    names = {action for action, members in _load().items() if role in members}
    return names | _role_abilities().get(role, set())


def _allowed(action: str, roles: Iterable) -> bool:
    return any(action in _abilities_of(role) for role in _normalize(roles))


def can(roles, action: str) -> bool:
    """
    Whether any of these roles may perform the named action.

    The one entry point for a permission that has no dedicated helper. A
    widget that asks ``can(roles, "manage_users")`` is answered from
    access.json, so a studio adds a role name there once and every screen
    agrees. An action the file does not know is refused, never granted.
    """
    return _allowed(action, roles)


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
    scoped = wider = False
    for role in _normalize(roles):
        abilities = _abilities_of(role)
        if "dashboard_write" not in abilities:
            continue
        if "department_scoped" in abilities:
            scoped = True
        else:
            wider = True
    return scoped and not wider


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
