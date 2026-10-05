"""
The studio's own database password, proved on real throwaway clusters.

Same lab as test_recovery_lab: bundled PostgreSQL (and PgBouncer) in a temp
folder, loopback only, a free port, deleted afterwards. Workstations are real
PostgresManager connections, each with its own (in-memory) Credential Manager.

    a studio on the old shipped password upgrades; nobody is cut off   test_an_upgrade_*
    a fresh install has no shared password                               test_a_fresh_install_*
    a workstation that lost its password is told what to do             (in the upgrade test)
    Recover Slate publishes, switches, resets, and undoes a bad switch   test_recover_slate_*
    PgBouncer holds no password and still lets the app in                test_the_pool_*
    pgbouncer_hba on, off, rolled back, refused                          test_pgbouncer_hba_*
"""

import json

import keyring
import psycopg2
import pytest

from slate.core.infra import local_secrets
from slate.core.infra.local_secrets import LEGACY_PASSWORD
from slate_server.core import db_credentials
from slate_server.core.recovery import actions, health
from tests.test_recovery_lab import APP_PASSWORD, BIN, DBNAME, healthy, lab  # noqa: F401

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not (BIN / "initdb.exe").exists(),
                                 reason="the bundled PostgreSQL is not here")]


@pytest.fixture(autouse=True)
def _no_lan(monkeypatch):
    """A refused password must not send the server search out on the network."""
    from slate.core.infra import network_discovery
    monkeypatch.setattr(network_discovery, "discover_server_details", lambda **kw: None)


def _studio_on_the_shipped_password(lab):
    """What every server before 2.2.0 left behind: both accounts on the shipped password."""
    lab.credentials.write_text(json.dumps({"db_name": DBNAME}), encoding="utf-8")
    db_credentials.reload()
    conn = lab.connect("postgres", dbname="postgres")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE ROLE ut_vfx_app LOGIN PASSWORD %s", (LEGACY_PASSWORD,))
        cur.execute("ALTER ROLE postgres PASSWORD %s", (LEGACY_PASSWORD,))
        cur.execute('CREATE DATABASE "%s" OWNER ut_vfx_app' % DBNAME)
    conn.close()
    (lab.data / "pg_hba.conf").write_text(lab.engine().standard_rules(), encoding="utf-8")
    lab.engine()._reload_configuration()
    assert not lab.can_login("postgres"), "hardened, as an older server left it"


class Workstation:
    """One PC on Slate 2.2.0: its own Credential Manager and its own config.json."""

    def __init__(self, lab, name, config_password=None):
        self.lab, self.name, self.keyring = lab, name, {}
        self.config = lab.root / ("%s_config.json" % name)
        self.config.write_text(json.dumps(
            {"db_password": config_password} if config_password else {}), encoding="utf-8")

    def stored(self, key="db_password"):
        return self.keyring.get(("Slate", key))

    def connect(self, monkeypatch):
        """Start Slate's connection the way the app does; returns where the password came from."""
        from slate.core.infra.global_config import GlobalConfig
        from slate.core.infra.postgres_manager import PostgresManager
        if GlobalConfig._instance is None:
            GlobalConfig._instance = GlobalConfig()
        settings = json.loads(self.config.read_text(encoding="utf-8"))
        for key, value in {"db_mode": "postgres", "db_host": "127.0.0.1",
                           "db_port": self.lab.port, "db_pooler_port": 0, "db_name": DBNAME,
                           "db_user": "ut_vfx_app", "db_config": {},
                           "db_password": settings.get("db_password")}.items():
            monkeypatch.setitem(GlobalConfig._instance.data, key, value)
        monkeypatch.setattr(local_secrets, "_password_files", lambda: iter([self.config]))
        monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([self.config]))
        monkeypatch.setattr(local_secrets, "machine_name", lambda: self.name)
        monkeypatch.setattr(PostgresManager, "_instance", None)
        store = type(keyring.get_keyring()).store
        others = dict(store)
        store.clear()
        store.update(self.keyring)
        manager = PostgresManager()
        try:
            manager._init_pool(retry=False)
            local_secrets.after_connect(manager, manager.password, manager.password_source)
            return manager.password_source
        finally:
            manager._close_pool()
            self.keyring = dict(store)
            store.clear()
            store.update(others)


