"""
Sign-in hardening for 2.2.0 (track: signin). One check per rule; every switch
is shown off (old behaviour), log_only (logs, refuses nothing) and on.
"""
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate.core.domain.user_manager import UserManager
from slate.core.security import switches


@pytest.fixture
def um(mock_db, monkeypatch):
    yield from _manager(mock_db, monkeypatch)


@pytest.fixture(params=["sqlite", "postgres"])
def both(request, monkeypatch):
    """(UserManager, db) on each backend: the import's transaction is the backend's own."""
    db = request.getfixturevalue("mock_db" if request.param == "sqlite" else "pg_db")
    for manager in _manager(db, monkeypatch):
        yield manager, db


def _manager(mock_db, monkeypatch):
    monkeypatch.delenv(switches.OVERRIDE_ENV, raising=False)
    switches.reset_cache()
    from slate.core.domain import access
    access.reset_cache()
    manager = UserManager(db=mock_db)
    manager.add_user("aarav", "Password1", ["Artist"], "Aarav", "Comp")
    yield manager
    switches.reset_cache()
    access.reset_cache()


def _switch(db, name, mode):
    logging.getLogger(switches.__name__).disabled = True   # set_mode logs the name
    try:
        switches.set_mode(name, mode, by="test", db=db)
    finally:
        logging.getLogger(switches.__name__).disabled = False


def _row(um, name):
    return um._row(name)


# ------------------------------------------------------------------ the API

def test_api_login_checks_the_password(um, mock_db, monkeypatch):
    from fastapi import HTTPException
    from slate.api.routers import users as router
    monkeypatch.setattr(router, "create_access_token", lambda data: "token-for-" + data["sub"])
    form = lambda pw: SimpleNamespace(username="aarav", password=pw)
    with pytest.raises(HTTPException) as refused:
        router.login_for_access_token(form("wrong-password"), db=mock_db)
    assert refused.value.status_code == 401
    assert router.login_for_access_token(form("Password1"), db=mock_db)["access_token"] == "token-for-aarav"


def test_api_has_no_unauthenticated_writes_and_no_credentialed_cors():
    from slate.api import main
    paths = [r.path for r in main.app.routes]
    assert "/api/users/sync" not in paths
    assert not any(p.endswith("/batch") for p in paths)
    cors = main.app.user_middleware[0]
    options = getattr(cors, "kwargs", None) or getattr(cors, "options", {})
    assert options["allow_credentials"] is False
    html = main.get_admin_dashboard().body.decode("utf-8")
    assert "innerHTML = `<span" not in html and "textContent = `▶ ${msg}`" in html
    assert "api_auth_required" not in switches.CATALOGUE


# ------------------------------------------------------- users.json (NEW-2)

def _json_files(um, tmp_path, users):
    um.users_file = tmp_path / "users.json"
    um.roles_file = tmp_path / "roles.json"
    um.users_file.write_text(json.dumps({"users": users}), encoding="utf-8")
    um.roles_file.write_text(json.dumps({"roles": {"Artist": ["Dashboard"]}}), encoding="utf-8")


def test_users_json_never_overwrites_existing_accounts(both, tmp_path):
    um, _ = both
    _json_files(um, tmp_path, {"aarav": {"password_hash": "old", "roles": ["Developer"]}})
    before = _row(um, "aarav")
    um._run_migration()
    assert _row(um, "aarav") == before
    assert um.users_file.exists()                       # left alone, not renamed


def test_users_json_goes_into_an_empty_database_once(both, tmp_path):
    um, mock_db = both
    _json_files(um, tmp_path, {"bob": {"password_hash": "x", "roles": ["Artist"]}})
    mock_db.execute_update("DELETE FROM ut_users")
    um._run_migration()
    assert _row(um, "bob") and not um.users_file.exists()
    assert (tmp_path / "users.json.migrated").exists()


def test_a_failed_import_is_rolled_back_and_the_files_stay(both, tmp_path):
    um, mock_db = both
    _json_files(um, tmp_path, {"bob": {"password_hash": "x"}, "broken": "not an account"})
    mock_db.execute_update("DELETE FROM ut_users")
    um._run_migration()
    assert _row(um, "bob") is None
    assert um.users_file.exists() and um.roles_file.exists()


# ------------------------------------------- default accounts (SYS-084)

