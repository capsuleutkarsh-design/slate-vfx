"""
A plain health check: what is wrong, in sentences, and what to do about it.

Read-only - it changes nothing and needs no key - so it is the first thing the
recovery tool shows. Each line is a Check with a status:

    ok     fine
    info   worth knowing, nothing to do
    warn   something to look at; people can probably still work
    fail   this is why people cannot get in

    for check in health_check(find_layout()):
        print(check.status, check.title, check.message, check.fix)
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import fs, key as recovery_key, marker, snapshots
from .layout import ServerLayout, server_pc_problem

logger = logging.getLogger(__name__)

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
STUDIO_NETWORKS = ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")


@dataclass
class Check:
    title: str
    status: str
    message: str
    fix: str = ""

    def line(self) -> str:
        mark = {OK: "OK  ", INFO: "INFO", WARN: "WARN", FAIL: "FAIL"}.get(self.status, "?")
        text = "[%s] %s: %s" % (mark, self.title, self.message)
        if self.fix:
            text += "\n       What to do: %s" % self.fix
        return text


def _first_line(exc) -> str:
    try:
        from slate_server.core.server_facts import explain
        return explain(exc)
    except Exception:
        text = str(exc).strip().splitlines()
        return text[0] if text else exc.__class__.__name__


def current_addresses() -> List[str]:
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def record_server_state(layout: ServerLayout) -> None:
    """At every successful start: this PC's addresses, so a change can be spotted later."""
    try:
        fs.ensure_private_dir(layout.recovery_dir)
        fs.write_json(layout.state_file, {
            "addresses": current_addresses(), "port": int(layout.port),
            "pooler_port": int(layout.pooler_port), "data_dir": str(layout.data_dir),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "recorded_epoch": time.time(),
        })
    except Exception as exc:
        logger.debug("Server state not recorded: %s", exc)


# ------------------------------------------------------------------ checks

def _check_server_pc(layout, out):
    problem = server_pc_problem(layout)
    if problem:
        out.append(Check("This PC", FAIL, problem,
                         "Run the recovery tool on the server PC, where the database lives."))
    else:
        out.append(Check("This PC", OK, "This is the server PC; the database is in %s."
                         % layout.data_dir))
    return not problem


def _check_settings(layout, out):
    for note in layout.notes:
        if "cannot be read" in note:
            out.append(Check("Server settings", FAIL, note,
                             "Restore the last snapshot (it keeps a copy), or delete the file "
                             "and set the database folder again in Slate Server's Settings."))
        else:
            out.append(Check("Server settings", WARN, note,
                             "The server uses its defaults. Open Slate Server's Settings and "
                             "save, or restore the last snapshot."))
    if not layout.notes:
        out.append(Check("Server settings", OK, "%s reads correctly." % layout.settings_path))
    try:
        from slate_server.core import db_credentials
        broken = []
        for path in db_credentials._config_layers():
            if path.is_file():
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    broken.append(str(path))
        if broken:
            out.append(Check("Password settings", FAIL,
                             "These settings files are damaged and are being ignored: %s"
                             % ", ".join(broken),
                             "Restore the last snapshot, or set the passwords again with "
                             "the recovery tool."))
        elif not db_credentials.app_password():
            out.append(Check("Password settings", FAIL,
                             "No database password is configured on this server.",
                             "Use 'Set database app password' in the recovery tool."))
        else:
            out.append(Check("Password settings", OK, "The server has its database passwords%s."
                             % (" (a separate superuser password is set)"
                                if db_credentials.has_separate_admin_password() else "")))
    except Exception as exc:
        out.append(Check("Password settings", WARN, "Could not be read: %s" % exc))


def _check_window(layout, out):
    open_window = marker.read_marker(layout.data_dir)
    if open_window is None:
        out.append(Check("Temporary way in", OK, "Closed, as it should be."))
    elif marker.is_live(open_window):
        out.append(Check("Temporary way in", INFO,
                         "A recovery is running right now (it closes itself)."))
    else:
        out.append(Check("Temporary way in", FAIL,
                         "An interrupted recovery left the access rules open on this PC "
                         "(since %s)." % open_window.get("opened_at", "?"),
                         "Start Slate Server or the recovery tool - either closes it at once."))


