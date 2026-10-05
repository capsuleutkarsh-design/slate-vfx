"""
The lockout test lab.

Each test locks a real, throwaway PostgreSQL out in one of the ways a studio
can be locked out, and proves the recovery tool gets back in - using only "the
server PC + the Recovery Key", never an existing password.

The clusters are made with the bundled initdb in a temporary folder, listen on
127.0.0.1 only, on a free port, and are stopped and deleted afterwards. They
never touch the studio's database (5440) or its settings.

    forgotten admin password                       test_forgotten_admin_password
    every admin deleted / deactivated              test_every_admin_gone_*
    app password changed on the server only        test_app_password_changed_on_the_server_only
    superuser password unknown                     test_superuser_password_unknown
    pg_hba hardened before accounts existed        test_hardened_before_accounts_existed_*
    config.json deleted or corrupted               test_server_settings_*, test_password_settings_corrupted
    a stale PgBouncer left running                 test_a_stale_pool_*
    the server PC's IP changed                     test_the_server_address_changed
    the clock wrong by a day                       test_clock_*
    recovery interrupted halfway                   test_interrupted_recovery_*
    wrong Recovery Key                             test_wrong_key_*
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import bcrypt
import psycopg2
import pytest

from slate_server.core import db_credentials
from slate_server.core.db_engine import DatabaseEngine
from slate_server.core.recovery import (actions, fs, health, key as recovery_key, marker,
                                        snapshots)
from slate_server.core.recovery.layout import (NotServerPC, ServerLayout, assert_server_pc,
                                               bundled_bin_dir, find_layout)
from slate_server.core.recovery.session import Locked, RecoverySession
from slate_server.core.recovery.trust_window import TrustWindow, trust_rules

ROOT = Path(__file__).resolve().parent.parent
BIN = bundled_bin_dir()
APP_PASSWORD = "lab-app-pass-1"
DBNAME = "slate_lab"

pytestmark = pytest.mark.skipif(not (BIN / "initdb.exe").exists(),
                                reason="the bundled PostgreSQL is not here")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _run(*cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)


class Lab:
    """One throwaway Slate server: home, settings, cluster, recovery folder."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.data = tmp_path / "db" / "LocalDatabase"
        self.port = _free_port()
        self.pooler_port = _free_port()
        self.credentials = tmp_path / "config.json"
        self.credentials.write_text(json.dumps({"db_password": APP_PASSWORD,
                                                "db_name": DBNAME}), encoding="utf-8")
        (self.home / "slate_server_config.json").write_text(json.dumps(
            {"db_path": str(self.data), "port": self.port, "pooler_port": self.pooler_port}),
            encoding="utf-8")
        self.layout = ServerLayout(data_dir=self.data, port=self.port,
                                   pooler_port=self.pooler_port, server_home=self.home,
                                   settings_path=self.home / "slate_server_config.json",
                                   credentials_path=self.credentials)
        self.said = []
        self.cleanup = []

    # ------------------------------------------------------------ cluster
    def initdb(self):
        self.data.parent.mkdir(parents=True, exist_ok=True)
        out = _run(BIN / "initdb.exe", "-D", self.data, "-U", "postgres", "-A", "trust",
                   "-E", "utf8")
        assert out.returncode == 0, out.stderr
        engine = self.engine()
        engine._configure_network_access()
        with open(self.data / "postgresql.conf", "a", encoding="utf-8") as conf:
            conf.write("listen_addresses = '127.0.0.1'\n")      # never on the LAN

    def engine(self):
        return DatabaseEngine(str(self.data), port=self.port)

    def _pg_ctl(self, *args):
        # No pipes: postgres inherits them and the call never returns.
        subprocess.run([str(BIN / "pg_ctl.exe"), "-D", str(self.data)] + [str(a) for a in args],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def pg_start(self):
        self._pg_ctl("-w", "-t", "60", "-l", self.root / "pg.log", "start")
        assert self.engine().is_ready(), (self.root / "pg.log").read_text(errors="replace")

    def pg_stop(self):
        if (self.data / "postmaster.pid").exists():
            self._pg_ctl("-m", "immediate", "-w", "stop")

    def bootstrap(self):
        """What a healthy server start does: database, accounts, hardened rules."""
        assert self.engine()._bootstrap(self.said.append) is True, self.said

    # ------------------------------------------------------------ logins
    def connect(self, user, password=None, dbname=DBNAME):
        kwargs = dict(host="127.0.0.1", port=self.port, user=user, dbname=dbname,
                      connect_timeout=5)
        if password is not None:
            kwargs["password"] = password
        return psycopg2.connect(**kwargs)

    def can_login(self, user, password=None, dbname=DBNAME) -> bool:
        try:
            self.connect(user, password, dbname).close()
            return True
        except Exception:
            return False

    def sql(self, statement, params=None, fetch=False):
        """As the superuser, with the server's own configured password."""
        conn = self.connect("postgres", db_credentials.admin_password())
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(statement, params)
                return cur.fetchall() if fetch else None
        finally:
            conn.close()

    def seed_accounts(self):
        """The tables Slate makes, an administrator and an artist."""
        conn = self.connect("postgres", db_credentials.admin_password())
        conn.autocommit = True
        try:
            actions.restore_admin(conn, "admin", "first-admin-pass", app_role="ut_vfx_app")
            with conn.cursor() as cur:
                for column in ("last_day",):
                    cur.execute("ALTER TABLE ut_users ADD COLUMN IF NOT EXISTS %s TEXT" % column)
                cur.execute("INSERT INTO ut_roles (role_name, permissions) VALUES "
                            "('Artist', '[\"Dashboard\"]')")
                cur.execute("INSERT INTO ut_users (username, password_hash, roles, active) "
                            "VALUES ('aarav', %s, '[\"Artist\"]', 1)",
                            (bcrypt.hashpw(b"artist-pass", bcrypt.gensalt()).decode(),))
                cur.execute("UPDATE ut_users SET must_change_password=0")
        finally:
            conn.close()

    def user(self, name):
        rows = self.sql("SELECT username, password_hash, roles, active, last_day, "
                        "must_change_password FROM ut_users WHERE username=%s", (name,), True)
        if not rows:
            return None
        keys = ("username", "password_hash", "roles", "active", "last_day",
                "must_change_password")
        return dict(zip(keys, rows[0]))

    def hba(self) -> str:
        return (self.data / "pg_hba.conf").read_text(encoding="utf-8")

    # ------------------------------------------------------------ recovery
    def make_key(self) -> str:
        return recovery_key.create_first_key(self.layout)

    def session(self, key=None) -> RecoverySession:
        session = RecoverySession(self.layout, say=self.said.append, window_seconds=60)
        if key:
            session.unlock(key)
        return session

    def admins(self):
        from slate.core.security import admin_guard
        from slate.core.security.dbapi import ConnectionDB
        conn = self.connect("postgres", db_credentials.admin_password())
        try:
            return admin_guard.administrators(*admin_guard.read_state(ConnectionDB(conn)))
        finally:
            conn.close()


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    monkeypatch.delenv("SLATE_DB_PASSWORD", raising=False)
    monkeypatch.delenv("SLATE_DB_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SLATE_DB_PATH", raising=False)
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setattr(recovery_key, "SCRYPT_N", 2 ** 12)        # fast, same code path
    the_lab = Lab(tmp_path)
    monkeypatch.setattr(db_credentials, "_config_layers", lambda: [the_lab.credentials])
    db_credentials.reload()
    the_lab.initdb()
    the_lab.pg_start()
    yield the_lab
    try:
        from slate_server.core.pgbouncer_engine import PgBouncerEngine
        pool = PgBouncerEngine(str(the_lab.data), db_port=the_lab.port,
                               listen_port=the_lab.pooler_port)
        pool.stop()
    except Exception:
        pass
    the_lab.pg_stop()
    for undo in the_lab.cleanup:
        undo()
    db_credentials._cache = None


@pytest.fixture
def healthy(lab):
    """A hardened, working server with accounts and a Recovery Key."""
    lab.bootstrap()
    lab.seed_accounts()
    lab.key = lab.make_key()
    lab.hardened = lab.hba()
    assert "trust" not in "".join(l.split("#")[0] for l in lab.hardened.splitlines())
    return lab


def _no_window_left(lab, before_hba):
    assert lab.hba() == before_hba, "the access rules must be exactly as they were"
    assert marker.read_marker(lab.data) is None, "the marker must be gone"
    assert not lab.can_login("postgres"), "no password-less way in may remain"


slow = pytest.mark.slow


# ===================================================== 1. forgotten password

@slow
def test_forgotten_admin_password(healthy):
    lab = healthy
    lab.sql("UPDATE ut_users SET password_hash=%s WHERE username='admin'",
            (bcrypt.hashpw(b"nobody-remembers", bcrypt.gensalt()).decode(),))
    session = lab.session(lab.key)
    lines = session.reset_password("admin", "Brand-new-pass1")
    assert any("New password set" in l for l in lines)
    row = lab.user("admin")
    assert bcrypt.checkpw(b"Brand-new-pass1", row["password_hash"].encode())
    assert row["must_change_password"] == 1
    _no_window_left(lab, lab.hardened)
    assert snapshots.latest_snapshot(lab.layout) is not None, "a snapshot was kept first"


@slow
def test_a_reset_also_switches_the_account_back_on(healthy):
    lab = healthy
    lab.sql("UPDATE ut_users SET active=0, last_day='2001-01-01' WHERE username='admin'")
    lab.session(lab.key).reset_password("admin", "Brand-new-pass1")
    row = lab.user("admin")
    assert str(row["active"]) == "1" and row["last_day"] is None


# ================================================= 2. every admin gone

@slow
def test_every_admin_gone_deleted_in_the_database(healthy):
    lab = healthy
    lab.sql("DELETE FROM ut_users WHERE username='admin'")
    assert lab.admins() == []
    lab.session(lab.key).restore_admin("admin", "Brand-new-pass1")
    assert lab.admins() == ["admin"]
    _no_window_left(lab, lab.hardened)


@slow
def test_every_admin_gone_deactivated_and_demoted(healthy):
    lab = healthy
    lab.sql("UPDATE ut_users SET active=0, roles='[\"Artist\"]' WHERE username='admin'")
    lab.sql("UPDATE ut_roles SET permissions='[\"Dashboard\"]' WHERE role_name='Developer'")
    assert lab.admins() == []
    lab.session(lab.key).restore_admin("admin", "Brand-new-pass1")
    assert lab.admins() == ["admin"]
    row = lab.user("admin")
    assert "Developer" in row["roles"] and str(row["active"]) == "1"


@slow
def test_every_admin_gone_the_guard_refuses_it_first(healthy):
    """Through Slate itself it cannot happen at all (the guard, on this database)."""
    from slate.core.security import admin_guard
    from slate.core.security.dbapi import ConnectionDB
    lab = healthy
    conn = lab.connect("ut_vfx_app", APP_PASSWORD)
    try:
        db = ConnectionDB(conn)
        why = admin_guard.check(db, lambda users, perms: users.pop("admin"))
        assert "no active administrator" in why
    finally:
        conn.close()


# =================================== 3. app password changed on the server only

@slow
def test_app_password_changed_on_the_server_only(healthy):
    lab = healthy
    lab.sql("ALTER ROLE ut_vfx_app PASSWORD 'rotated-on-server'")
    assert not lab.can_login("ut_vfx_app", APP_PASSWORD), "workstations are locked out"
    checks = health.health_check(lab.layout)
    assert any(c.title == "Workstation login" and c.status == health.FAIL for c in checks)

    # Recovery: give the database the password the workstations already have.
    lines = lab.session(lab.key).set_app_password(APP_PASSWORD)
    assert lab.can_login("ut_vfx_app", APP_PASSWORD)
    assert any("EVERY workstation" in l for l in lines), "and it says what to do on them"
    assert json.loads(lab.credentials.read_text())["db_password"] == APP_PASSWORD
    _no_window_left(lab, lab.hardened)


# =========================================== 4. superuser password unknown

@slow
def test_superuser_password_unknown(healthy):
    lab = healthy
    lab.sql("ALTER ROLE postgres PASSWORD 'lost-for-ever'")
    assert not lab.can_login("postgres", db_credentials.admin_password())
    checks = health.health_check(lab.layout)
    assert any(c.title == "Server login" and c.status == health.FAIL for c in checks)

    lab.session(lab.key).set_superuser_password("separate-super-1")
    db_credentials.reload()
    assert db_credentials.admin_password() == "separate-super-1"
    assert db_credentials.app_password() == APP_PASSWORD, "kept apart"
    assert lab.can_login("postgres", "separate-super-1")
    assert lab.can_login("ut_vfx_app", APP_PASSWORD), "workstations untouched"
    # And the server's own start keeps the two apart from now on.
    assert lab.engine()._bootstrap(lab.said.append) is True
    assert lab.can_login("postgres", "separate-super-1")
    assert lab.can_login("ut_vfx_app", APP_PASSWORD)
    assert not lab.can_login("postgres", APP_PASSWORD)


# ============================== 5. pg_hba hardened before accounts existed

def _historic_lockout(lab):
    """What the frozen build did: harden a cluster with no passwords set."""
    engine = lab.engine()
    engine._ensure_slate_database()
    engine._harden_access()
    assert "trust" not in "".join(l.split("#")[0] for l in lab.hba().splitlines())
    assert not lab.can_login("postgres", APP_PASSWORD, dbname="postgres")


@slow
def test_hardened_before_accounts_existed_the_tool_gets_back_in(lab):
    _historic_lockout(lab)
    hardened = lab.hba()
    key = lab.make_key()
    session = lab.session(key)
    session.set_superuser_password("separate-super-1")
    session.set_app_password(APP_PASSWORD)
    db_credentials.reload()
    _no_window_left(lab, hardened)
    assert lab.can_login("postgres", "separate-super-1", dbname="postgres")
    assert lab.can_login("ut_vfx_app", APP_PASSWORD, dbname="postgres")


@slow
def test_hardened_before_accounts_existed_the_server_repairs_itself_and_closes_up(lab):
    """The automatic repair now writes the marker first and never leaves it open."""
    _historic_lockout(lab)
    assert lab.engine()._bootstrap(lab.said.append) is True, lab.said
    assert marker.read_marker(lab.data) is None
    assert "trust" not in "".join(l.split("#")[0] for l in lab.hba().splitlines())
    assert lab.can_login("postgres", APP_PASSWORD)


@slow
def test_a_failed_automatic_repair_does_not_leave_the_window_open(lab, monkeypatch):
    _historic_lockout(lab)
    hardened = lab.hba()
    engine = lab.engine()
    monkeypatch.setattr(engine, "_ensure_application_role", lambda: False)
    assert engine._bootstrap(lab.said.append) is False
    assert lab.hba() == hardened and marker.read_marker(lab.data) is None


# ================================== 6. config.json deleted or corrupted

@slow
def test_server_settings_corrupted_are_found_and_restored(healthy):
    lab = healthy
    from slate_server.core import server_home
    server_home.remember_last_good(str(lab.home), lab.data, lab.port, lab.pooler_port)
    snapshots.before_security_change("baseline", lab.layout)
    good = (lab.home / "slate_server_config.json").read_text()
    (lab.home / "slate_server_config.json").write_text("{ this is not json", encoding="utf-8")

    found = find_layout(server_home_dir=lab.home, credentials_path=lab.credentials)
    assert found.data_dir == lab.data and found.port == lab.port, "still found the database"
    checks = health.health_check(found)
    assert any(c.title == "Server settings" and c.status == health.FAIL for c in checks)

    lab.session(lab.key).restore_latest_snapshot()
    assert (lab.home / "slate_server_config.json").read_text() == good


@slow
def test_server_settings_deleted_the_database_is_still_found(healthy):
    lab = healthy
    from slate_server.core import server_home
    server_home.remember_last_good(str(lab.home), lab.data, lab.port, lab.pooler_port)
    (lab.home / "slate_server_config.json").unlink()
    found = find_layout(server_home_dir=lab.home, credentials_path=lab.credentials)
    assert found.data_dir == lab.data and found.port == lab.port
    assert_server_pc(found)


@slow
def test_password_settings_corrupted(healthy):
    lab = healthy
    snapshots.before_security_change("baseline", lab.layout)
    good = lab.credentials.read_text()
    lab.credentials.write_text("{ broken", encoding="utf-8")
    db_credentials.reload()
    checks = health.health_check(lab.layout)
    assert any(c.title == "Password settings" and c.status == health.FAIL for c in checks)
    # Either restore the snapshot...
    lab.session(lab.key).restore_latest_snapshot()
    assert lab.credentials.read_text() == good
    # ...or, with no snapshot at all, set the password again from the tool.
    lab.credentials.write_text("{ broken", encoding="utf-8")
    lab.session(lab.key).set_app_password(APP_PASSWORD)
    db_credentials.reload()
    assert db_credentials.app_password() == APP_PASSWORD


# ============================================= 7. a stale PgBouncer

@slow
def test_a_stale_pool_is_replaced_on_the_next_start(healthy, monkeypatch):
    from slate_server.core.pgbouncer_engine import PgBouncerEngine
    lab = healthy

    def pool():
        return PgBouncerEngine(str(lab.data), db_port=lab.port, listen_port=lab.pooler_port,
                               dbname=DBNAME, db_user="ut_vfx_app", db_password=APP_PASSWORD)

    render = PgBouncerEngine._render_ini
    # Loopback only in the lab - this test PC is on a real network.
    monkeypatch.setattr(PgBouncerEngine, "_render_ini",
                        lambda self: render(self).replace("listen_addr = *",
                                                          "listen_addr = 127.0.0.1"))
    first = pool()
    if not first.is_installed():
        pytest.skip("PgBouncer is not bundled here")
    assert first.start(psql_exe=BIN / "psql.exe"), "the pool should start"
    old_pid = int((lab.data.parent / "pgbouncer" / "pgbouncer.pid").read_text().strip())

    # The window was closed without stopping the pool, then the database
    # stopped: the health check names it.
    lab.pg_stop()
    checks = health.health_check(lab.layout)
    assert any(c.title == "Connection pool" and c.status == health.WARN for c in checks)
    lab.pg_start()

    # A new server run (a new engine object knows nothing of the old process)
    # finds the leftover by its pid file and replaces it.
    second = pool()
    assert second.start(psql_exe=BIN / "psql.exe")
    new_pid = int((lab.data.parent / "pgbouncer" / "pgbouncer.pid").read_text().strip())
    assert new_pid != old_pid
    conn = psycopg2.connect(host="127.0.0.1", port=lab.pooler_port, dbname=DBNAME,
                            user="ut_vfx_app", password=APP_PASSWORD, connect_timeout=5)
    conn.close()
    second.stop()


# ============================================= 8. the server's IP changed

@slow
def test_the_server_address_changed(healthy):
    lab = healthy
    fs.write_json(lab.layout.state_file, {"addresses": ["10.250.250.250"]})
    checks = health.health_check(lab.layout)
    address = [c for c in checks if c.title == "Network address"]
    assert address[0].status == health.WARN and "10.250.250.250" in address[0].message
    assert "Reconfigure" in address[0].fix
    # Recovery itself does not depend on the address: loopback only.
    lab.session(lab.key).reset_password("aarav", "artist-pass-2")
    assert "host    all   postgres   127.0.0.1/32     trust" in trust_rules()


# ============================================= 9. the clock wrong by a day

def test_clock_a_day_out_never_lengthens_the_wrong_key_pause(tmp_path, monkeypatch):
    layout = ServerLayout(data_dir=tmp_path / "LocalDatabase")
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    monkeypatch.setattr(recovery_key, "SCRYPT_N", 2 ** 12)
    recovery_key.create_first_key(layout)
    now = [1_000_000.0]
    monkeypatch.setattr(recovery_key, "clock", lambda: now[0])
    for _ in range(6):
        try:
            recovery_key.verify(layout, "WRONG-KEY")
        except recovery_key.TooManyAttempts:
            now[0] += 1000
        except recovery_key.WrongKey:
            pass
    now[0] -= 86400                                    # the clock goes back a day
    assert 0 < recovery_key.wait_remaining(layout) <= recovery_key.MAX_PAUSE
    now[0] += 2 * 86400                                # and forward a day
    assert recovery_key.wait_remaining(layout) == 0


def test_clock_a_day_out_closes_a_window_rather_than_keeping_it(tmp_path):
    data = tmp_path / "LocalDatabase"
    data.mkdir()
    (data / "pg_hba.conf").write_text("original\n", encoding="utf-8")
    marker.open_marker(data, "original\n", "test", max_seconds=120)
    record = marker.read_marker(data)
    for skew in (-86400, 86400):
        shifted = dict(record, opened_epoch=record["opened_epoch"] + skew)
        assert not marker.is_live(shifted), "a window a day off is stale, so it is closed"


def test_clock_a_day_out_does_not_reorder_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    layout = ServerLayout(data_dir=tmp_path / "LocalDatabase")
    for name in ("0001_20261003-120000_first", "0002_20261002-120000_second-clock-was-back"):
        folder = layout.snapshots_dir / name
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text("{}", encoding="utf-8")
    assert snapshots.latest_snapshot(layout).name.startswith("0002_")


@slow
def test_clock_a_day_ahead_is_reported_and_recovery_still_works(healthy):
    lab = healthy
    lab.pg_log = lab.layout.pg_log
    lab.pg_log.write_text("log", encoding="utf-8")
    future = time.time() + 86400
    os.utime(lab.pg_log, (future, future))
    checks = health.health_check(lab.layout)
    assert any(c.title == "Clock" and c.status == health.WARN for c in checks)
    # An admin whose last day is "today" by a fast clock looks gone; a reset brings them back.
    from datetime import date
    lab.sql("UPDATE ut_users SET last_day=%s WHERE username='admin'", (date.today().isoformat(),))
    lab.session(lab.key).reset_password("admin", "Brand-new-pass1")
    assert lab.user("admin")["last_day"] is None


# ======================================= 10. recovery interrupted halfway

CRASH_SCRIPT = r'''
import sys, os, json
sys.path.insert(0, {root!r})
from pathlib import Path
from slate_server.core.recovery import fs
fs.RESTRICT_PERMISSIONS = False
from slate_server.core.recovery.layout import ServerLayout
from slate_server.core.recovery.trust_window import TrustWindow
layout = ServerLayout(data_dir=Path({data!r}), port={port})
window = TrustWindow(layout, reason="crash test", max_seconds=600).open()
print("OPEN", flush=True)
os._exit(9)          # the PC "loses power" with the window open
'''


def _crash_mid_recovery(lab):
    script = lab.root / "crash.py"
    script.write_text(CRASH_SCRIPT.format(root=str(ROOT), data=str(lab.data), port=lab.port),
                      encoding="utf-8")
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                         timeout=120)
    assert "OPEN" in out.stdout, out.stderr
    assert marker.read_marker(lab.data) is not None
    assert "trust" in lab.hba(), "the crash left the window open"
    assert lab.can_login("postgres"), "...and it really lets the superuser in"


