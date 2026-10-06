"""
The database half of the 2.2.0 security work, on real throwaway clusters (the
recovery lab in test_recovery_lab.py):

    slate_secure is made by the server's start      test_the_server_start_makes_slate_secure
    audit trail: append-only, the workstations'     test_the_audit_trail_is_append_only_*
      AuditLogger writes it, the screen reads it,   test_auditlogger_*
      the share only when the database cannot
    hide_password_hashes: on / log_only / off,      test_hide_password_hashes_*
      a refused precheck, a rollback
    signed_fleet_commands: the key, who gets it     test_the_fleet_key_*, test_signed_fleet_commands_*
"""

import json

import bcrypt
import hashlib
import psycopg2
import pytest

from slate.core.security import switches
from slate.core.security.dbapi import ConnectionDB
from slate_server.core import db_credentials, secure_schema
from slate_server.core.recovery import actions, hardening, snapshots
from tests.test_recovery_lab import (APP_PASSWORD, DBNAME, Lab, _mode,  # noqa: F401
                                     healthy, lab, slow)

APP = "ut_vfx_app"


def _app(lab):
    """A connection as a workstation makes it."""
    conn = lab.connect(APP, APP_PASSWORD)
    conn.autocommit = True
    return conn


def _refused(conn, statement, params=None):
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
        return False
    except psycopg2.Error:
        return True


def _users(lab, conn=None):
    """The real sign-in, over the workstations' login: UserManager on the lab database."""
    from slate.core.domain.user_manager import UserManager
    return UserManager(db=ConnectionDB(conn or _app(lab)))


# ======================================================== slate_secure itself

@slow
def test_the_server_start_makes_slate_secure(healthy):
    lab = healthy
    rows = lab.sql("SELECT n.nspname, pg_get_userbyid(n.nspowner) FROM pg_namespace n "
                   "WHERE n.nspname='slate_secure'", fetch=True)
    assert rows == [("slate_secure", "postgres")], "the superuser's, not the workstations'"
    assert lab.sql("SELECT count(*) FROM slate_secure.keys WHERE name='fleet'", fetch=True)[0][0] == 1
    # The next start changes nothing and keeps the key.
    key = lab.sql("SELECT public_key FROM slate_secure.keys", fetch=True)
    lab.bootstrap()
    assert lab.sql("SELECT public_key FROM slate_secure.keys", fetch=True) == key
    # The workstations' account still owns public, so migrations go on working.
    assert lab.sql("SELECT tableowner FROM pg_tables WHERE tablename='ut_users'",
                   fetch=True)[0][0] == APP


# ============================================================ the audit trail

@slow
def test_the_audit_trail_is_append_only_for_the_workstations(healthy):
    conn = _app(healthy)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO slate_secure.audit_trail (kind, username, status, details, at) "
                    "VALUES ('AUTH', 'aarav', 'SUCCESS', 'Login successful', '2001-01-01')")
        cur.execute("SELECT at > now() - interval '1 minute', details FROM slate_secure.audit_trail "
                    "WHERE username='aarav'")
        assert cur.fetchall() == [(True, "Login successful")], "stamped by the server's clock"
        cur.execute("SELECT count(*) FROM slate_secure.audit_trail")
        lines = cur.fetchone()[0]
    assert _refused(conn, "UPDATE slate_secure.audit_trail SET details='x'")
    assert _refused(conn, "DELETE FROM slate_secure.audit_trail")
    assert _refused(conn, "TRUNCATE slate_secure.audit_trail")
    assert _refused(conn, "ALTER TABLE slate_secure.audit_trail DISABLE TRIGGER ALL")
    assert _refused(conn, "DROP TABLE slate_secure.audit_trail")
    conn.close()
    # Not even the superuser changes a line by accident.
    with pytest.raises(psycopg2.Error):
        healthy.sql("DELETE FROM slate_secure.audit_trail")
    assert healthy.sql("SELECT count(*) FROM slate_secure.audit_trail", fetch=True)[0][0] == lines


