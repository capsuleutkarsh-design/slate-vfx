"""
What the recovery tool can do once it is in.

Every function here takes an open connection from a TrustWindow (the superuser,
from this PC) - none of them needs, or asks for, any existing password - and
returns plain sentences saying what was done and what the person has to do
next. Passwords are passed to PostgreSQL as literals and never logged.

    reset_account(conn, "admin", new)          (a) password, deactivation, last day
    restore_admin(conn, "admin", new)          (b) make or mend an administrator
    set_app_password(conn, new, layout)        (c) the workstations' password
    set_superuser_password(conn, new, layout)  (c) postgres's own password
    switches_off(layout, names, conn)          (d) turn security features off
    restore latest snapshot                    (e) snapshots.restore_snapshot
    health check                               (f) health.health_check

Session wrapper: ``recover(layout, key, action, ...)`` checks the server PC
and the Recovery Key, takes a snapshot, opens the trust window, runs one action
and closes the window - see run_action().
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional

import bcrypt

from slate.core.security.dbapi import ConnectionDB

logger = logging.getLogger(__name__)

MIN_PASSWORD = 8
LOCKOUT_COLUMNS = {"failed_logins": 0, "failed_attempts": 0, "failed_login_count": 0,
                   "locked_until": None, "lockout_until": None, "locked": 0}


class RecoveryRefused(Exception):
    """The action was not carried out; the message says why."""


def _check_password(password: str, what: str = "password") -> str:
    cleaned = str(password or "").strip()
    if len(cleaned) < MIN_PASSWORD:
        raise RecoveryRefused("The new %s needs at least %d characters." % (what, MIN_PASSWORD))
    return cleaned


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _need_users_table(db: ConnectionDB):
    if not db.table_exists("ut_users"):
        raise RecoveryRefused("The studio database has no accounts table yet - Slate has "
                              "never been opened against it. Open Slate once (it signs in "
                              "with the first-run account), or use 'Create administrator'.")


# ---------------------------------------------------------------- (a) reset

def reset_account(conn, username: str, new_password: str) -> List[str]:
    """New password (to be changed at next sign-in); switched back on; lockouts cleared."""
    password = _check_password(new_password)
    db = ConnectionDB(conn)
    _need_users_table(db)
    row = db.execute_query("SELECT * FROM ut_users WHERE LOWER(username)=LOWER(%s)",
                           (str(username).strip(),), fetch="one")
    if not row:
        raise RecoveryRefused("There is no account called %s. Use 'Create administrator' "
                              "to make one." % username)
    name = row["username"]
    sets, values, done = ["password_hash=%s"], [_hash(password)], ["New password set."]
    if db.column_exists("ut_users", "must_change_password"):
        sets.append("must_change_password=1")
        done.append("They must choose their own password at the next sign-in.")
    from slate.core.domain.people import switched_off
    if db.column_exists("ut_users", "active") and switched_off(row.get("active")):
        sets += ["active=1", "deactivated_on=NULL", "deactivated_by=NULL"]
        done.append("The account was deactivated; it is active again.")
    last = row.get("last_day")
    if last:
        try:
            if date.fromisoformat(str(last)[:10]) <= date.today():
                sets.append("last_day=NULL")
                done.append("Their last day (%s) had passed or is today; it was cleared."
                            % str(last)[:10])
        except ValueError:
            pass
    for column, value in LOCKOUT_COLUMNS.items():
        if db.column_exists("ut_users", column):
            sets.append("%s=%%s" % column)
            values.append(value)
            done.append("Cleared %s." % column)
    values.append(name)
    db.execute_update("UPDATE ut_users SET %s WHERE username=%%s" % ", ".join(sets),
                      tuple(values))
    _audit(db, name, "Recovery tool: password reset" )
    logger.warning("Recovery: password reset for %s.", name)
    return ["%s: " % name + line for line in done]


# ------------------------------------------------------------ (b) the admin

USERS_DDL = """
    CREATE TABLE IF NOT EXISTS ut_users (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        job_title TEXT DEFAULT '',
        roles TEXT,
        profile_pic_path TEXT DEFAULT '',
        last_synced TEXT
    )"""
ROLES_DDL = "CREATE TABLE IF NOT EXISTS ut_roles (role_name TEXT PRIMARY KEY, permissions TEXT)"
ADMIN_ROLE = "Developer"


def restore_admin(conn, username: str, new_password: str, app_role: Optional[str] = None) -> List[str]:
    """
    Make sure ``username`` exists, is active, holds the full-access Developer
    role and has this password (to be changed at the next sign-in).
    """
    password = _check_password(new_password)
    uid = str(username or "").strip()
    if not uid:
        raise RecoveryRefused("Give the administrator a username.")
    db = ConnectionDB(conn)
    done = []
    created_tables = []
    for table, ddl in (("ut_roles", ROLES_DDL), ("ut_users", USERS_DDL)):
        if not db.table_exists(table):
            db.execute_update(ddl)
            created_tables.append(table)
    for column, kind in (("must_change_password", "INTEGER"), ("active", "INTEGER"),
                         ("deactivated_on", "TEXT"), ("deactivated_by", "TEXT")):
        if not db.column_exists("ut_users", column):
            db.execute_update("ALTER TABLE ut_users ADD COLUMN %s %s" % (column, kind))
    if created_tables and app_role:
        # Made by the superuser, so hand them to the workstations' account, or
        # its next migration is refused.
        from psycopg2 import sql
        with conn.cursor() as cur:
            for table in created_tables:
                cur.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(
                    sql.Identifier(table), sql.Identifier(app_role)))
    role = db.execute_query("SELECT permissions FROM ut_roles WHERE role_name=%s",
                            (ADMIN_ROLE,), fetch="one")
    perms = []
    if role:
        try:
            perms = list(json.loads(role["permissions"] or "[]"))
        except Exception:
            perms = []
    if "ALL" not in [str(p).upper() for p in perms]:
        perms = ["ALL"] + [p for p in perms if str(p).upper() != "ALL"]
        if role:
            db.execute_update("UPDATE ut_roles SET permissions=%s WHERE role_name=%s",
                              (json.dumps(perms), ADMIN_ROLE))
        else:
            db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                              (ADMIN_ROLE, json.dumps(perms + ["Admin Panel"])))
        done.append("The %s role has full access again." % ADMIN_ROLE)

    row = db.execute_query("SELECT * FROM ut_users WHERE LOWER(username)=LOWER(%s)",
                           (uid,), fetch="one")
    hashed = _hash(password)
    if row:
        name = row["username"]
        try:
            roles = json.loads(row.get("roles") or "[]")
            roles = [roles] if isinstance(roles, str) else list(roles or [])
        except Exception:
            roles = []
        if ADMIN_ROLE not in roles:
            roles = [ADMIN_ROLE] + roles
        db.execute_update(
            "UPDATE ut_users SET password_hash=%s, roles=%s, must_change_password=1, active=1, "
            "deactivated_on=NULL, deactivated_by=NULL WHERE username=%s",
            (hashed, json.dumps(roles), name))
        if db.column_exists("ut_users", "last_day"):
            db.execute_update("UPDATE ut_users SET last_day=NULL WHERE username=%s", (name,))
        for column, value in LOCKOUT_COLUMNS.items():
            if db.column_exists("ut_users", column):
                db.execute_update("UPDATE ut_users SET %s=%%s WHERE username=%%s" % column,
                                  (value, name))
        done.append("%s is an active administrator again, with the new password." % name)
    else:
        name = uid.lower()
        db.execute_update(
            "INSERT INTO ut_users (username, password_hash, display_name, job_title, roles, "
            "profile_pic_path, must_change_password, active) "
            "VALUES (%s, %s, %s, %s, %s, '', 1, 1)",
            (name, hashed, "Administrator", "Admin", json.dumps([ADMIN_ROLE])))
        done.append("Created the administrator %s with the new password." % name)
    done.append("%s must choose their own password at the next sign-in." % name)
    _audit(db, name, "Recovery tool: administrator restored")
    logger.warning("Recovery: administrator %s restored.", name)
    return done


def _audit(db: ConnectionDB, username: str, what: str) -> None:
    """Best effort: a line in the audit table, if it exists. Never the password."""
    try:
        if db.table_exists("audit_log"):
            cols = {r["column_name"] for r in db.execute_query(
                "SELECT column_name FROM information_schema.columns WHERE table_name='audit_log'",
                fetch="all") or []}
            if {"action", "user_id"} <= cols:
                values = {"action": "RECOVERY", "user_id": "recovery-tool"}
                if "details" in cols:
                    values["details"] = "%s (%s)" % (what, username)
                if "timestamp" in cols:
                    values["timestamp"] = datetime.now().isoformat(timespec="seconds")
                names = list(values)
                db.execute_update("INSERT INTO audit_log (%s) VALUES (%s)" % (
                    ", ".join(names), ", ".join(["%s"] * len(names))),
                    tuple(values[n] for n in names))
    except Exception as exc:
        logger.debug("Recovery audit line not written: %s", exc)


# --------------------------------------------------------- (c) passwords

def _write_setting(layout, key: str, value: str) -> Path:
    """Merge one setting into the server's credentials file, atomically."""
    from . import fs
    target = Path(layout.credentials_path) if layout.credentials_path else None
    if target is None:
        from .layout import credentials_file
        target = credentials_file()
    data = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            # A damaged file is replaced, and kept beside it for whoever wants
            # to see what else it held.
            import shutil
            shutil.copy2(target, target.with_name(target.name + ".damaged"))
            data = {}
    data[key] = value
    fs.write_atomic(target, json.dumps(data, indent=4))
    try:
        from slate_server.core import db_credentials
        db_credentials.reload()
    except Exception:
        pass
    return target


