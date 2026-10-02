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
    # May edit shots, statuses and assignments on the dashboard. "producer"
    # is here so the dashboard can stop treating it as an alias.
    "dashboard_write": [
        "admin", "developer", "supervisor", "coordinator", "lead", "producer",
    ],
    # May import from and export to the project Excel backup.
    "excel_sync": [
        "admin", "developer", "supervisor", "coordinator", "lead", "producer",
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
        # Every artist-type role, not only the one called "Artist": a
        # Compositor could not set the status of their own comp row.
        "team lead", "generalist", "compositor", "roto artist", "paint artist",
        "deage artist", "ai artist", "dmp", "cg", "producer",
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
    # Supervisors are not here any more: with it they could create a
    # Developer account. They see their own reports' attendance through
    # approve_leave instead (read-only), not the whole studio's.
    "manage_users": [
        "hr", "human resources", "admin", "developer",
    ],
    "view_team_attendance": [
        "hr", "human resources", "developer", "admin",
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
    # Changes what roles may open and do, on the Permissions screen - within
    # what the editor holds themselves (see can_grant).
    "manage_permissions": [
        "admin", "developer", "it", "hr", "human resources",
    ],
    # ------------------------------------------------------ added 2026-09
    # Existing roles are upgraded once so nobody loses what they could do
    # (UserManager._upgrade_role_abilities); these lists cover role names.
    #
    # May be given work on the dashboard (listed in the Artist pickers).
    "assignable": [
        "artist", "lead", "team lead", "generalist", "compositor", "roto artist",
        "paint artist", "deage artist", "ai artist", "dmp", "cg",
    ],
    # Sees every shot of a project on the dashboard, read-only unless they
    # can also edit it. Without it (and without dashboard_write) a person
    # sees the shots they are named on.
    "dashboard_view_all": [
        "admin", "developer", "supervisor", "coordinator", "producer",
        "production head", "production coordinator", "comp supervisor",
        "roto prep supervisor", "editor",
    ],
    # May add, change and shift milestones on Scheduling.
    "schedule_write": [
        "admin", "developer", "production head", "production coordinator", "producer",
    ],
    # May mark a bid Won or Lost. Nobody approves their own bid except
    # Admin and Developer (the Bidding tab enforces that part).
    "approve_bid": [
        "admin", "developer", "production head",
    ],
    # May archive or delete a whole dashboard project.
    "delete_project": [
        "admin", "developer",
    ],
    # Read-only Licences for people who approve renewals.
    "view_licences": [
        "admin", "developer", "production head", "it", "it support",
    ],
    # Data Center, table editing, the SQL console, purge, the API gateway,
    # Audit Logs and every remote workstation action.
    "manage_system": [
        "admin", "developer",
    ],
    # Studio-wide settings: studio policy, server and database paths,
    # branding and updates. Personal preferences stay open to everybody.
    "studio_settings": [
        "admin", "developer", "it", "it support",
    ],
    # The Tester Panel's destructive tools: wipe, set file dates, the big
    # generators, VACUUM.
    "tester_destructive": [
        "developer",
    ],
}

# Only these roles may grant Full access or a sensitive ability, or change
# a role they hold themselves. Everybody else can only hand on what they hold.
SUPERUSER_ROLES = frozenset({"admin", "developer"})


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
    global _cache, _db_cache, _perm_cache
    _cache = None
    _db_cache = None
    _perm_cache = None


# Abilities ticked on a role in the Permissions screen live with the role in
# ut_roles ("can:dashboard_write", ...). They are read here, so every screen
# that asks can(...) honours them - a new role needs ticks, not code. Kept for
# a short while: roles change rarely and this is asked for every table cell.
_DB_TTL_SECONDS = 30.0
_db_cache = None
_db_cache_at = 0.0
_perm_cache = None
_perm_cache_at = 0.0


def _role_abilities() -> dict:
    """{role name (lower-case): set of abilities} from ut_roles; empty if unreachable."""
    global _db_cache, _db_cache_at
    import time
    now = time.monotonic()
    if _db_cache is not None and now - _db_cache_at < _DB_TTL_SECONDS:
        return _db_cache
    from .permissions_catalog import ABILITY_KEYS, NOT_IMPLIED_BY_ALL, abilities_in, has_all
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
                found |= set(ABILITY_KEYS) - NOT_IMPLIED_BY_ALL
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


# ------------------------------------------------------------------ granting
#
# "Who may give whom what." Nothing stopped an HR or IT editor ticking Full
# access on their own role, or a supervisor creating a Developer account: the
# Permissions screen and the Users dialog offered everything to anybody who
# could open them. The rule now, enforced in UserManager as well as on screen:
#
#   * Admin and Developer may grant anything.
#   * Everybody else may only hand on tabs and abilities they hold themselves,
#     never Full access, never a sensitive ability (SENSITIVE_ABILITIES), and
#     may not change a role they hold, or one more powerful than their own.
#   * Roles are given to people on the same terms: only roles no more powerful
#     than the editor's own, and never to change somebody more powerful.


class GrantRefused(PermissionError):
    """An edit to roles or accounts the acting person may not make."""


def _role_permission_lists() -> dict:
    """{role name (lower-case): stored permission list} from ut_roles."""
    global _perm_cache, _perm_cache_at
    import time
    now = time.monotonic()
    if _perm_cache is not None and now - _perm_cache_at < _DB_TTL_SECONDS:
        return _perm_cache
    result = {}
    try:
        from slate.core.infra.database_manager import database_manager
        rows = database_manager.execute_query(
            "SELECT role_name, permissions FROM ut_roles", fetch="all") or []
        for row in rows:
            try:
                perms = json.loads(row["permissions"] or "[]")
            except Exception:
                perms = []
            result[str(row["role_name"]).strip().lower()] = list(perms or [])
    except Exception as exc:
        logging.debug("Role permissions not read from the database: %s", exc)
    _perm_cache, _perm_cache_at = result, now
    return result


def is_superuser(roles) -> bool:
    """Admin or Developer: may grant anything."""
    return bool(_normalize(roles) & SUPERUSER_ROLES)


def holdings(roles, role_permissions=None):
    """
    (tab keys, abilities, full) that these roles hold between them.

    role_permissions ({role: [permissions]}) may be passed in; by default it
    is read from ut_roles.
    """
    from .permissions_catalog import TAB_KEYS, abilities_in, has_all
    lists = role_permissions if role_permissions is not None else _role_permission_lists()
    lists = {str(k).strip().lower(): v for k, v in (lists or {}).items()}
    tabs, abilities, full = set(), set(), False
    for role in _normalize(roles):
        perms = lists.get(role, [])
        if has_all(perms):
            full = True
        tabs |= {str(p).strip() for p in perms if str(p).strip() in TAB_KEYS}
        abilities |= _abilities_of(role) | abilities_in(perms)
    if full:
        tabs |= set(TAB_KEYS)
    return tabs, abilities, full


def _split(permission):
    from .permissions_catalog import ABILITY_PREFIX, ALL, TAB_KEYS
    text = str(permission or "").strip()
    if text.upper() == ALL:
        return "all", text
    if text.lower().startswith(ABILITY_PREFIX):
        return "ability", text[len(ABILITY_PREFIX):].strip().lower()
    if text in TAB_KEYS:
        return "tab", text
    return "other", text


def refused_grants(editor_roles, permissions, role_permissions=None):
    """
    The items in `permissions` this editor may not put on a role, as they
    appear in the list (empty when everything is allowed).
    """
    if is_superuser(editor_roles):
        return []
    from .permissions_catalog import RESTRICTIONS, SENSITIVE_ABILITIES
    lists = role_permissions if role_permissions is not None else _role_permission_lists()
    lists = {str(k).strip().lower(): v for k, v in (lists or {}).items()}
    tabs, abilities, full = holdings(editor_roles, lists)
    held_raw = set()
    for role in _normalize(editor_roles):
        held_raw |= {str(p).strip() for p in lists.get(role, [])}
    refused = []
    for permission in permissions or []:
        kind, key = _split(permission)
        if kind == "all":
            refused.append(permission)
        elif kind == "ability":
            if key in RESTRICTIONS:
                continue                    # a limit, not a right
            if key in SENSITIVE_ABILITIES or key not in abilities:
                refused.append(permission)
        elif kind == "tab":
            if not full and key not in tabs:
                refused.append(permission)
        elif key and key not in held_raw:
            refused.append(permission)
    return refused


def can_grant(editor_roles, permissions, role_permissions=None) -> bool:
    """Whether this editor may put all of `permissions` on a role."""
    return not refused_grants(editor_roles, permissions, role_permissions)


def role_change_refusal(editor_roles, role_name, old_permissions, new_permissions,
                        role_permissions=None) -> str:
    """
    Why this editor may not change `role_name` from old to new permissions,
    or "" when they may.
    """
    if is_superuser(editor_roles):
        return ""
    role_key = str(role_name or "").strip().lower()
    if role_key in _normalize(editor_roles):
        return (f"You hold the {role_name} role, so you cannot change it. "
                "Ask an Admin or Developer.")
    if role_key in SUPERUSER_ROLES:
        return f"Only an Admin or Developer can change the {role_name} role."
    from .permissions_catalog import TAB_KEYS, ABILITY_PREFIX, ALL
    # A role more powerful than the editor's own cannot be touched at all -
    # taking things away from Admin is as much an escalation as adding.
    known_old = [p for p in (old_permissions or [])
                 if _split(p)[0] in ("all", "ability", "tab")]
    if refused_grants(editor_roles, known_old, role_permissions):
        return (f"The {role_name} role has rights you do not hold, so only an "
                "Admin or Developer can change it.")
    added = [p for p in (new_permissions or []) if p not in set(old_permissions or [])]
    refused = refused_grants(editor_roles, added, role_permissions)
    if refused:
        return ("You can only give a role what you hold yourself, and only an Admin "
                "or Developer can give Full access or a sensitive ability. Not "
                "allowed: " + ", ".join(str(r) for r in refused) + ".")
    return ""


def assignable_roles(editor_roles, available_roles, role_permissions=None):
    """
    The roles in `available_roles` this editor may give to a person.

    Admin and Developer may give any. Anybody else may give a role unless it
    carries Full access, is Admin or Developer, or carries a privileged tab or
    an administrative ability (permissions_catalog.PRIVILEGED_TABS and
    ADMIN_ABILITIES) that the editor does not hold themselves. Ordinary job
    rights - editing the dashboard, approving a team's leave - do not make a
    role "more powerful": HR takes on supervisors as well as artists.
    """
    if is_superuser(editor_roles):
        return list(available_roles or [])
    from .permissions_catalog import ADMIN_ABILITIES, PRIVILEGED_TABS, abilities_in, has_all
    lists = role_permissions if role_permissions is not None else _role_permission_lists()
    lists = {str(k).strip().lower(): v for k, v in (lists or {}).items()}
    tabs, abilities, full = holdings(editor_roles, lists)
    result = []
    for role in available_roles or []:
        key = str(role).strip().lower()
        if key in SUPERUSER_ROLES:
            continue
        perms = lists.get(key, [])
        if has_all(perms):
            continue
        role_tabs = {str(p).strip() for p in perms} & PRIVILEGED_TABS
        role_abilities = (abilities_in(perms) | _abilities_of(key)) & ADMIN_ABILITIES
        if not full and not role_tabs <= tabs:
            continue
        if not role_abilities <= abilities:
            continue
        result.append(role)
    return result


def role_assignment_refusal(editor_roles, target_old_roles, target_new_roles,
                            available_roles=None, role_permissions=None) -> str:
    """
    Why this editor may not change a person's roles from old to new, or "".

    Also answers "may they edit this person at all": pass the same list twice.
    """
    if is_superuser(editor_roles):
        return ""
    old = [str(r) for r in (target_old_roles or [])]
    new = [str(r) for r in (target_new_roles or [])]
    universe = list(dict.fromkeys(list(available_roles or []) + old + new))
    allowed = {str(r).strip().lower() for r in assignable_roles(editor_roles, universe, role_permissions)}
    stronger = [r for r in old if r.strip().lower() not in allowed]
    if stronger:
        return ("This person holds " + ", ".join(stronger) + ", which has rights "
                "you do not hold. Only an Admin or Developer can change their account.")
    added = [r for r in new if r.strip().lower() not in {o.strip().lower() for o in old}]
    refused = [r for r in added if r.strip().lower() not in allowed]
    if refused:
        return ("You can only give roles no more powerful than your own. Not allowed: "
                + ", ".join(refused) + ".")
    return ""


def can_be_assigned(roles) -> bool:
    """May be given work on the dashboard (listed as an artist)."""
    return _allowed("assignable", roles)


def can_view_all_shots(roles) -> bool:
    """Sees every shot of the project, not only their own."""
    return _allowed("dashboard_view_all", roles) or can_edit_dashboard(roles)


def can_delete_project(roles) -> bool:
    return _allowed("delete_project", roles)


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