@pytest.fixture
def audit(monkeypatch, tmp_path):
    """AuditLogger with a share of its own and the database it is handed."""
    from slate.core.infra import audit_logger, database_manager as dbm
    share = tmp_path / "share" / "Logs" / "Audit"
    monkeypatch.setattr(audit_logger.AuditLogger, "_instance", None)
    monkeypatch.setattr(audit_logger.AuditLogger, "_db_retry_at", 0.0)
    monkeypatch.setattr(audit_logger.AuditLogger, "log_directory", staticmethod(lambda: share))
    state = {}

    class Manager:
        active_mode = "postgres"

        def execute_update(self, sql, params=None):
            if state.get("down"):
                from slate.core.infra.db_results import DatabaseUnavailableError
                state["tried"] = state.get("tried", 0) + 1
                raise DatabaseUnavailableError("down")
            return state["db"].execute_update(sql, params)

    monkeypatch.setattr(dbm, "is_connected", lambda: True)
    monkeypatch.setattr(dbm, "database_manager", Manager())
    return audit_logger.AuditLogger, share, state


@slow
def test_auditlogger_writes_the_database_and_never_touches_the_share(healthy, audit):
    from slate.gui.advanced_log_viewer import read_audit_trail
    AuditLogger, share, state = audit
    state["db"] = ConnectionDB(_app(healthy))
    AuditLogger().log_auth("aarav", success=False, reason="wrong password")
    AuditLogger().log_event("BACKUP", "SYSTEM", "Workstation backup written: Données")
    assert not share.exists(), "a share that is down cannot slow a sign-in it is not used for"
    trail = read_audit_trail(share, None, db=state["db"])
    by_type = {row["type"]: row for row in trail}
    assert by_type["AUTH"]["user"] == "aarav" and by_type["AUTH"]["status"] == "FAILURE"
    assert by_type["BACKUP"]["details"].endswith("Données")
    assert all(len(row["time"]) == 19 for row in trail)


@slow
def test_auditlogger_uses_the_share_only_while_the_database_cannot(healthy, audit):
    from slate.gui.advanced_log_viewer import read_audit_trail
    AuditLogger, share, state = audit
    state["db"] = ConnectionDB(_app(healthy))
    state["down"] = True
    AuditLogger().log_auth("aarav", success=True)
    AuditLogger().log_auth("meera", success=True)
    assert state["tried"] == 1, "after one refusal the database is left alone for a while"
    assert len(list(share.glob("audit_*.log"))) == 1
    # The screen shows both: what went to the share and what is in the database.
    state["down"] = False
    AuditLogger._db_retry_at = 0.0
    AuditLogger().log_auth("kabir", success=True)
    users = [row["user"] for row in read_audit_trail(share, None, db=state["db"])
             if row["type"] == "AUTH"]
    assert sorted(users) == ["aarav", "kabir", "meera"]


# ===================================================== hide_password_hashes

def _add_legacy_accounts(lab):
    lab.sql("INSERT INTO ut_users (username, password_hash, roles, active) VALUES "
            "('oldplain', 'plain-pass-1', '[\"Artist\"]', 1), "
            "('oldsha', %s, '[\"Artist\"]', 1)",
            (hashlib.sha256(b"sha-pass-12").hexdigest(),))


def _stored(lab):
    return dict(lab.sql("SELECT username, password_hash FROM ut_users", fetch=True))