def workstation_instructions(new_password_hint: str = "the new password") -> List[str]:
    return [
        "On EVERY workstation, Slate now needs %s. On each one:" % new_password_hint,
        "  - open Slate; on the sign-in screen click 'Reconfigure server / database'",
        "    and type the new database password; or",
        "  - open %LOCALAPPDATA%\\Slate\\config.json in Notepad and set",
        '      "db_password": "<the new password>"   (or run setup.bat again).',
        "Until a workstation has it, that workstation cannot open Slate.",
        "Tip: if the workstations still have the OLD password and you only need them",
        "working again, set the database app password back to THAT value instead.",
    ]


def set_app_password(conn, new_password: str, layout) -> List[str]:
    """The workstations' account (ut_vfx_app) - created if it is missing."""
    password = _check_password(new_password, "database app password")
    from psycopg2 import sql
    from slate_server.core.db_credentials import application_user, database_name
    role = application_user()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        exists = bool(cur.fetchone())
        cur.execute(sql.SQL("%s ROLE {} LOGIN PASSWORD {}" % ("ALTER" if exists else "CREATE"))
                    .format(sql.Identifier(role), sql.Literal(password)))
        cur.execute(sql.SQL("ALTER ROLE {} NOSUPERUSER NOCREATEROLE NOCREATEDB")
                    .format(sql.Identifier(role)))
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database_name(),))
        if cur.fetchone():
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name()), sql.Identifier(role)))
    written = _write_setting(layout, "db_password", password)
    logger.warning("Recovery: the database app password was changed (not logged).")
    return (["The workstations' database account (%s) %s." % (
                role, "has the new password" if exists else "was created with the new password"),
             "The server's settings (%s) were updated, so its connection pool uses it after "
             "the server is restarted." % written]
            + workstation_instructions())