def _published(lab):
    rows = lab.sql("SELECT password FROM app_password_next", fetch=True)
    return rows[0][0] if rows else None


# ======================================================= the upgrade

def test_an_upgrade_from_the_shipped_password_cuts_no_workstation_off(lab, monkeypatch):
    _studio_on_the_shipped_password(lab)

    # 1. Slate Server 2.2.0 starts. It proves the old password, keeps it for
    #    now, and publishes the studio's own one. Nothing changes for anybody.
    lab.bootstrap()
    db_credentials.reload()
    have = db_credentials.stored()
    assert have["app_password"] == LEGACY_PASSWORD
    new = have["app_password_next"]
    assert new and new != LEGACY_PASSWORD and len(new) >= 24
    assert _published(lab) == new
    assert lab.can_login("ut_vfx_app", LEGACY_PASSWORD), "every workstation still gets in"
    assert any(c.title == "Studio password" and c.status == health.WARN
               for c in health.health_check(lab.layout))
    key = lab.make_key()

    # 2. Workstations update to 2.2.0. One never had a password of its own
    #    (it used the shipped default); one had it in config.json.
    bare = Workstation(lab, "PC-BARE")
    plain = Workstation(lab, "PC-PLAIN", config_password=LEGACY_PASSWORD)
    assert bare.connect(monkeypatch) == local_secrets.LEGACY_SOURCE
    assert plain.connect(monkeypatch).startswith("the settings")
    for pc in (bare, plain):
        assert pc.stored("db_password_next") == new, "each learned the next password"
    assert plain.stored() == LEGACY_PASSWORD, "moved into Credential Manager..."
    assert "db_password" not in json.loads(plain.config.read_text()), "...and out of the file"
    assert bare.stored() is None, "the public default is never written anywhere"

    # 3. The admin sees who has it, and switches.
    session = lab.session(key)
    shown = "\n".join(session.show_app_password())
    assert new in shown and "PC-BARE" in shown and "PC-PLAIN" in shown
    session.switch_app_password()
    assert not lab.can_login("ut_vfx_app", LEGACY_PASSWORD), "the public password opens nothing"
    assert not lab.can_login("postgres", LEGACY_PASSWORD)
    assert lab.can_login("ut_vfx_app", new) and lab.can_login("postgres", new)
    have = db_credentials.stored()
    assert have["app_password"] == new and have["app_password_previous"] == LEGACY_PASSWORD
    assert "app_password_next" not in have and _published(lab) is None

    # 4. Both workstations follow on their own, and keep the new password.
    for pc in (bare, plain):
        assert pc.connect(monkeypatch) == "Credential Manager"
        assert pc.stored() == new and pc.stored("db_password_next") is None
        assert pc.connect(monkeypatch) == "Windows Credential Manager"

    # 5. A PC that never learned it (switched off all along, or lost its
    #    password) is told what to do, and Reconfigure brings it back.
    lost = Workstation(lab, "PC-LOST")
    with pytest.raises(ConnectionError) as refused:
        lost.connect(monkeypatch)
    assert "does not have the studio's database password" in str(refused.value)
    assert "Show app password" in str(refused.value)
    from slate.core.infra.global_config import GlobalConfig
    store = type(keyring.get_keyring()).store
    GlobalConfig.set("db_password", new)                 # what Reconfigure does
    lost.keyring = dict(store)
    assert lost.connect(monkeypatch) == "Windows Credential Manager"
    assert "db_password" not in json.loads(lost.config.read_text())


# ======================================================= a fresh install

