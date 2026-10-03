"""
Never remove the last way in.

Slate refuses any change that would leave the studio with no active account
able to administer it:

    * no active account with full access (the Developer / Admin roles, or a
      role holding "ALL"), or
    * no active account that may manage users (manage_users).

"Active" means not deactivated and not past its last day, with a password that
can actually be checked (a non-empty stored hash). The check is made on the
state the change WOULD produce, so it covers every route that can produce it:
deleting, deactivating, giving somebody a last day in the past, taking a role
away, editing a role's permissions, renaming a role (access.json grants by role
name), a user import, and a bulk sync of the user table.

A change that does not make things worse is never refused: if a damaged
database already has no administrator, adding roles or users still works. The
recovery tool (slate_server/core/recovery) is how such a database is mended.

Used by UserManager and UserRepository; the raw SQL console and direct database
edits can still get around it, which is what the recovery tool is for.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import date
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from slate.core.domain.access import GrantRefused as _Base
except Exception:                                   # pragma: no cover
    _Base = PermissionError


class LastAdminRefused(_Base):
    """The change would leave nobody able to administer Slate."""


# --------------------------------------------------------------- the rules

def account_active(record) -> bool:
    """Not switched off and not past the last day. Lenient: anything unclear is active."""
    value = record.get("active")
    if value is not None and str(value).strip() not in ("", "1", "True", "true"):
        try:
            if int(value) == 0:
                return False
        except (TypeError, ValueError):
            if str(value).strip().lower() in ("false", "no"):
                return False
    last = record.get("last_day")
    if last:
        try:
            if date.fromisoformat(str(last)[:10]) < date.today():
                return False
        except ValueError:
            pass
    return True


def usable_password(stored_hash) -> bool:
    return bool(str(stored_hash or "").strip())


def parse_roles(raw):
    if isinstance(raw, list):
        return [str(r) for r in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
            return [value] if isinstance(value, str) else [str(r) for r in (value or [])]
        except Exception:
            return [raw]
    return []


def _superuser_names():
    try:
        from slate.core.domain.access import SUPERUSER_ROLES
        return set(SUPERUSER_ROLES)
    except Exception:
        return {"admin", "developer"}


def _manage_users_names():
    try:
        from slate.core.domain.access import roles_for
        return set(roles_for("manage_users"))
    except Exception:
        return {"admin", "developer", "hr", "human resources"}


def _perms(role_perms, role):
    return (role_perms or {}).get(str(role).strip().lower(), [])


def has_full_access(roles, role_perms) -> bool:
    from slate.core.domain.permissions_catalog import has_all
    names = _superuser_names()
    for role in roles or []:
        key = str(role).strip().lower()
        if key in names or has_all(_perms(role_perms, key)):
            return True
    return False


def can_manage_users(roles, role_perms) -> bool:
    from slate.core.domain.permissions_catalog import abilities_in, has_all
    if has_full_access(roles, role_perms):
        return True
    names = _manage_users_names()
    for role in roles or []:
        key = str(role).strip().lower()
        perms = _perms(role_perms, key)
        if key in names or "manage_users" in abilities_in(perms) or has_all(perms):
            return True
    return False


def counts(users: Dict[str, dict], role_perms) -> Tuple[int, int]:
    """(active full-access accounts, active user-managing accounts) with a usable password."""
    admins = managers = 0
    for record in (users or {}).values():
        if not account_active(record) or not usable_password(record.get("password_hash")):
            continue
        roles = parse_roles(record.get("roles"))
        if has_full_access(roles, role_perms):
            admins += 1
        if can_manage_users(roles, role_perms):
            managers += 1
    return admins, managers


def administrators(users: Dict[str, dict], role_perms):
    """Usernames of the active full-access accounts with a usable password."""
    return sorted(name for name, record in (users or {}).items()
                  if account_active(record) and usable_password(record.get("password_hash"))
                  and has_full_access(parse_roles(record.get("roles")), role_perms))


def refusal(before_users, before_perms, after_users, after_perms) -> str:
    """Why this change may not be made, or '' when it may."""
    admins_before, managers_before = counts(before_users, before_perms)
    admins_after, managers_after = counts(after_users, after_perms)
    if admins_before > 0 and admins_after == 0:
        return ("This would leave Slate with no active administrator, and then "
                "nobody could sign in to put it right. Give another person full "
                "access (the Developer or Admin role) first.")
    if managers_before > 0 and managers_after == 0:
        return ("This would leave nobody who can manage users. Give another "
                "active person that ability first.")
    return ""


# ------------------------------------------------------------ reading state

def read_state(db) -> Tuple[Dict[str, dict], Dict[str, list]]:
    """({username: row}, {role (lower-case): permissions}) from ut_users / ut_roles."""
    users = {}
    for row in (db.execute_query("SELECT * FROM ut_users", fetch="all") or []):
        row = dict(row)
        users[str(row.get("username"))] = row
    perms = {}
    try:
        for row in (db.execute_query("SELECT role_name, permissions FROM ut_roles",
                                     fetch="all") or []):
            try:
                value = json.loads(row["permissions"] or "[]")
            except Exception:
                value = []
            perms[str(row["role_name"]).strip().lower()] = list(value or [])
    except Exception as exc:
        logger.debug("Roles not read for the admin guard: %s", exc)
    return users, perms


def check(db, change: Callable[[Dict[str, dict], Dict[str, list]], None]) -> str:
    """
    Apply ``change`` (which edits the users and role permissions it is given,
    in place) to a copy of the current state, and say whether that is allowed.

    Never raises for a read problem: a guard that cannot read is a guard that
    stops everybody working. It logs and allows.
    """
    try:
        users, perms = read_state(db)
    except Exception as exc:
        logger.warning("The last-administrator check could not read the accounts: %s", exc)
        return ""
    after_users, after_perms = deepcopy(users), deepcopy(perms)
    change(after_users, after_perms)
    return refusal(users, perms, after_users, after_perms)


def sync_change(users_dict):
    """
    The change a bulk upsert of the user table makes (UserRepository.sync_users):
    roles are replaced for every listed account, which may be new; a password
    is replaced only when one is given.
    """
    def change(users, perms):
        for username, data in (users_dict or {}).items():
            data = data or {}
            roles = data.get("roles") or ([data["role"]] if data.get("role") else ["Artist"])
            key = find(users, username)
            if key is None:
                key = str(username)
                users[key] = {"username": key, "password_hash": ""}
            users[key]["roles"] = json.dumps(roles if isinstance(roles, list) else [roles])
            if data.get("password_hash"):
                users[key]["password_hash"] = data["password_hash"]
    return change


def find(users: Dict[str, dict], username) -> Optional[str]:
    """The stored key for username, compared case-insensitively."""
    wanted = str(username or "").strip().lower()
    for name in users:
        if str(name).strip().lower() == wanted:
            return name
    return None