def _check_hba(layout, out):
    try:
        text = layout.pg_hba.read_text(encoding="utf-8")
    except OSError as exc:
        out.append(Check("Access rules", FAIL, "pg_hba.conf cannot be read: %s" % exc,
                         "Restore the last snapshot."))
        return
    from slate.core.security.precheck import HBA_SIGNATURE, hba_decision
    live = [l.split("#", 1)[0].split() for l in text.splitlines()]
    trusts = [l for l in live if l and l[-1].lower() == "trust"]
    if not text.startswith(HBA_SIGNATURE):
        out.append(Check("Access rules", WARN,
                         "pg_hba.conf was not written by Slate (its first line is missing), "
                         "so the server's automatic repair will not touch it.",
                         "That is fine if your IT set it up. The recovery tool still works."))
    elif trusts and marker.read_marker(layout.data_dir) is None:
        out.append(Check("Access rules", WARN,
                         "pg_hba.conf lets some connections in without a password (trust).",
                         "Starting Slate Server with its password set replaces these rules."))
    else:
        out.append(Check("Access rules", OK, "pg_hba.conf is Slate's and asks for passwords."))
    from slate_server.core.db_credentials import application_user, database_name
    for address in current_addresses():
        method = hba_decision(text, database=database_name(), user=application_user(),
                              address=address)
        if method in (None, "reject"):
            out.append(Check("Access rules", FAIL,
                             "This PC's address %s is not in any network the access rules let "
                             "in, so workstations on that network are refused." % address,
                             "Give the server an address in the studio network, or ask for "
                             "the rules to include it."))


def _connect(layout, user, password, dbname=None):
    import psycopg2
    from slate_server.core.db_credentials import database_name
    kwargs = {"host": "127.0.0.1", "port": int(layout.port), "user": user,
              "dbname": dbname or database_name(), "connect_timeout": 4,
              "application_name": "Slate health check"}
    if password:
        kwargs["password"] = password
    return psycopg2.connect(**kwargs)


def _check_database(layout, out, conn=None):
    engine = layout.engine()
    running = engine.is_ready()
    if not running:
        out.append(Check("Database", WARN, "The database is not running on port %d." % layout.port,
                         "Start it from Slate Server. The recovery tool can still work: it "
                         "starts the database on this PC only for the repair."))
        return None
    out.append(Check("Database", OK, "Running on port %d." % layout.port))

    from slate_server.core.db_credentials import (admin_password, admin_user, app_password,
                                                  application_user)
    server_conn = None
    try:
        server_conn = _connect(layout, admin_user(), admin_password())
        out.append(Check("Server login", OK, "The server can log in to its own database."))
    except Exception as exc:
        out.append(Check("Server login", FAIL, "The server cannot log in: %s" % _first_line(exc),
                         "Use 'Set superuser password' in the recovery tool. It needs only "
                         "the Recovery Key, not the old password."))
    try:
        _connect(layout, application_user(), app_password()).close()
        out.append(Check("Workstation login", OK,
                         "The workstations' account (%s) accepts the configured password."
                         % application_user()))
    except Exception as exc:
        out.append(Check("Workstation login", FAIL,
                         "The workstations' account is refused: %s" % _first_line(exc),
                         "Use 'Set database app password' and give the password the "
                         "workstations already have."))
    return conn or server_conn


def _check_accounts(conn, out):
    if conn is None:
        out.append(Check("Administrator", INFO,
                         "Not checked - the tool could not get in without the Recovery Key.",
                         "Unlock with the Recovery Key to check the accounts."))
        return
    try:
        from slate.core.security import admin_guard
        from slate.core.security.dbapi import ConnectionDB
        users, perms = admin_guard.read_state(ConnectionDB(conn))
        admins = admin_guard.administrators(users, perms)
    except Exception as exc:
        out.append(Check("Administrator", WARN, "The accounts could not be read: %s"
                         % _first_line(exc)))
        return
    if admins:
        out.append(Check("Administrator", OK, "Active administrator(s): %s." % ", ".join(admins)))
    else:
        out.append(Check("Administrator", FAIL,
                         "There is no active administrator with a password - nobody can "
                         "manage Slate.", "Use 'Create or restore administrator'."))


def _check_pool(layout, out):
    try:
        from slate_server.core.pgbouncer_engine import PgBouncerEngine
        pool = PgBouncerEngine(str(layout.data_dir), db_port=layout.port,
                               listen_port=layout.pooler_port)
    except Exception as exc:
        out.append(Check("Connection pool", INFO, "Not checked: %s" % exc))
        return
    if not pool.is_installed():
        out.append(Check("Connection pool", INFO, "Not installed; workstations connect directly."))
        return
    leftover = pool._leftover_pid()
    if leftover and not layout.engine().is_ready():
        out.append(Check("Connection pool", WARN,
                         "A connection pool (process %d) is still running from an earlier start, "
                         "while the database is down." % leftover,
                         "Starting Slate Server replaces it, or use 'Stop leftover pool'."))
    elif pool.is_ready():
        out.append(Check("Connection pool", OK, "Answering on port %d." % layout.pooler_port))
    else:
        out.append(Check("Connection pool", INFO, "Not running; workstations connect directly."))


