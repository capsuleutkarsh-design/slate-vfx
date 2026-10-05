"""
What the recovery tool can do once it is in.

Every function here takes an open connection from a TrustWindow (the superuser,
from this PC) - none of them needs, or asks for, any existing password - and
returns plain sentences saying what was done and what the person has to do
next. Passwords are passed to PostgreSQL as literals and never logged.

    reset_account(conn, "admin", new)          (a) password, deactivation, last day
    restore_admin(conn, "admin", new)          (b) make or mend an administrator
    set_app_password(conn, new, layout)        (c) the workstations' password, now
    publish_app_password(conn)                 (c) the next one, for them to learn first
    app_password_lines(conn)                   (c) what the passwords are, for the admin
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
import socket
from datetime import date, datetime
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
    """Best effort: a line in the audit trail, if it exists. Never the password."""
    try:
        if db.execute_query("SELECT to_regclass('slate_secure.audit_trail') AS t",
                            fetch="one").get("t"):
            db.execute_update("INSERT INTO slate_secure.audit_trail (kind, username, status, "
                              "details, pc) VALUES ('RECOVERY', 'recovery-tool', 'SUCCESS', "
                              "%s, %s)", ("%s (%s)" % (what, username), socket.gethostname()))
        elif db.table_exists("audit_log"):
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

def workstation_instructions(new_password_hint: str = "the new password") -> List[str]:
    return [
        "Workstations that already have %s carry on. Any other workstation:" % new_password_hint,
        "  open Slate; on the sign-in screen click 'Reconfigure server / database'",
        "  and type it as the database password (Show app password shows it).",
        "Until a workstation has it, that workstation cannot open Slate.",
        "Tip: if the workstations still have the OLD password and you only need them",
        "working again, set the database app password back to THAT value instead.",
        "The gentle way to change it is Publish, then Switch: the workstations learn",
        "the new password first.",
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
    from slate_server.core import db_credentials as creds
    old = creds.app_password()
    keep = {"app_password": password, "app_password_previous": old if old != password else None}
    if creds.stored().get("app_password_next") == password:
        keep["app_password_next"] = None
    written = creds.store(**keep)
    logger.warning("Recovery: the database app password was changed (not logged).")
    return (["The workstations' database account (%s) %s." % (
                role, "has the new password" if exists else "was created with the new password"),
             "This server keeps it protected (%s). Press Restart pool on the Dashboard, "
             "or restart the server." % written]
            + workstation_instructions())


def publish_app_password(conn, password: Optional[str] = None) -> List[str]:
    """
    Publish the studio's next app password in the database, where the
    workstations (2.2.0 and later) learn it each time they connect. Nothing
    changes for anyone until switch_app_password (hardening.py). A random
    one unless ``password`` is given; publishing again keeps the one already
    published.
    """
    import secrets
    from psycopg2 import sql
    from slate.core.infra.local_secrets import LEARNED_TABLE, NEXT_TABLE
    from slate_server.core import db_credentials as creds
    published = creds.stored().get("app_password_next")
    new = (_check_password(password, "database app password") if password
           else published or secrets.token_urlsafe(24))
    if new == creds.app_password():
        raise RecoveryRefused("That is already the workstations' password.")
    creds.store(app_password_next=new)
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS %s (id INTEGER PRIMARY KEY DEFAULT 1 "
                    "CHECK (id = 1), password TEXT NOT NULL, published_at TEXT)" % NEXT_TABLE)
        cur.execute("CREATE TABLE IF NOT EXISTS %s (machine TEXT PRIMARY KEY, "
                    "learned_at TEXT)" % LEARNED_TABLE)
        for table in (NEXT_TABLE, LEARNED_TABLE):
            cur.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(
                sql.Identifier(table), sql.Identifier(creds.application_user())))
        if new != published:
            cur.execute("DELETE FROM %s" % LEARNED_TABLE)
        cur.execute("INSERT INTO %s (id, password, published_at) VALUES (1, %%s, %%s) "
                    "ON CONFLICT (id) DO UPDATE SET password = EXCLUDED.password, "
                    "published_at = EXCLUDED.published_at" % NEXT_TABLE,
                    (new, datetime.now().isoformat(timespec="seconds")))
    logger.warning("The next database app password was published (not logged).")
    return ["A new app password is published. Each workstation on Slate 2.2.0 or later "
            "learns it the next time it connects; nothing changes yet.",
            "Show app password lists the workstations that have it. When every one does, "
            "press Switch to the published password."]


def learned_machines(conn) -> List[str]:
    """The workstations that have learned the published password ([] when none)."""
    from slate.core.infra.local_secrets import LEARNED_TABLE
    db = ConnectionDB(conn)
    if not db.table_exists(LEARNED_TABLE):
        return []
    rows = db.execute_query("SELECT machine, learned_at FROM %s ORDER BY machine"
                            % LEARNED_TABLE, fetch="all") or []
    return ["%s (%s)" % (r["machine"], r["learned_at"]) for r in rows]


def app_password_lines(conn=None) -> List[str]:
    """For the admin's eyes only (Recover Slate, unlocked): the passwords and who has the next one."""
    from slate.core.infra.local_secrets import LEGACY_PASSWORD
    from slate_server.core import db_credentials as creds
    have = creds.stored()
    current = creds.app_password()
    lines = ["Workstations' database password now: %s" % (current or "(none on this server)")]
    if current == LEGACY_PASSWORD:
        lines.append("  That is the password every older Slate shipped with: it is public. "
                     "Publish a new one and switch to it.")
    if have.get("app_password_next"):
        lines.append("Published, not in use yet: %s" % have["app_password_next"])
        machines = learned_machines(conn) if conn is not None else []
        lines.append("Workstations that have it (%d):" % len(machines))
        lines += ["  " + m for m in machines] or ["  none yet"]
    if have.get("app_password_previous"):
        lines.append("Before the last change it was: %s" % have["app_password_previous"])
    lines.append("A new workstation: Reconfigure server / database on its sign-in screen, "
                 "and type the password in use now.")
    return lines


def set_superuser_password(conn, new_password: str, layout) -> List[str]:
    """postgres's own password, kept apart from the workstations' (db_admin_password)."""
    password = _check_password(new_password, "superuser password")
    from psycopg2 import sql
    from slate_server.core.db_credentials import admin_user
    with conn.cursor() as cur:
        cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(admin_user()), sql.Literal(password)))
    from slate_server.core import db_credentials
    written = db_credentials.store(admin_password=password)
    db_credentials.strip_from_files("db_admin_password")
    logger.warning("Recovery: the superuser password was changed (not logged).")
    return ["The superuser (%s) has the new password." % admin_user(),
            "This server keeps it protected (%s), so it keeps working. "
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
    if conn is not None and (names is None or "hide_password_hashes" in names):
        done += _unhide_passwords(conn)
    return done


def _unhide_passwords(conn) -> List[str]:
    """hide_password_hashes off: every hash back in ut_users, as before."""
    from slate_server.core import secure_schema
    if not secure_schema.is_installed(conn):
        return []
    was_on = secure_schema.is_hiding(conn)
    left = secure_schema.unhide_passwords(conn)
    if not was_on:
        return []
    return ["The password hashes are back in the accounts table, as before "
            "hide_password_hashes." + (" %d account(s) still show 'hidden:' and need their "
                                       "password reset here." % left if left else "")]


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
    creds.store(admin_password=None)
    creds.strip_from_files("db_admin_password")
    return ["The superuser (%s) has the workstations' database password again, as before "
            "split_superuser_password. If Slate Server is running in another window, "
            "restart it." % creds.admin_user()]