@slow
def test_hide_password_hashes_on_signs_in_hides_and_off_puts_them_back(healthy):
    lab = healthy
    _add_legacy_accounts(lab)
    before = _stored(lab)
    session = lab.session(lab.key)
    session.turn_on("hide_password_hashes")
    assert _mode(lab, "hide_password_hashes") == "on"

    conn = _app(lab)
    with conn.cursor() as cur:
        cur.execute("SELECT username, password_hash FROM ut_users")
        shown = dict(cur.fetchall())
        cur.execute("SELECT row_to_json(u)::text FROM ut_users u")
        as_json = " ".join(r[0] for r in cur.fetchall())
    assert shown == {"admin": "hidden:bcrypt", "aarav": "hidden:bcrypt",
                     "oldplain": "hidden:legacy", "oldsha": "hidden:legacy"}
    assert "$2" not in as_json and before["oldsha"] not in as_json and "plain-pass-1" not in as_json
    assert _refused(conn, "SELECT hash FROM slate_secure.passwords")
    assert lab.sql("SELECT count(*) FROM ut_users WHERE username=%s",
                   (secure_schema.PROBE_USER,), fetch=True)[0][0] == 0, "the check account is gone"

    # The real sign-in, as a workstation.
    users = _users(lab, conn)
    assert users.legacy_password_accounts() == ["oldplain", "oldsha"]
    assert users.authenticate("aarav", "artist-pass")
    assert not users.authenticate("aarav", "artist-pasS")
    assert users.authenticate("oldsha", "sha-pass-12")
    assert users.authenticate("oldplain", "plain-pass-1")          # and upgraded on the way
    assert users.legacy_password_accounts() == []
    assert _stored(lab)["oldplain"] == "hidden:bcrypt"
    ok, _ = users.set_password("aarav", "Reset-by-admin1")
    assert ok and _stored(lab)["aarav"] == "hidden:bcrypt"
    assert users.authenticate("aarav", "Reset-by-admin1")
    # Recover Slate keeps working: it writes as the superuser, and the hash is hidden too.
    session.reset_password("aarav", "Brand-new-pass1")
    assert _stored(lab)["aarav"] == "hidden:bcrypt"
    assert users.authenticate("aarav", "Brand-new-pass1")

    # Off: exactly as before - real hashes in ut_users, nothing left in slate_secure.
    session.switches_off(["hide_password_hashes"])
    after = _stored(lab)
    assert not any(v.startswith("hidden:") for v in after.values())
    assert after["admin"] == before["admin"], "the very hash it had"
    assert bcrypt.checkpw(b"sha-pass-12", after["oldsha"].encode()), "upgraded while hidden"
    assert bcrypt.checkpw(b"Brand-new-pass1", after["aarav"].encode())
    assert lab.sql("SELECT count(*) FROM slate_secure.passwords", fetch=True)[0][0] == 0
    assert not secure_schema.is_hiding(lab.connect("postgres", db_credentials.admin_password()))
    assert _users(lab).authenticate("aarav", "Brand-new-pass1")
    assert _mode(lab, "hide_password_hashes") == "off"
    conn.close()


@slow
def test_hide_password_hashes_log_only_changes_nothing(healthy):
    lab = healthy
    before = _stored(lab)
    lab.session(lab.key).turn_on("hide_password_hashes", "log_only")
    assert _stored(lab) == before
    assert _mode(lab, "hide_password_hashes") == "log_only"


@slow
def test_hide_password_hashes_refused_by_its_precheck_changes_nothing(healthy):
    lab = healthy
    lab.sql("UPDATE ut_users SET active=0 WHERE username='admin'")
    before = _stored(lab)
    with pytest.raises(actions.RecoveryRefused) as refused:
        lab.session(lab.key).turn_on("hide_password_hashes")
    assert "administrator" in str(refused.value)
    assert _stored(lab) == before, "nothing moved, and the check account is gone"
    assert snapshots.latest_snapshot(lab.layout) is None
    assert _mode(lab, "hide_password_hashes") == "off"


@slow
def test_hide_password_hashes_that_breaks_sign_in_is_rolled_back(healthy, monkeypatch):
    lab = healthy
    before = _stored(lab)
    real = secure_schema.hide_passwords

    def hide_and_break(conn):
        real(conn)
        with conn.cursor() as cur:       # the workstations can no longer check a password
            cur.execute("REVOKE EXECUTE ON FUNCTION slate_secure.check_password(text, text) "
                        "FROM ut_vfx_app")
    monkeypatch.setattr(secure_schema, "hide_passwords", hide_and_break)
    result = hardening.turn_on(lab.layout, "hide_password_hashes")
    assert not result.applied and result.rolled_back
    assert "a real sign-in" in result.message
    assert _stored(lab) == before, "every hash is back where it was"
    assert not secure_schema.is_hiding(lab.connect("postgres", db_credentials.admin_password()))
    assert _mode(lab, "hide_password_hashes") == "off"
    assert _users(lab).authenticate("aarav", "artist-pass")