@slow
def test_interrupted_recovery_is_closed_by_the_next_server_start(healthy):
    lab = healthy
    _crash_mid_recovery(lab)
    checks = health.health_check(lab.layout)
    assert any(c.title == "Temporary way in" and c.status == health.FAIL for c in checks)
    lab.engine().start(lab.said.append)                   # the Slate Server window starting
    _no_window_left(lab, lab.hardened)
    assert any("interrupted" in line for line in lab.said)


@slow
def test_interrupted_recovery_is_closed_by_the_next_recovery_tool_start(healthy):
    lab = healthy
    _crash_mid_recovery(lab)
    lab.session(lab.key)                                   # unlocking closes it first
    _no_window_left(lab, lab.hardened)


@slow
def test_the_watchdog_closes_a_window_that_runs_too_long(healthy):
    lab = healthy
    window = TrustWindow(lab.layout, reason="hang test", max_seconds=3).open()
    assert lab.can_login("postgres")
    time.sleep(5)
    assert window.expired and window.closed
    _no_window_left(lab, lab.hardened)


@slow
def test_a_window_on_a_stopped_database_starts_it_on_loopback_and_stops_it(healthy):
    lab = healthy
    lab.pg_stop()
    lab.session(lab.key).reset_password("aarav", "artist-pass-2")
    assert not lab.engine().is_ready(), "stopped again afterwards"
    assert lab.hba() == lab.hardened and marker.read_marker(lab.data) is None