def _check_address(layout, out):
    state = fs.read_json(layout.state_file) or {}
    now = current_addresses()
    before = state.get("addresses") or []
    if before and now and not set(before) & set(now):
        out.append(Check("Network address", WARN,
                         "This PC's address changed: it was %s and is now %s."
                         % (", ".join(before), ", ".join(now)),
                         "Workstations that were set to the old address must be pointed at the "
                         "new one: on each, click 'Reconfigure server / database' on the "
                         "sign-in screen (or set db_host in their config.json). Best: give "
                         "the server a fixed address (a DHCP reservation)."))
    else:
        out.append(Check("Network address", OK, "%s." % (", ".join(now) or "no network address")))
    for address in now:
        try:
            ip = ipaddress.ip_address(address)
            if not any(ip in ipaddress.ip_network(n) for n in STUDIO_NETWORKS):
                out.append(Check("Network address", WARN,
                                 "%s is not a private studio address." % address,
                                 "The access rules only let in 10.x, 172.16-31.x and 192.168.x."))
        except ValueError:
            continue


def _check_clock(layout, out):
    now = time.time()
    newest, what = 0.0, ""
    candidates = [layout.pg_log, layout.data_dir / "global" / "pg_control",
                  layout.data_dir / "postmaster.pid", layout.state_file, layout.log_file]
    latest = snapshots.latest_snapshot(layout)
    if latest:
        candidates.append(latest / "manifest.json")
    for path in candidates:
        try:
            stamp = Path(path).stat().st_mtime
        except OSError:
            continue
        if stamp > newest:
            newest, what = stamp, str(path)
    if newest - now > 3600:
        out.append(Check("Clock", WARN,
                         "This PC's clock is behind: %s was written %.0f hour(s) in the future."
                         % (what, (newest - now) / 3600),
                         "Set the date and time (Windows Settings > Time). Recovery still works; "
                         "people's last days and leave are judged by this clock."))
    else:
        out.append(Check("Clock", OK, "%s." % datetime.now().strftime("%Y-%m-%d %H:%M")))


def _check_recovery(layout, out):
    if recovery_key.has_key(layout):
        made = recovery_key.key_info(layout).get("created_at", "?")
        out.append(Check("Recovery Key", OK, "Set (made %s)." % made))
    else:
        out.append(Check("Recovery Key", WARN, "This server has no Recovery Key yet.",
                         "Open Slate Server once, or run 'Recover Slate init-key', and print it."))
    latest = snapshots.latest_snapshot(layout)
    if latest:
        manifest = snapshots.read_manifest(latest)
        out.append(Check("Snapshots", OK, "Latest: %s (%s, before %s)."
                         % (latest.name.split("_", 1)[0], manifest.get("created_at", "?"),
                            manifest.get("name", "?"))))
    else:
        out.append(Check("Snapshots", INFO, "None taken yet."))
    try:
        from slate.core.security import switches
        forced = switches.local_overrides(layout.switches_file)
        if forced:
            out.append(Check("Security switches", INFO, "Forced off on this PC: %s."
                             % ("all" if "*" in forced else ", ".join(sorted(forced)))))
    except Exception:
        pass


def health_check(layout: ServerLayout, conn=None) -> List[Check]:
    """Every check, in the order a person would want to read them."""
    out: List[Check] = []
    here = _check_server_pc(layout, out)
    _check_settings(layout, out)
    if here:
        _check_window(layout, out)
        _check_hba(layout, out)
    db_conn = None
    try:
        db_conn = _check_database(layout, out, conn) if here else None
    except Exception as exc:
        out.append(Check("Database", WARN, "Could not be checked: %s" % exc))
    _check_accounts(db_conn, out)
    if db_conn is not None and db_conn is not conn:
        try:
            db_conn.close()
        except Exception:
            pass
    _check_pool(layout, out)
    _check_address(layout, out)
    _check_clock(layout, out)
    _check_recovery(layout, out)
    return out


def worst(checks: List[Check]) -> str:
    order = {OK: 0, INFO: 0, WARN: 1, FAIL: 2}
    level = max((order.get(c.status, 0) for c in checks), default=0)
    return {0: OK, 1: WARN, 2: FAIL}[level]


def report(checks: List[Check]) -> str:
    head = {OK: "Everything looks right.", WARN: "Mostly fine - see the warnings.",
            FAIL: "Something is stopping people getting in - see FAIL below."}[worst(checks)]
    return head + "\n\n" + "\n".join(c.line() for c in checks)


def stop_leftover_pool(layout: ServerLayout) -> str:
    from slate_server.core.pgbouncer_engine import PgBouncerEngine
    pool = PgBouncerEngine(str(layout.data_dir), db_port=layout.port,
                           listen_port=layout.pooler_port)
    leftover = pool._leftover_pid()
    if not leftover:
        return "No leftover connection pool is running."
    pool._kill_pid(leftover)
    return "Stopped the leftover connection pool (process %d)." % leftover