# ===================================================== signed_fleet_commands

@slow
def test_the_fleet_key_goes_only_to_an_administrator_with_their_password(healthy):
    from slate.core.security import signing
    lab = healthy
    db = ConnectionDB(_app(lab))
    public = signing.fleet_public_key(db)
    assert len(public) == 64
    assert _refused(db.conn, "SELECT private_key FROM slate_secure.keys")
    assert signing.fleet_private_key("aarav", "artist-pass", db) is None, "not an administrator"
    assert signing.fleet_private_key("admin", "wrong-pass-1", db) is None
    private = signing.fleet_private_key("admin", "first-admin-pass", db)
    assert private and signing.verify(signing.sign({"command": "restart"}, private), public)
    lab.sql("UPDATE ut_users SET active=0 WHERE username='admin'")
    assert signing.fleet_private_key("admin", "first-admin-pass", db) is None, "switched off"
    lab.sql("UPDATE ut_users SET active=1 WHERE username='admin'")
    # Roles store abilities as 'can:<name>': a custom role that may restart workstations counts.
    lab.sql("INSERT INTO ut_roles (role_name, permissions) VALUES ('Desk IT', '[\"can:fleet_control\"]')")
    lab.sql("UPDATE ut_users SET roles='[\"Desk IT\"]' WHERE username='aarav'")
    assert signing.fleet_private_key("aarav", "artist-pass", db) == private
    # Hidden passwords are checked the same way.
    lab.session(lab.key).turn_on("hide_password_hashes")
    assert signing.fleet_private_key("admin", "first-admin-pass", db) == private


@slow
def test_signed_fleet_commands_off_log_only_on(healthy, caplog):
    from slate.core.security import signing
    lab = healthy
    db = ConnectionDB(_app(lab))
    private = signing.fleet_private_key("admin", "first-admin-pass", db)
    good = signing.sign({"command": "restart", "target": "PC-07", "message": "",
                         "timestamp": 1.5, "expires": 61.5}, private)
    unsigned = {k: v for k, v in good.items() if k != "signature"}
    retargeted = dict(good, target="all")

    assert all(signing.command_allowed(c, db) for c in (good, unsigned, retargeted)), "off"
    session = lab.session(lab.key)
    session.turn_on("signed_fleet_commands", "log_only")
    with caplog.at_level("WARNING"):
        assert all(signing.command_allowed(c, db) for c in (good, unsigned, retargeted))
    assert "would be refused" in caplog.text and "not signed" in caplog.text
    session.turn_on("signed_fleet_commands")
    assert signing.command_allowed(good, db)
    assert not signing.command_allowed(unsigned, db)
    assert not signing.command_allowed(retargeted, db)
    session.switches_off(["signed_fleet_commands"])
    switches.reset_cache()
    assert signing.command_allowed(unsigned, db), "off again: as before"


@slow
def test_signed_fleet_commands_refused_or_failing_changes_nothing(healthy, monkeypatch):
    lab = healthy
    lab.sql("UPDATE ut_users SET active=0 WHERE username='admin'")
    with pytest.raises(actions.RecoveryRefused) as refused:
        lab.session(lab.key).turn_on("signed_fleet_commands", "log_only")
    assert "administrator" in str(refused.value)
    assert _mode(lab, "signed_fleet_commands") == "off"
    lab.sql("UPDATE ut_users SET active=1 WHERE username='admin'")

    real = secure_schema.install

    def install_and_leak(conn, app_role):
        real(conn, app_role)
        with conn.cursor() as cur:
            cur.execute("GRANT SELECT ON slate_secure.keys TO ut_vfx_app")
    monkeypatch.setattr(secure_schema, "install", install_and_leak)
    result = hardening.turn_on(lab.layout, "signed_fleet_commands", "log_only")
    assert not result.applied and "private half" in result.message
    assert _mode(lab, "signed_fleet_commands") == "off"
    monkeypatch.setattr(secure_schema, "install", real)
    lab.bootstrap()                                    # the next server start mends it
    assert _refused(_app(lab), "SELECT private_key FROM slate_secure.keys")
