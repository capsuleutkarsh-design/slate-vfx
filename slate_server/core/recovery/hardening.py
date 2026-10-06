"""
apply_hardening_step(): the only way a hardening step should be applied.

    from slate_server.core.recovery.hardening import apply_hardening_step

    result = apply_hardening_step(
        "strict_pg_hba",
        apply=lambda: write_new_rules(),                 # the change itself
        precheck={"db": db, "hba_text": proposed},       # can_still_get_in(**precheck)
        verify=lambda: can_still_get_in(client=..., server=...),   # after, for real
        rollback=None,                                   # extra undo beyond the files
        switch="strict_pg_hba", mode="log_only")
    if not result.applied:
        show(result.message)

In order:
    1. can_still_get_in(**precheck)  - "no" means nothing is touched at all;
    2. before_security_change(name)  - the snapshot to go back to;
    3. apply();
    4. verify()                      - with real connections, after the change;
    5. if apply() raised or verify() said "no": rollback(), then the snapshot's
       files are put back and the database re-reads them. The step reports
       what went wrong and that it was undone;
    6. only then is the switch recorded (on or log_only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    name: str
    applied: bool
    message: str
    snapshot: Optional[Path] = None
    rolled_back: bool = False
    details: List[str] = field(default_factory=list)


def apply_hardening_step(name: str, apply: Callable[[], object], *, precheck: dict,
                         verify: Optional[Callable[[], object]] = None,
                         rollback: Optional[Callable[[], object]] = None,
                         switch: Optional[str] = None, mode: str = "on",
                         layout=None, switch_db=None, by: str = "") -> StepResult:
    from slate.core.security import switches
    from slate.core.security.precheck import can_still_get_in
    from . import snapshots

    before = can_still_get_in(**precheck)
    if not before.ok:
        return StepResult(name, False, before.message)

    if layout is None:
        from .layout import find_layout
        layout = find_layout()
    snap = snapshots.before_security_change(name, layout)

    failure = ""
    try:
        apply()
    except Exception as exc:
        failure = "The change failed: %s" % (str(exc).splitlines()[0] if str(exc) else exc)
    if not failure and verify is not None:
        after = verify()
        if not getattr(after, "ok", bool(after)):
            failure = getattr(after, "message", "The check after the change failed.")

    if failure:
        details = []
        if rollback is not None:
            try:
                rollback()
                details.append("Undid the change.")
            except Exception as exc:
                details.append("The undo step failed: %s" % exc)
        try:
            details += snapshots.restore_snapshot(layout, snap)
        except Exception as exc:
            details.append("Putting the files back failed: %s - use Recover Slate, "
                           "'Restore last snapshot'." % exc)
        logger.error("Hardening step %s was undone: %s", name, failure)
        return StepResult(name, False, failure + " Everything was put back as it was.",
                          snap, True, details)

    if switch:
        switches.set_mode(switch, mode, by=by or "hardening", db=switch_db,
                          note="applied by %s" % name)
    logger.warning("Hardening step %s applied (snapshot %s).", name, snap.name)
    return StepResult(name, True, "Applied. Snapshot %s can undo it." % snap.name, snap)


# ------------------------------------------------- turning a switch on (or log only)
#
# Recover Slate turns switches on here, one at a time, each through
# apply_hardening_step() with the check that fits it:
#
#   split_superuser_password  postgres gets its own random password, kept on this
#                             PC only (db_admin_password in the server's settings
#                             file, where "Set superuser password" can reset it);
#                             both logins are proved before and after.
#   strict_pg_hba             postgres from this PC only; workstations only into the
#                             studio database with the app account.
#   hide_password_hashes      the hashes move to slate_secure.passwords; a throwaway
#                             account proves a real sign-in through the workstations'
#                             login before and after, and every stored hash is
#                             proved unchanged. Turning it off moves them back.
#   signed_fleet_commands     slate_secure and the studio's fleet key exist, and the
#                             workstations can read its public half only.
#   pgbouncer_hba             the same for the pool: its admin console from this PC
#                             only, workstations only into the studio database. The
#                             running pool re-reads it (RELOAD); logins through the
#                             pool are proved before and after.
#   anything else             the switch is all there is to it: at least one
#                             active administrator must still be able to sign in.
#
# Log only changes nothing but the switch: the server then logs, on its session
# poll, the connections the rule WOULD refuse (logged_refusals).

SERVER_SWITCHES = ("split_superuser_password", "strict_pg_hba", "pgbouncer_hba")
NOT_BUILT: dict = {}   # every switch in the catalogue is built
STRICT_MARK = "# strict_pg_hba:"
BEFORE_STRICT = "pg_hba.conf.before-strict"


def _logins(layout):
    """(workstation login, server login) as the settings say now, over loopback."""
    from slate_server.core import db_credentials as creds
    base = {"host": "127.0.0.1", "port": int(layout.port), "dbname": creds.database_name()}
    return (dict(base, user=creds.application_user(), password=creds.app_password()),
            dict(base, user=creds.admin_user(), password=creds.admin_password()))


def _hba_context(client, server) -> dict:
    return {"database": client["dbname"], "app_user": client["user"],
            "superuser": server["user"]}


def strict_rules() -> str:
    from slate_server.core import db_credentials as creds
    from slate_server.core.db_engine import DatabaseEngine
    su, app, db = creds.admin_user(), creds.application_user(), creds.database_name()
    lines = [
        DatabaseEngine.HBA_SIGNATURE,
        STRICT_MARK + " the superuser from this PC only; workstations only into the",
        "# studio database with the app account. Turned on in Recover Slate; turning",
        "# the switch off there puts the previous rules back.",
        "",
        'local   all      "%s"                    scram-sha-256' % su,
        'host    all      "%s"   127.0.0.1/32     scram-sha-256' % su,
        'host    all      "%s"   ::1/128          scram-sha-256' % su,
        'host    all      "%s"   0.0.0.0/0        reject' % su,
        'host    all      "%s"   ::/0             reject' % su,
        'host    "%s"  "%s"   127.0.0.1/32     scram-sha-256' % (db, app),
        'host    "%s"  "%s"   ::1/128          scram-sha-256' % (db, app),
    ]
    lines += ['host    "%s"  "%s"   %-16s scram-sha-256' % (db, app, net)
              for net in DatabaseEngine.STUDIO_NETWORKS]
    return "\n".join(lines) + "\n"


def would_refuse(rows, name: str) -> List[str]:
    """
    The connections from the network (server_facts.sessions rows) that switch
    ``name``, turned on, would refuse. Loopback ones - the pool, this server -
    are refused by neither rule.
    """
    import ipaddress
    from slate.core.security.precheck import hba_decision
    from slate_server.core.db_credentials import admin_user
    hba = (strict_rules() if name == "strict_pg_hba" else
           _pool_rules() if name == "pgbouncer_hba" else None)
    found = set()
    for row in rows or []:
        address = str(row.get("client") or "")
        try:
            if ipaddress.ip_address(address).is_loopback:
                continue
        except ValueError:
            continue
        user, database = str(row.get("user") or ""), str(row.get("database") or "")
        if hba is None:
            refused = user == admin_user()
        else:
            refused = hba_decision(hba, database=database, user=user,
                                   address=address) in (None, "reject")
        if refused:
            found.add("%s from %s into %s" % (user, address, database))
    return sorted(found)


def logged_refusals(db, rows, pooler_port: int = 0) -> List[str]:
    """
    What the server switches in log_only (or on) would refuse right now, as
    log lines. ``rows`` are the database's sessions; pgbouncer_hba looks at
    the pool's own clients (those reach the database from this PC).
    """
    from slate.core.security import switches
    from slate_server.core.pgbouncer_engine import clients
    lines = []
    for name in SERVER_SWITCHES:
        if switches.is_logging(name, db=db):
            seen = (clients(pooler_port) if pooler_port else []) \
                if name == "pgbouncer_hba" else rows
            lines += ["%s would refuse %s" % (name, who) for who in would_refuse(seen, name)]
    return lines


def _pool_rules() -> str:
    from slate_server.core import db_credentials as creds
    from slate_server.core.pgbouncer_engine import hba_rules
    return hba_rules(creds.database_name(), creds.application_user(), creds.admin_user())


def _pool(layout):
    from slate_server.core import db_credentials as creds
    from slate_server.core.pgbouncer_engine import PgBouncerEngine
    pool = PgBouncerEngine(str(layout.data_dir), db_port=int(layout.port),
                           listen_port=int(layout.pooler_port),
                           db_user=creds.application_user())
    return pool


def reload_pool(layout, old_passwords=()) -> List[str]:
    """
    Restart pool, from this process: fresh verifiers and settings. Lines to
    show. ``old_passwords``: the superuser's from before a change just made.
    """
    pool = _pool(layout)
    if not pool.is_installed():
        return []
    problem = pool.reload(layout.bin_dir / "psql.exe", old_passwords)
    if problem:
        return [problem]
    return ["The connection pool re-read its settings." if pool.is_ready() else
            "The connection pool is not running; it uses the new settings when it starts."]


def current_modes(layout) -> Optional[dict]:
    """{name: mode} as the workstations see them; None when the database cannot be read."""
    import psycopg2
    from slate.core.security import switches
    from slate.core.security.dbapi import ConnectionDB
    from slate_server.core.db_credentials import connect_kwargs
    try:
        conn = psycopg2.connect(**connect_kwargs(layout.port, connect_timeout=4))
    except Exception:
        return None
    try:
        return switches.all_modes(db=ConnectionDB(conn), override_path=layout.switches_file)
    finally:
        conn.close()


def turn_on(layout, name: str, mode: str = "on", by: str = "recovery tool") -> StepResult:
    """Turn one switch on (or log_only) through apply_hardening_step. Never raises."""
    import psycopg2
    from slate.core.security import switches
    from slate.core.security.dbapi import ConnectionDB
    from slate.core.security.precheck import can_still_get_in
    from slate_server.core import db_credentials
    from slate_server.core.pgbouncer_engine import clients
    from slate_server.core.server_facts import sessions

    name = switches._key(name)
    if name in NOT_BUILT:
        return StepResult(name, False, NOT_BUILT[name])
    if name not in switches.CATALOGUE or mode not in (switches.LOG_ONLY, switches.ON):
        return StepResult(name, False, "There is no security switch called %r." % name)
    db_credentials.reload()
    client, server = _logins(layout)
    try:
        conn = psycopg2.connect(connect_timeout=5, application_name="Slate recovery",
                                **server)
    except Exception as exc:
        return StepResult(name, False, "Not turned on: this server cannot log in to its own "
                          "database (%s). Run the health check and fix that first."
                          % (str(exc).strip().splitlines() or ["?"])[0])
    db = ConnectionDB(conn)
    probe = None
    try:
        apply, verify, rollback = (lambda: None), None, None
        if name == "hide_password_hashes" and mode == switches.ON:
            from slate_server.core import secure_schema
            try:
                probe = secure_schema.make_probe(conn)
            except Exception as exc:
                return StepResult(name, False, "Not turned on: the sign-in check could not "
                                  "be prepared (%s). Nothing was changed."
                                  % (str(exc).strip().splitlines() or ["?"])[0])
            precheck, apply, verify, rollback = _hide_hashes(conn, client, probe)
        elif name not in SERVER_SWITCHES:
            precheck = {"db": db}
            verify = lambda: can_still_get_in(db=db)                       # noqa: E731
            if name == "signed_fleet_commands":
                from slate_server.core import secure_schema
                apply = lambda: secure_schema.install(conn, client["user"])  # noqa: E731
                verify = lambda: can_still_get_in(db=db, extra={            # noqa: E731
                    "the fleet key": lambda: secure_schema.fleet_key_problem(client)})
        else:
            precheck = {"client": client, "server": server}
            if name == "strict_pg_hba":
                precheck.update(hba_text=strict_rules(),
                                hba_context=_hba_context(client, server))
            rows = (clients(layout.pooler_port) if name == "pgbouncer_hba"
                    else sessions(int(layout.port)))
            in_use = would_refuse(rows, name)
            if mode == switches.ON and in_use:
                return StepResult(name, False, (
                    "Not turned on: these connections would be refused straight away: %s. "
                    "Set those machines to the workstations' account first, or use Log "
                    "only to watch for them. Nothing was changed." % "; ".join(in_use)))
            if mode == switches.ON and name == "split_superuser_password":
                apply, verify, rollback = _split_superuser(layout, conn)
            elif mode == switches.ON and name == "pgbouncer_hba":
                apply, verify, rollback = _pool_hba(layout)
            elif mode == switches.ON:
                apply, verify = _strict_hba(layout)
        result = apply_hardening_step(name, apply, precheck=precheck, verify=verify,
                                      rollback=rollback, switch=name, mode=mode,
                                      layout=layout, switch_db=db, by=by)
        if result.applied:
            # A switch forced off on this PC would win over the database.
            switches.clear_local_override([name, "*"], layout.switches_file)
            # Written as the superuser, the table may be new and the superuser's;
            # the workstations' account must be able to read it, or every
            # workstation reads "off".
            try:
                from psycopg2 import sql
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("ALTER TABLE security_switches OWNER TO {}").format(
                        sql.Identifier(client["user"])))
            except Exception as exc:
                logger.warning("security_switches was not handed to %s: %s",
                               client["user"], exc)
            if name == "split_superuser_password" and mode == switches.ON:
                result.details += reload_pool(layout, [server["password"]])
        return result
    finally:
        if probe:
            try:
                secure_schema.drop_probe(conn, probe[0])
            except Exception as exc:
                logger.warning("The sign-in check account was not removed: %s", exc)
        conn.close()
        db_credentials.reload()


def _hide_hashes(conn, client, probe):
    """precheck, apply, verify, rollback for hide_password_hashes = on."""
    from slate.core.security.dbapi import ConnectionDB
    from slate.core.security.precheck import can_still_get_in
    from slate_server.core import secure_schema
    db = ConnectionDB(conn)
    before = secure_schema.effective_hashes(conn)
    signin = {"a real sign-in through the workstations' login":
              lambda: secure_schema.signin_problem(client, *probe)}

    def apply():
        secure_schema.install(conn, client["user"])
        secure_schema.hide_passwords(conn)

    def kept():
        return "" if secure_schema.effective_hashes(conn) == before else             "a stored password changed while it was moved"

    def verify():
        return can_still_get_in(db=db, extra=dict(signin, **{
            "every stored password kept": kept,
            "no hash readable by the workstations":
                lambda: secure_schema.readable_hashes(client)}))

    return ({"db": db, "extra": signin}, apply, verify,
            lambda: secure_schema.unhide_passwords(conn))


def _split_superuser(layout, conn):
    import secrets
    from psycopg2 import sql
    from slate.core.security.precheck import can_still_get_in
    from slate_server.core.db_credentials import admin_password, admin_user
    from . import actions
    old = admin_password()

    def apply():
        actions.set_superuser_password(conn, secrets.token_urlsafe(24), layout)

    def verify():
        client, server = _logins(layout)
        return can_still_get_in(client=client, server=server)

    def rollback():
        with conn.cursor() as cur:
            cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(admin_user()), sql.Literal(old)))
    return apply, verify, rollback


def _pool_hba(layout):
    """pgbouncer_hba on: the pool's hba file, re-read by the running pool, proved by logging in."""
    import psycopg2
    from slate.core.security.precheck import can_still_get_in
    pool = _pool(layout)
    psql = layout.bin_dir / "psql.exe"

    def apply():
        pool.conf_dir.mkdir(parents=True, exist_ok=True)
        pool.hba_path.write_text(_pool_rules(), encoding="utf-8")
        problem = pool.reload(psql)
        if problem:
            raise RuntimeError(problem)

    def verify():
        client, server = _logins(layout)
        if not pool.is_ready():
            return can_still_get_in(client=client, server=server)
        result = can_still_get_in(client=dict(client, port=int(layout.pooler_port)),
                                  server=server)
        try:
            psycopg2.connect(host="127.0.0.1", port=int(layout.pooler_port),
                             dbname="pgbouncer", user=server["user"],
                             password=server["password"], connect_timeout=5).close()
            result.checked.append("the pool's admin console from this PC")
        except Exception as exc:
            result.fail("The pool's admin console refuses this PC: %s"
                        % (str(exc).strip().splitlines() or ["?"])[0])
        return result

    def rollback():
        undo_pgbouncer_hba(layout)
    return apply, verify, rollback