def set_superuser_password(conn, new_password: str, layout) -> List[str]:
    """postgres's own password, kept apart from the workstations' (db_admin_password)."""
    password = _check_password(new_password, "superuser password")
    from psycopg2 import sql
    from slate_server.core.db_credentials import admin_user
    with conn.cursor() as cur:
        cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(admin_user()), sql.Literal(password)))
    written = _write_setting(layout, "db_admin_password", password)
    logger.warning("Recovery: the superuser password was changed (not logged).")
    return ["The superuser (%s) has the new password." % admin_user(),
            "The server reads it from %s (db_admin_password), so it keeps working. "
            "Workstations are not affected - they never use this account." % written,
            "Keep this password with the Recovery Key; nobody else needs it."]


# ------------------------------------------------------------ (d) switches

def switches_off(layout, names: Optional[Iterable[str]] = None, conn=None,
                 by: str = "recovery tool") -> List[str]:
    """Force security switches off (None: all). Works without a database."""
    from slate.core.security import switches
    path = switches.force_off_locally(names, layout.switches_file, by=by,
                                      reason="turned off by the recovery tool")
    label = "every security switch" if names is None else ", ".join(names)
    done = ["Forced off on this PC: %s (%s)." % (label, path)]
    if conn is not None:
        db = ConnectionDB(conn)
        try:
            if names is None:
                changed = switches.all_off_in_db(db, by=by)
            else:
                changed = switches.apply_local_overrides(db, layout.switches_file, by=by)
            done.append("Turned off in the database for the workstations (%d changed)." % changed)
        except Exception as exc:
            done.append("The database copy was not changed (%s); the server applies the "
                        "local file when it next starts." % str(exc).splitlines()[0])
    else:
        done.append("The database is updated from this when the server next starts.")
    if conn is not None and (names is None or "split_superuser_password" in names):
        done += _unsplit_superuser(conn, layout)
    return done


def _unsplit_superuser(conn, layout) -> List[str]:
    """split_superuser_password off: postgres shares the workstations' password again."""
    from psycopg2 import sql
    from slate_server.core import db_credentials as creds
    import os
    creds.reload()
    if os.environ.get("SLATE_DB_ADMIN_PASSWORD"):
        return ["The superuser keeps its own password: SLATE_DB_ADMIN_PASSWORD sets it."]
    if not creds.has_separate_admin_password() or not creds.app_password():
        return []
    with conn.cursor() as cur:
        cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(creds.admin_user()), sql.Literal(creds.app_password())))
    _write_setting(layout, "db_admin_password", "")      # blank = "not set" (db_credentials)
    return ["The superuser (%s) has the workstations' database password again, as before "
            "split_superuser_password. If Slate Server is running in another window, "
            "restart it." % creds.admin_user()]