def test_a_fresh_install_has_its_own_password_and_no_shared_one(lab):
    lab.credentials.write_text(json.dumps({"db_name": DBNAME}), encoding="utf-8")
    db_credentials.reload()
    assert db_credentials.app_password() == ""
    lab.bootstrap()
    db_credentials.reload()
    own = db_credentials.stored()["app_password"]
    assert own != LEGACY_PASSWORD and len(own) >= 24
    assert db_credentials.app_password() == own and db_credentials.admin_password() == own
    assert lab.can_login("ut_vfx_app", own) and lab.can_login("postgres", own)
    assert not lab.can_login("ut_vfx_app", LEGACY_PASSWORD)
    assert not lab.can_login("postgres", LEGACY_PASSWORD)
    assert not lab.can_login("postgres"), "hardened"
    assert "app_password_next" not in db_credentials.stored(), "nothing to roll out"
    assert not any(c.title == "Studio password" for c in health.health_check(lab.layout))
    assert own not in lab.credentials.read_text(), "never in a settings file"
    # A second start keeps the same one.
    lab.bootstrap()
    assert db_credentials.stored()["app_password"] == own


# ======================================================= Recover Slate

def test_recover_slate_publishes_switches_and_resets_the_app_password(healthy, pool,
                                                                     monkeypatch):
    lab = healthy
    session = lab.session(lab.key)
    with pytest.raises(actions.RecoveryRefused) as refused:
        session.switch_app_password()
    assert "Publish first" in str(refused.value)

    session.publish_app_password()
    new = db_credentials.stored()["app_password_next"]
    assert _published(lab) == new and lab.can_login("ut_vfx_app", APP_PASSWORD)
    pc = Workstation(lab, "PC-1", config_password=APP_PASSWORD)
    pc.connect(monkeypatch)
    assert pc.stored("db_password_next") == new
    lines = session.switch_app_password()
    assert lab.can_login("ut_vfx_app", new) and not lab.can_login("ut_vfx_app", APP_PASSWORD)
    assert "The connection pool re-read its settings." in lines
    assert _via_pool(lab, password=new) and not _via_pool(lab), "the pool follows at once"
    assert pc.connect(monkeypatch) == "Credential Manager"

    # Reset: straight back to the old one (what "Show" lists as the previous).
    assert "Before the last change it was: %s" % APP_PASSWORD in \
        "\n".join(session.show_app_password())
    session.set_app_password(APP_PASSWORD)
    assert lab.can_login("ut_vfx_app", APP_PASSWORD)
    assert db_credentials.stored()["app_password"] == APP_PASSWORD
    assert keyring.get_password("Slate", "db_password") == APP_PASSWORD, \
        "a Slate on the server PC has it too (its Credential Manager)"


def test_recover_slate_undoes_a_switch_the_server_could_not_follow(healthy, monkeypatch):
    lab = healthy
    session = lab.session(lab.key)
    session.publish_app_password()
    # The server reads its password from the environment, so after the switch
    # its login (and the workstation check) still uses the old one: fails.
    monkeypatch.setenv("SLATE_DB_PASSWORD", APP_PASSWORD)
    from slate_server.core.recovery.hardening import switch_app_password
    result = switch_app_password(lab.layout)
    monkeypatch.delenv("SLATE_DB_PASSWORD")
    assert not result.applied and result.rolled_back
    db_credentials.reload()
    assert lab.can_login("ut_vfx_app", APP_PASSWORD), "the old password is back"
    assert lab.can_login("postgres", APP_PASSWORD)
    assert db_credentials.stored()["app_password"] == APP_PASSWORD
    assert db_credentials.stored().get("app_password_next"), "still published, to try again"


# ======================================================= PgBouncer

@pytest.fixture
def pool(healthy, monkeypatch):
    from slate_server.core.pgbouncer_engine import PgBouncerEngine
    render = PgBouncerEngine._render_ini
    monkeypatch.setattr(PgBouncerEngine, "_render_ini",          # never on the LAN
                        lambda self: render(self).replace("listen_addr = *",
                                                          "listen_addr = 127.0.0.1"))
    engine = PgBouncerEngine(str(healthy.data), db_port=healthy.port,
                             listen_port=healthy.pooler_port, db_user="ut_vfx_app")
    if not engine.is_installed():
        pytest.skip("PgBouncer is not bundled here")
    assert engine.start(psql_exe=BIN / "psql.exe")
    yield engine
    engine.stop()


def _via_pool(lab, user="ut_vfx_app", password=APP_PASSWORD, dbname=DBNAME):
    try:
        conn = psycopg2.connect(host="127.0.0.1", port=lab.pooler_port, dbname=dbname,
                                user=user, password=password, connect_timeout=5)
    except Exception:
        return False
    try:
        conn.autocommit = True
        conn.cursor().execute("SHOW VERSION" if dbname == "pgbouncer" else "SELECT 1")
        return True
    finally:
        conn.close()