def undo_pgbouncer_hba(layout) -> List[str]:
    """Turning pgbouncer_hba off: the pool checks passwords only, as before."""
    pool = _pool(layout)
    if not pool.hba_path.exists():
        return []
    pool.hba_path.unlink()
    return ["pgbouncer_hba: the pool's address rules are gone."] + reload_pool(layout)


def switch_app_password(layout, by: str = "recovery tool") -> StepResult:
    """
    Switch the workstations to the published app password, through
    apply_hardening_step: both logins proved with the password in use first,
    a snapshot, the change, both logins proved with the new one, and
    everything put back if that fails. The superuser follows when it shares
    the workstations' password. Never raises.
    """
    import psycopg2
    from psycopg2 import sql
    from slate.core.infra.local_secrets import LEARNED_TABLE, NEXT_TABLE
    from slate.core.security.precheck import can_still_get_in
    from slate_server.core import db_credentials as creds
    name = "switch app password"
    creds.reload()
    new = creds.stored().get("app_password_next")
    if not new:
        return StepResult(name, False, "No new app password is published yet. Press Publish "
                                       "first, and let the workstations learn it.")
    client, server = _logins(layout)
    old, separate = client["password"], creds.has_separate_admin_password()
    try:
        conn = psycopg2.connect(connect_timeout=5, application_name="Slate recovery", **server)
    except Exception as exc:
        return StepResult(name, False, "Not switched: this server cannot log in to its own "
                          "database (%s). Run the health check and fix that first."
                          % (str(exc).strip().splitlines() or ["?"])[0])
    conn.autocommit = True

    def set_password(password):
        with conn.cursor() as cur:
            cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(client["user"]), sql.Literal(password)))
            if not separate:
                cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(server["user"]), sql.Literal(password)))

    def apply():
        set_password(new)
        creds.store(app_password=new, app_password_next=None, app_password_previous=old)

    def verify():
        now_client, now_server = _logins(layout)
        return can_still_get_in(client=now_client, server=now_server)

    try:
        result = apply_hardening_step(name, apply, precheck={"client": client, "server": server},
                                      verify=verify, rollback=lambda: set_password(old),
                                      layout=layout, by=by)
        if result.applied:
            learned = 0
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", ("public." + LEARNED_TABLE,))
                if cur.fetchone()[0]:
                    cur.execute("SELECT count(*) FROM %s" % LEARNED_TABLE)
                    learned = cur.fetchone()[0]
                    cur.execute("DELETE FROM %s" % LEARNED_TABLE)
                cur.execute("DELETE FROM %s" % NEXT_TABLE)
            result.details += reload_pool(layout, [server["password"]])
            result.details += [
                "The workstations now use the new password. %d had learned it and follow "
                "on their own." % learned,
                "Any other workstation: Reconfigure server / database on its sign-in screen, "
                "with the password Show app password gives.",
                "To go back: Set app password (workstations) with the old one (Show app "
                "password lists it)."]
        return result
    finally:
        conn.close()
        creds.reload()