# ============================================= 11. wrong Recovery Key

@slow
def test_wrong_key_is_refused_and_changes_nothing(healthy):
    lab = healthy
    session = lab.session()
    with pytest.raises(recovery_key.WrongKey):
        session.unlock("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
    with pytest.raises(Locked):
        session.reset_password("admin", "Brand-new-pass1")
    assert lab.hba() == lab.hardened


def test_wrong_key_repeated_is_slowed_down_but_never_locked_for_good(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    monkeypatch.setattr(recovery_key, "SCRYPT_N", 2 ** 12)
    layout = ServerLayout(data_dir=tmp_path / "LocalDatabase")
    right = recovery_key.create_first_key(layout)
    now = [5_000_000.0]
    monkeypatch.setattr(recovery_key, "clock", lambda: now[0])
    for _ in range(recovery_key.FREE_ATTEMPTS):
        with pytest.raises(recovery_key.WrongKey):
            recovery_key.verify(layout, "nope")
    with pytest.raises(recovery_key.TooManyAttempts) as wait:
        recovery_key.verify(layout, right)              # even the right key waits
    assert 0 < wait.value.seconds <= recovery_key.FIRST_PAUSE
    # Fifty failures in, the pause is still capped.
    fs.write_json(layout.attempts_file, {"failures": 50, "last_failure": now[0]})
    assert recovery_key.wait_remaining(layout) == recovery_key.MAX_PAUSE
    now[0] += recovery_key.MAX_PAUSE + 1
    assert recovery_key.verify(layout, right.lower().replace("-", " ")) is True
    assert not layout.attempts_file.exists(), "a right key clears the count"


# ============================================= the rules around all of it

def test_the_key_is_stored_only_as_a_slow_hash_and_never_logged(tmp_path, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    layout = ServerLayout(data_dir=tmp_path / "LocalDatabase")
    key = recovery_key.create_first_key(layout)
    assert recovery_key.create_first_key(layout) is None, "made once"
    stored = layout.key_file.read_text()
    data = json.loads(stored)
    assert data["algorithm"] == "scrypt" and data["n"] >= 2 ** 15
    compact = recovery_key.normalise(key)
    for text in (stored, caplog.text):
        assert key not in text and compact not in text
    assert recovery_key.verify(layout, key)
    new = recovery_key.replace_key(layout, old_key=key)
    assert new != key and key not in caplog.text and new not in caplog.text
    with pytest.raises(recovery_key.WrongKey):
        recovery_key.verify(layout, key)


def test_a_lost_key_is_replaced_only_with_windows_admin_rights(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    monkeypatch.setattr(recovery_key, "SCRYPT_N", 2 ** 12)
    layout = ServerLayout(data_dir=tmp_path / "LocalDatabase")
    recovery_key.create_first_key(layout)
    with pytest.raises(PermissionError):
        recovery_key.replace_key(layout)
    from slate_server.core.recovery import layout as layout_module
    monkeypatch.setattr(layout_module, "is_windows_admin", lambda: False)
    with pytest.raises(PermissionError):
        recovery_key.replace_key(layout, as_admin=True)
    monkeypatch.setattr(layout_module, "is_windows_admin", lambda: True)
    assert recovery_key.replace_key(layout, as_admin=True)


def test_it_refuses_to_run_anywhere_but_the_server_pc(tmp_path):
    with pytest.raises(NotServerPC):
        assert_server_pc(ServerLayout(data_dir=Path(r"\\fileserver\share\LocalDatabase")))
    with pytest.raises(NotServerPC) as no_db:
        assert_server_pc(ServerLayout(data_dir=tmp_path / "nothing-here"))
    assert "no Slate database" in str(no_db.value)


def test_the_trust_window_is_loopback_and_superuser_only():
    rules = [l.split() for l in trust_rules().splitlines()
             if l.strip() and not l.startswith("#")]
    assert rules and all(r[0] == "host" and r[2] == "postgres" and r[-1] == "trust" for r in rules)
    assert {r[3] for r in rules} == {"127.0.0.1/32", "::1/128"}
    assert trust_rules().startswith("# Written by Slate Central Server.")


def test_the_private_folder_never_locks_this_account_out(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", True)
    folder = fs.ensure_private_dir(tmp_path / "slate_recovery")
    fs.write_json(folder / "probe.json", {"ok": True})
    assert fs.read_json(folder / "probe.json") == {"ok": True}


# ============================== apply_hardening_step: the rule in practice

def _server_login(lab):
    return {"host": "127.0.0.1", "port": lab.port, "dbname": DBNAME, "user": "postgres",
            "password": db_credentials.admin_password()}


@slow
def test_a_hardening_step_that_fails_the_precheck_is_not_applied(healthy):
    from slate_server.core.recovery.hardening import apply_hardening_step
    lab = healthy
    applied = []
    result = apply_hardening_step(
        "strict_pg_hba", apply=lambda: applied.append(True),
        precheck={"hba_text": "host all all 0.0.0.0/0 scram-sha-256\n"},
        layout=lab.layout)
    assert not result.applied and applied == []
    assert "first line" in result.message
    assert snapshots.latest_snapshot(lab.layout) is None, "nothing was touched"


@slow
def test_a_hardening_step_that_locks_people_out_is_undone_by_itself(healthy):
    from slate.core.security.precheck import can_still_get_in
    from slate_server.core.recovery.hardening import apply_hardening_step
    lab = healthy
    bad = "# Written by Slate Central Server.\nhost all all 0.0.0.0/0 reject\n"

    def apply():
        (lab.data / "pg_hba.conf").write_text(bad, encoding="utf-8")
        lab.engine()._reload_configuration()

    result = apply_hardening_step(
        "strict_pg_hba", apply=apply,
        precheck={"server": _server_login(lab)},                       # fine before...
        verify=lambda: can_still_get_in(server=_server_login(lab)),    # ...refused after
        switch="strict_pg_hba", layout=lab.layout)
    assert not result.applied and result.rolled_back
    assert lab.hba() == lab.hardened
    assert lab.can_login("postgres", db_credentials.admin_password())


@slow
def test_a_hardening_step_that_keeps_people_in_is_applied_and_switched_on(healthy):
    from slate.core.security import switches
    from slate.core.security.dbapi import ConnectionDB
    from slate.core.security.precheck import can_still_get_in
    from slate_server.core.recovery.hardening import apply_hardening_step
    lab = healthy
    conn = lab.connect("postgres", db_credentials.admin_password())
    try:
        db = ConnectionDB(conn)
        result = apply_hardening_step(
            "refuse_inactive_signin", apply=lambda: None,
            precheck={"db": db}, verify=lambda: can_still_get_in(db=db),
            switch="refuse_inactive_signin", mode=switches.LOG_ONLY, switch_db=db,
            layout=lab.layout)
        assert result.applied and result.snapshot is not None
        assert switches.mode("refuse_inactive_signin", db=db,
                             override_path=lab.layout.switches_file) == switches.LOG_ONLY
        # ...and the recovery tool can turn it off again, from the file alone.
        lab.session(lab.key).switches_off(["refuse_inactive_signin"])
        assert switches.mode("refuse_inactive_signin", db=db,
                             override_path=lab.layout.switches_file) == switches.OFF
    finally:
        conn.close()


# ===================================================== the command line, for real

@slow
def test_the_command_line_resets_a_password(healthy, monkeypatch, capsys):
    from slate_server.core.recovery import cli
    lab = healthy
    answers = iter([lab.key, "Typed-at-prompt1", "Typed-at-prompt1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    code = cli.main(["--data-dir", str(lab.data), "--port", str(lab.port),
                     "reset-password", "admin"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert bcrypt.checkpw(b"Typed-at-prompt1", lab.user("admin")["password_hash"].encode())
    assert lab.key not in out and "Typed-at-prompt1" not in out
    log = lab.layout.log_file.read_text(encoding="utf-8")
    assert "reset-password" in log and "Typed-at-prompt1" not in log and lab.key not in log
    _no_window_left(lab, lab.hardened)


def test_the_command_line_refuses_another_pc(tmp_path, capsys):
    from slate_server.core.recovery import cli
    code = cli.main(["--data-dir", str(tmp_path / "not-a-database"), "reset-password", "admin"])
    assert code == 1
    assert "only runs on the server PC" in capsys.readouterr().out


def test_the_health_check_runs_without_a_key_or_a_database(tmp_path, capsys):
    from slate_server.core.recovery import cli
    assert cli.main(["--data-dir", str(tmp_path / "nothing"), "health"]) == 0
    out = capsys.readouterr().out
    assert "[FAIL] This PC" in out


def test_old_snapshots_are_pruned_but_the_first_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    layout = ServerLayout(data_dir=tmp_path / "LocalDatabase")
    for n in range(1, 106):
        folder = layout.snapshots_dir / ("%04d_x_change" % n)
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text("{}", encoding="utf-8")
    assert snapshots.prune(layout, keep=100) == 4
    names = [p.name[:4] for p in snapshots.list_snapshots(layout)]
    assert names[0] == "0001" and names[1] == "0006" and names[-1] == "0105" and len(names) == 101