def test_only_admin_is_seeded(um):
    assert _row(um, "admin")
    assert _row(um, "artist") is None and _row(um, "tester") is None


@pytest.mark.parametrize("mode, created", [("off", True), ("log_only", True), ("on", False)])
def test_no_default_accounts(um, mock_db, mode, created, caplog):
    _switch(mock_db, "no_default_accounts", mode)
    mock_db.execute_update("DELETE FROM ut_users WHERE username='admin'")
    with caplog.at_level(logging.WARNING):
        um._ensure_essential_accounts()
    assert bool(_row(um, "admin")) is created
    assert ("no_default_accounts" in caplog.text) is (mode != "off")


# ------------------------------------------ master password (SYS-001)

def test_the_shared_admin_password_unlocks_nothing(monkeypatch):
    from slate.gui import admin_panel
    from slate.core.infra.global_config import GlobalConfig
    defaults = json.loads((Path(admin_panel.__file__).parents[1]
                           / "default_config.json").read_text(encoding="utf-8"))
    assert "admin_password" not in defaults
    assert "no_master_password" not in switches.CATALOGUE
    monkeypatch.setattr(GlobalConfig, "get", classmethod(
        lambda cls, k, d=None: "admin123" if k == "admin_password" else d))
    monkeypatch.setattr(admin_panel.QInputDialog, "getText", lambda *a, **k: ("admin123", True))
    monkeypatch.setattr(admin_panel.QMessageBox, "warning", lambda *a, **k: None)
    panel = SimpleNamespace(current_username="boss", log_action=lambda m: None,
                            user_manager=SimpleNamespace(authenticate=lambda u, p: None))
    assert admin_panel.AdminPanel.verify_admin_action(panel) is False


# ------------------------------------------- inactive people (SHL-013)

@pytest.mark.parametrize("mode, signs_in", [("off", True), ("log_only", True), ("on", False)])
def test_refuse_inactive_signin(um, mock_db, mode, signs_in, caplog):
    _switch(mock_db, "refuse_inactive_signin", mode)
    mock_db.execute_update("UPDATE ut_users SET active=0 WHERE username='aarav'")
    with caplog.at_level(logging.WARNING):
        user = um.authenticate("aarav", "Password1")
    assert bool(user) is signs_in
    assert ("refuse_inactive_signin" in caplog.text) is (mode != "off")
    assert ("switched off" in um.last_error) is (not signs_in)


def test_past_last_day_is_refused_but_the_admin_never_is(um, mock_db):
    _switch(mock_db, "refuse_inactive_signin", "on")
    um.update_user("aarav", last_day="2020-01-01")
    assert not um.authenticate("aarav", "Password1")
    mock_db.execute_update("UPDATE ut_users SET active=0, last_day='2020-01-01' WHERE username='admin'")
    assert um.authenticate("admin", "admin123")


def test_an_inactive_administrator_signs_in_when_none_is_active(um, mock_db):
    _switch(mock_db, "refuse_inactive_signin", "on")
    um.add_user("boss", "Password1", ["Developer"], "Boss", "")
    mock_db.execute_update("UPDATE ut_users SET active=0 WHERE username='boss'")
    assert not um.authenticate("boss", "Password1")               # admin is still active
    mock_db.execute_update("DELETE FROM ut_users WHERE username='admin'")
    assert um.authenticate("boss", "Password1")


# ------------------------------------------ legacy passwords (HR-144)

@pytest.mark.parametrize("stored", ["Legacy-pass1", hashlib.sha256(b"Legacy-pass1").hexdigest()])
def test_a_legacy_password_is_upgraded_at_sign_in(um, mock_db, stored):
    mock_db.execute_update("UPDATE ut_users SET password_hash=%s WHERE username='aarav'", (stored,))
    assert um.legacy_password_accounts() == ["aarav"]
    assert um.authenticate("aarav", "Legacy-pass1")
    assert _row(um, "aarav")["password_hash"].startswith("$2")
    assert um.legacy_password_accounts() == []
    assert um.authenticate("aarav", "Legacy-pass1")