def _strict_hba(layout):
    import shutil
    from slate.core.security.precheck import can_still_get_in

    def apply():
        if STRICT_MARK not in layout.pg_hba.read_text(encoding="utf-8"):
            shutil.copy2(layout.pg_hba, layout.data_dir / BEFORE_STRICT)
        with open(layout.pg_hba, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(strict_rules())
        if not layout.engine()._reload_configuration():
            raise RuntimeError("the database did not re-read its access rules")

    def verify():
        client, server = _logins(layout)
        return can_still_get_in(client=client, server=server,
                                hba_text=layout.pg_hba.read_text(encoding="utf-8"),
                                hba_context=_hba_context(client, server))
    return apply, verify


def undo_strict_pg_hba(layout) -> List[str]:
    """Turning strict_pg_hba off: the rules from before it go back. File only."""
    try:
        if STRICT_MARK not in layout.pg_hba.read_text(encoding="utf-8"):
            return []
    except OSError:
        return []
    kept = layout.data_dir / BEFORE_STRICT
    text = (kept.read_text(encoding="utf-8") if kept.is_file()
            else layout.engine().standard_rules())
    with open(layout.pg_hba, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    engine = layout.engine()
    if engine.is_ready():
        engine._reload_configuration()
    return ["strict_pg_hba: the access rules from before it are back (%s)."
            % ("the kept copy" if kept.is_file() else "Slate's standard rules")]
