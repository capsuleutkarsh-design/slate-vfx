"""
can_still_get_in(): the question every hardening step asks before it is applied.

A hardening step that cannot answer "yes, people can still get in afterwards"
is not applied, and the reason is shown instead. That is the whole rule, and it
is what was missing when the access rules locked a studio out of its own
database.

    from slate.core.security.precheck import can_still_get_in

    result = can_still_get_in(db=database_manager,
                              client={"host": "10.0.0.5", "port": 5440,
                                      "dbname": "ut_vfx", "user": "ut_vfx_app",
                                      "password": new_password},
                              hba_text=proposed_pg_hba)
    if not result.ok:
        show(result.message)          # and do not apply the step
        return

What it checks, each only when it is given what it needs:

    accounts   (db=...)          at least one active administrator with a
                                 usable password exists in ut_users
    client     (client=...)      a real connection with the new settings works
                                 and can read ut_users
    server     (server=...)      the same, for the server's own superuser login
    pg_hba     (hba_text=...)    the proposed rules still start with Slate's
                                 signature line, still let the superuser in
                                 from this PC, and still let the workstations'
                                 account into the studio database from a
                                 studio address

For a change that can only be proved after it is made (most pg_hba edits),
use slate_server.core.recovery.hardening.apply_hardening_step(), which calls
this before, snapshots, applies, calls it again with real connections, and puts
everything back on its own if the second answer is "no".
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

HBA_SIGNATURE = "# Written by Slate Central Server."
PASSWORD_METHODS = {"scram-sha-256", "md5", "password"}


@dataclass
class GetInResult:
    ok: bool = True
    reasons: List[str] = field(default_factory=list)
    checked: List[str] = field(default_factory=list)

    def fail(self, reason: str):
        self.ok = False
        self.reasons.append(reason)

    @property
    def message(self) -> str:
        if self.ok:
            return "People can still get in: " + ", ".join(self.checked or ["nothing checked"])
        return "Not applied, because afterwards nobody could get in:\n- " + "\n- ".join(self.reasons)

    def __bool__(self):
        return self.ok


# ----------------------------------------------------------- pg_hba.conf

def _address_matches(spec: str, address: str) -> bool:
    spec = spec.strip().lower()
    if spec in ("all",):
        return True
    if spec in ("samehost", "samenet"):
        try:
            return ipaddress.ip_address(address).is_loopback if spec == "samehost" else True
        except ValueError:
            return False
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(spec, strict=False)
    except ValueError:
        return False


def _list_matches(spec: str, value: str, extra_all=()) -> bool:
    for item in spec.split(","):
        item = item.strip().strip('"')
        if item.lower() in ("all",) + tuple(extra_all):
            return True
        if item.startswith("+") or item.startswith("@"):
            # Group membership and included files cannot be judged from the
            # text alone; treat them as not matching rather than guess "yes".
            continue
        if item == value:
            return True
    return False


def hba_decision(hba_text: str, *, database: str, user: str, address: str = "127.0.0.1",
                 local: bool = False) -> Optional[str]:
    """
    The method of the first pg_hba.conf line that would match this connection
    ('scram-sha-256', 'trust', 'reject', ...), or None when no line matches -
    which PostgreSQL also refuses.
    """
    for raw in (hba_text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        kind = parts[0].lower()
        if local:
            if kind != "local" or len(parts) < 4:
                continue
            db_spec, user_spec, method = parts[1], parts[2], parts[3]
        else:
            if not kind.startswith("host") or len(parts) < 5:
                continue
            db_spec, user_spec, addr = parts[1], parts[2], parts[3]
            rest = parts[4:]
            if "/" not in addr and rest and _looks_like_mask(rest[0]):
                try:
                    addr = str(ipaddress.ip_network("%s/%s" % (addr, rest[0]), strict=False))
                    rest = rest[1:]
                except ValueError:
                    continue
            if not rest or not _address_matches(addr, address):
                continue
            method = rest[0]
        if db_spec.lower() == "replication":
            continue
        if not _list_matches(db_spec, database, extra_all=("sameuser",) if database == user else ()):
            continue
        if not _list_matches(user_spec, user):
            continue
        return method.lower()
    return None


def _looks_like_mask(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def check_hba(hba_text: str, *, database: str = "ut_vfx", app_user: str = "ut_vfx_app",
              superuser: str = "postgres", studio_address: str = "10.0.0.50",
              result: Optional[GetInResult] = None) -> GetInResult:
    result = result or GetInResult()
    result.checked.append("pg_hba.conf")
    if not (hba_text or "").startswith(HBA_SIGNATURE):
        result.fail("pg_hba.conf must keep its first line, %r. Without it Slate cannot "
                    "tell its own file from a hand-written one, and the recovery for a "
                    "locked-out database is switched off." % HBA_SIGNATURE)
    for address in ("127.0.0.1",):
        method = hba_decision(hba_text, database=database, user=superuser, address=address)
        if method is None or method == "reject":
            result.fail("The server itself (%s from %s) would be refused." % (superuser, address))
    method = hba_decision(hba_text, database=database, user=app_user, address=studio_address)
    if method is None or method == "reject":
        result.fail("Workstations (%s from %s) would be refused." % (app_user, studio_address))
    elif method == "trust":
        result.fail("Workstations would be let in without a password (trust).")
    return result


# --------------------------------------------------------------- accounts

def check_accounts(db, result: Optional[GetInResult] = None) -> GetInResult:
    from slate.core.security import admin_guard
    result = result or GetInResult()
    result.checked.append("an active administrator")
    try:
        users, perms = admin_guard.read_state(db)
    except Exception as exc:
        result.fail("The accounts could not be read (%s), so it cannot be shown that an "
                    "administrator can still sign in." % str(exc).splitlines()[0])
        return result
    if not admin_guard.administrators(users, perms):
        result.fail("There is no active administrator account with a password. "
                    "Create one first (the recovery tool can).")
    return result


# ------------------------------------------------------------- connections

def try_connect(settings: dict, connect: Optional[Callable] = None) -> str:
    """'' when these settings open the database and can read ut_users, else why not."""
    if connect is None:
        import psycopg2
        connect = psycopg2.connect
    params = {k: settings[k] for k in ("host", "port", "dbname", "user", "password")
              if settings.get(k) not in (None, "")}
    params.setdefault("connect_timeout", int(settings.get("connect_timeout", 5)))
    params["application_name"] = "Slate pre-check"
    try:
        conn = connect(**params)
    except Exception as exc:
        text = str(exc).strip().splitlines()
        return text[0] if text else exc.__class__.__name__
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM ut_users LIMIT 1")
            cur.fetchall()
    except Exception as exc:
        text = str(exc).strip().splitlines()
        return "connected, but could not read the accounts: %s" % (text[0] if text else exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return ""


def can_still_get_in(*, db=None, client: Optional[dict] = None,
                     server: Optional[dict] = None, hba_text: Optional[str] = None,
                     hba_context: Optional[dict] = None,
                     connect: Optional[Callable] = None) -> GetInResult:
    """
    Whether people can still get in with the proposed settings. See the module
    docstring. Every hardening step calls this BEFORE it applies anything, and
    applies nothing when ``result.ok`` is False.
    """
    result = GetInResult()
    if db is not None:
        check_accounts(db, result)
    if client is not None:
        result.checked.append("workstation login (%s)" % client.get("user", "?"))
        why = try_connect(client, connect)
        if why:
            result.fail("Workstations could not connect with the new settings: %s" % why)
    if server is not None:
        result.checked.append("server login (%s)" % server.get("user", "?"))
        why = try_connect(server, connect)
        if why:
            result.fail("The server could not log in with the new settings: %s" % why)
    if hba_text is not None:
        check_hba(hba_text, result=result, **(hba_context or {}))
    if not result.checked:
        result.fail("Nothing was given to check, so nothing can be shown to be safe.")
    if not result.ok:
        logger.warning("can_still_get_in refused: %s", "; ".join(result.reasons))
    return result