@pytest.mark.parametrize("mode, signs_in", [("off", True), ("log_only", True), ("on", False)])
def test_no_plaintext_passwords(um, mock_db, mode, signs_in, caplog):
    _switch(mock_db, "no_plaintext_passwords", mode)
    mock_db.execute_update("UPDATE ut_users SET password_hash='Legacy-pass1' WHERE username='aarav'")
    with caplog.at_level(logging.WARNING):
        user = um.authenticate("aarav", "Legacy-pass1")
    assert bool(user) is signs_in
    assert ("no_plaintext_passwords" in caplog.text) is (mode != "off")
    if not signs_in:
        assert _row(um, "aarav")["password_hash"] == "Legacy-pass1" and "reset" in um.last_error


# ------------------------------------------------- audit (SYS2-002)

def test_every_sign_in_is_audited_without_the_password(um, monkeypatch):
    seen = []
    monkeypatch.setattr(um.audit, "log_auth", lambda *a: seen.append(a))
    um.authenticate("aarav", "Password1")
    um.authenticate("aarav", "Wrong-pass9")
    um.authenticate("nobody", "Wrong-pass9")
    assert [(a[0], a[1]) for a in seen] == [("aarav", True), ("aarav", False), ("nobody", False)]
    assert "Wrong-pass9" not in repr(seen) and "Password1" not in repr(seen)


# ---------------------------------------- reset password (HR-127, HR-132)

def test_reset_password_follows_the_rule_and_forces_a_change(um, mock_db):
    assert not um.set_password("aarav", "short1")[0]
    ok, message = um.set_password("aarav", "Longer-pass1")
    assert ok, message
    assert _row(um, "aarav")["must_change_password"] == 1
    assert not um.create_user("newbie", "short1", ["Artist"])[0]
    # A six-character password set before the rule still signs in.
    mock_db.execute_update("UPDATE ut_users SET password_hash=%s WHERE username='aarav'",
                           (um._hash_password("abc123"),))
    assert um.authenticate("aarav", "abc123")


# ---------------------------------------------- sign-in window (SHL-004, NEW-7)

def test_the_window_never_names_the_default_password(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from slate.gui import login_dialog as module
    store = QSettings(str(tmp_path / "login.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(module, "login_settings", lambda: store)
    users = SimpleNamespace(authenticate=lambda u, p: None, is_fresh_seed=lambda: True,
                            MIN_PASSWORD_LENGTH=8, last_error="")
    dialog = module.LoginDialog(users, app_context=SimpleNamespace(user_manager=lambda: users))
    qtbot.addWidget(dialog)
    dialog.user_input.setText("someone")
    dialog._on_auth_result(None, "")
    assert "admin123" not in dialog.status_lbl.text()
    assert "administrator" in dialog.status_lbl.text()
    users.last_error = "This account is switched off or its last day has passed."
    dialog._on_auth_result(None, "")
    assert "switched off" in dialog.status_lbl.text()


def test_a_failed_sign_in_waits_on_the_worker_thread(qtbot, monkeypatch):
    from slate.gui import login_dialog as module
    waits = []
    monkeypatch.setattr(module.time, "sleep", waits.append)
    for user, expected in ((None, [module.FAILED_SIGNIN_DELAY]), ({"user_id": "a"}, [])):
        waits.clear()
        worker = module.LoginAuthWorker(SimpleNamespace(authenticate=lambda u, p: user), "a", "b")
        worker.run()                       # the QThread body; the window calls start()
        assert waits == expected


# ------------------------------------------ local copy (SEC-021)

@pytest.mark.parametrize("mode, allowed", [("off", True), ("log_only", True), ("on", False)])
def test_no_sqlite_fallback_uses_the_mode_last_read_from_the_server(monkeypatch, mode, allowed, caplog):
    import slate.core.infra.database_manager as dm
    from slate.core.infra.global_config import GlobalConfig
    stored = {}
    monkeypatch.setattr(GlobalConfig, "get", classmethod(lambda cls, k, d=None: stored.get(k, d)))
    monkeypatch.setattr(GlobalConfig, "set", classmethod(lambda cls, k, v: stored.__setitem__(k, v)))
    monkeypatch.setattr(switches, "mode", lambda name, db=None: mode)
    dm._remember_fallback_switch(backend=object())
    assert stored.get(dm._FALLBACK_SETTING, "off") == mode
    with caplog.at_level(logging.WARNING):
        assert dm._fallback_allowed_by_switch() is allowed
    assert ("no_sqlite_fallback" in caplog.text) is (mode != "off")