def test_the_pool_holds_no_password_and_still_lets_the_app_in(healthy, pool):
    ini = pool.ini_path.read_text(encoding="utf-8")
    assert APP_PASSWORD not in ini and "password=" not in ini
    userlist = pool.userlist_path.read_text(encoding="utf-8")
    assert APP_PASSWORD not in userlist and "SCRAM-SHA-256$" in userlist
    assert _via_pool(healthy)
    assert not _via_pool(healthy, password="wrong")


def test_pgbouncer_hba_on_off_and_its_rules(healthy, pool):
    from slate.core.security.precheck import hba_decision
    from slate_server.core.recovery.hardening import _pool_rules
    lab = healthy
    assert _via_pool(lab, dbname="pgbouncer"), "before: the app account reaches the console"
    session = lab.session(lab.key)
    session.turn_on("pgbouncer_hba")
    assert pool.hba_path.exists() and "auth_type = hba" in pool.ini_path.read_text()
    assert _via_pool(lab), "workstations still get in"
    assert _via_pool(lab, "postgres", APP_PASSWORD, "pgbouncer"), "the console, from this PC"
    assert not _via_pool(lab, dbname="pgbouncer"), "nothing else"
    assert not _via_pool(lab, "postgres", APP_PASSWORD), "postgres not through the pool"
    rules = _pool_rules()
    assert hba_decision(rules, database="pgbouncer", user="postgres", address="10.0.0.31") is None
    assert hba_decision(rules, database=DBNAME, user="ut_vfx_app",
                        address="10.0.0.31") == "scram-sha-256"
    from tests.test_recovery_lab import _mode
    assert _mode(lab, "pgbouncer_hba") == "on"

    session.switches_off(["pgbouncer_hba"])
    assert not pool.hba_path.exists()
    assert "auth_type = scram-sha-256" in pool.ini_path.read_text()
    assert _via_pool(lab) and _via_pool(lab, dbname="pgbouncer"), "exactly as before"
    assert _mode(lab, "pgbouncer_hba") == "off"


def test_pgbouncer_hba_that_locks_the_workstations_out_is_rolled_back(healthy, pool,
                                                                      monkeypatch):
    from slate_server.core.recovery import hardening
    lab = healthy
    shut = "\n".join(l for l in hardening._pool_rules().splitlines()
                     if not ("ut_vfx_app" in l and "127.0.0.1" in l))
    monkeypatch.setattr(hardening, "_pool_rules", lambda: shut + "\n")
    result = hardening.turn_on(lab.layout, "pgbouncer_hba")
    assert not result.applied and result.rolled_back
    assert not pool.hba_path.exists()
    assert _via_pool(lab), "the pool lets the workstations in again"
    from tests.test_recovery_lab import _mode
    assert _mode(lab, "pgbouncer_hba") == "off"


def test_pgbouncer_hba_is_refused_while_the_console_is_used_from_the_network(healthy, pool,
                                                                            monkeypatch):
    from slate_server.core import pgbouncer_engine
    lab = healthy
    monkeypatch.setattr(pgbouncer_engine, "clients", lambda port: [
        {"client": "10.0.0.31", "user": "postgres", "database": "pgbouncer"}])
    with pytest.raises(actions.RecoveryRefused) as refused:
        lab.session(lab.key).turn_on("pgbouncer_hba")
    assert "postgres from 10.0.0.31" in str(refused.value)
    assert not pool.hba_path.exists()
    lab.session(lab.key).turn_on("pgbouncer_hba", "log_only")          # changes nothing
    assert not pool.hba_path.exists()
    from slate_server.core.recovery.hardening import logged_refusals
    from slate.core.security.dbapi import ConnectionDB
    conn = lab.connect("ut_vfx_app", APP_PASSWORD)
    try:
        lines = logged_refusals(ConnectionDB(conn), [], lab.pooler_port)
    finally:
        conn.close()
    assert lines == ["pgbouncer_hba would refuse postgres from 10.0.0.31 into pgbouncer"]
