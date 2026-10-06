"""
The safety net, part one: switches, the last-administrator guard and the
pre-check every hardening step must pass (slate/core/security).
"""

import json

import pytest

from slate.core.domain.user_manager import UserManager
from slate.core.security import admin_guard, switches
from slate.core.security.precheck import (HBA_SIGNATURE, can_still_get_in, check_hba,
                                          hba_decision)


# ================================================================== switches

@pytest.fixture
def clean_switches(tmp_path, monkeypatch):
    monkeypatch.delenv(switches.OVERRIDE_ENV, raising=False)
    switches.set_override_file(None)
    switches.reset_cache()
    yield tmp_path / "security_switches.json"
    switches.set_override_file(None)
    switches.reset_cache()


def test_every_switch_starts_off(mock_db, clean_switches):
    modes = switches.all_modes(db=mock_db)
    assert modes and set(modes.values()) == {switches.OFF}
    assert switches.mode("no_such_switch", db=mock_db) == switches.OFF


def test_a_switch_can_be_turned_on_and_read_back(mock_db, clean_switches):
    switches.set_mode("refuse_inactive_signin", switches.LOG_ONLY, by="admin", db=mock_db)
    assert switches.mode("refuse_inactive_signin", db=mock_db) == switches.LOG_ONLY
    assert switches.is_logging("refuse_inactive_signin", db=mock_db)
    assert not switches.is_on("refuse_inactive_signin", db=mock_db)
    switches.set_mode("refuse_inactive_signin", switches.ON, by="admin", db=mock_db)
    assert switches.is_on("refuse_inactive_signin", db=mock_db)


def test_an_unknown_switch_name_is_refused_rather_than_created(mock_db, clean_switches):
    with pytest.raises(KeyError):
        switches.set_mode("refuse_inactve_signin", switches.ON, db=mock_db)


def test_an_unreadable_database_reads_as_off():
    class Broken:
        def execute_query(self, *a, **k):
            raise RuntimeError("database is down")

        def execute_update(self, *a, **k):
            raise RuntimeError("database is down")

    assert switches.mode("strict_pg_hba", db=Broken(), override_path="nowhere.json") == switches.OFF


def test_the_local_file_forces_a_switch_off_without_the_database(mock_db, clean_switches):
    switches.set_mode("strict_pg_hba", switches.ON, db=mock_db)
    switches.force_off_locally(["strict_pg_hba"], clean_switches, by="recovery")
    assert switches.mode("strict_pg_hba", db=mock_db, override_path=clean_switches) == switches.OFF
    # ...and the server copies it into the database for the workstations.
    assert switches.apply_local_overrides(mock_db, clean_switches) == 1
    assert switches.mode("strict_pg_hba", db=mock_db) == switches.OFF


def test_all_off_and_a_corrupt_file_both_mean_every_switch_off(mock_db, clean_switches):
    switches.set_mode("readonly_sql_console", switches.ON, db=mock_db)
    switches.force_off_locally(None, clean_switches)
    assert switches.mode("readonly_sql_console", db=mock_db, override_path=clean_switches) == switches.OFF
    clean_switches.write_text("{not json", encoding="utf-8")
    assert switches.mode("readonly_sql_console", db=mock_db, override_path=clean_switches) == switches.OFF


# ============================================================ the admin guard

@pytest.fixture(params=["sqlite", "postgres"])
def um(request):
    db = request.getfixturevalue("mock_db" if request.param == "sqlite" else "pg_db")
    from slate.core.domain import access
    access.reset_cache()
    manager = UserManager(db=db)
    # The seed brings admin (Developer). Leave exactly one administrator.
    for name in list(manager.get_all_users()):
        if name != "admin":
            manager._get_db().execute_update("DELETE FROM ut_users WHERE username=%s", (name,))
    manager.add_user("hr.kavya", "secret1", ["HR"], "Kavya", "HR")
    manager.add_user("aarav", "pw1234", ["Artist"], "Aarav", "Comp")
    yield manager
    access.reset_cache()


def _admins(um):
    users, perms = admin_guard.read_state(um._get_db())
    return admin_guard.administrators(users, perms)


def test_the_last_admin_cannot_be_deleted_even_if_unprotected(um, monkeypatch):
    monkeypatch.setattr(UserManager, "PROTECTED_ACCOUNTS", frozenset())
    assert um.delete_user("admin") is False
    assert "no active administrator" in um.last_error
    assert _admins(um) == ["admin"]


def test_the_last_admin_cannot_be_deactivated(um, monkeypatch):
    monkeypatch.setattr(UserManager, "PROTECTED_ACCOUNTS", frozenset())
    ok, message = um.deactivate_user("admin", by="hr.kavya")
    assert not ok and "no active administrator" in message
    assert _admins(um) == ["admin"]


def test_the_last_admin_cannot_be_demoted(um):
    with pytest.raises(PermissionError) as refused:
        um.update_user("admin", roles=["Artist"])
    assert isinstance(refused.value, admin_guard.LastAdminRefused)
    assert _admins(um) == ["admin"]


def test_a_last_day_in_the_past_counts_as_deactivating(um):
    with pytest.raises(PermissionError):
        um.update_user("admin", last_day="2001-01-01")
    with pytest.raises(PermissionError):
        um.add_user("admin", "KEEP_OLD", ["Developer"], last_day="2001-01-01")


def test_the_full_access_role_cannot_lose_it_or_be_renamed_away(um):
    um.update_role_permissions("Boss", ["ALL"])
    um.add_user("boss.one", "pw12345", ["Boss"], "Boss", "Ops")
    assert um.update_user("admin", roles=["Artist"])          # boss.one is still there
    with pytest.raises(PermissionError):
        um.update_role_permissions("Boss", ["Dashboard"])
    assert "ALL" in um.role_permissions("Boss")
    ok, _ = um.rename_role("Boss", "Chief")                    # the rights go with the name
    assert ok and _admins(um) == ["boss.one"]


def test_renaming_the_admin_role_away_is_refused_when_it_is_the_last(um):
    """access.json grants by role NAME: renaming "Admin" takes the rights away."""
    um.update_role_permissions("Admin", ["Dashboard"])
    um.add_user("ops.lead", "pw12345", ["Admin"], "Ops", "Ops")
    assert um.update_user("admin", roles=["Artist"])
    ok, message = um.rename_role("Admin", "Office")
    assert not ok and "administrator" in message
    assert _admins(um) == ["ops.lead"]


def test_a_bulk_sync_cannot_demote_the_last_admin(um):
    with pytest.raises(PermissionError):
        um.save_users({"admin": {"roles": ["Artist"]}})
    assert _admins(um) == ["admin"]


def test_with_a_second_admin_the_first_can_be_demoted(um):
    um.add_user("lead.dev", "pw12345", ["Developer"], "Second", "Dev")
    assert um.update_user("admin", roles=["Artist"])
    assert _admins(um) == ["lead.dev"]


def test_a_deactivated_second_admin_does_not_count(um):
    um.add_user("old.dev", "pw12345", ["Developer"], "Gone", "Dev")
    ok, _ = um.deactivate_user("old.dev", by="admin")
    assert ok
    with pytest.raises(PermissionError):
        um.update_user("admin", roles=["Artist"])


def test_the_last_user_manager_is_protected_too():
    perms = {"hr": ["Attendance"], "artist": ["Dashboard"]}
    before = {"hr.kavya": {"roles": '["HR"]', "password_hash": "x"},
              "aarav": {"roles": '["Artist"]', "password_hash": "x"}}
    assert admin_guard.counts(before, perms) == (0, 1)
    after = {"aarav": before["aarav"]}
    assert "manage users" in admin_guard.refusal(before, perms, after, perms)


def test_an_import_cannot_demote_the_last_admin(um, tmp_path):
    """
    An import never changes roles of people already in Slate (its own rule),
    and if a row ever tried, the guard in update_user refuses it: the row
    fails with the reason and the rest of the file still goes in.
    """
    from slate.core.domain import user_import
    path = tmp_path / "people.csv"
    path.write_text("Username,Name,Role\nadmin,System Admin,Artist\nnew.person,New,Artist\n",
                    encoding="utf-8")
    plan = user_import.plan_import(path, set(um.get_all_users()), user_manager=um,
                                   allowed_roles=um.get_available_roles(), update_existing=True)
    forced = user_import.Row(line=99, username="admin", display_name="System Admin",
                             status="update", changes={"roles": (["Developer"], ["Artist"])})
    plan.rows.append(forced)
    user_import.apply_import(um, plan, "Artist", "first-pass1")
    assert _admins(um) == ["admin"]
    assert forced.status == "failed" and "administrator" in forced.reason
    assert "new.person" in um.get_all_users()


def test_a_database_that_already_has_no_admin_can_still_be_worked_on(um):
    """The guard refuses making things worse, never making them better."""
    um._get_db().execute_update("UPDATE ut_users SET active=0 WHERE username='admin'")
    assert _admins(um) == []
    assert um.update_user("aarav", roles=["Artist", "Lead"])
    um.add_user("new.admin", "pw12345", ["Developer"], "New", "Dev")
    assert _admins(um) == ["new.admin"]


# =============================================================== the precheck

HARDENED = "\n".join([
    HBA_SIGNATURE,
    "local   all   all                    scram-sha-256",
    "host    all   all   127.0.0.1/32     scram-sha-256",
    "host    all   all   10.0.0.0/8       scram-sha-256",
    "",
])


def test_hba_first_match_wins():
    text = HBA_SIGNATURE + "\nhost all postgres 0.0.0.0/0 reject\nhost all all 0.0.0.0/0 scram-sha-256\n"
    assert hba_decision(text, database="ut_vfx", user="postgres", address="10.1.2.3") == "reject"
    assert hba_decision(text, database="ut_vfx", user="ut_vfx_app", address="10.1.2.3") == "scram-sha-256"
    assert hba_decision("host all all 10.0.0.0/8 md5\n", database="x", user="y",
                        address="192.168.1.2") is None


def test_the_hardened_rules_slate_writes_pass():
    assert check_hba(HARDENED).ok


def test_rules_without_the_signature_line_are_refused():
    result = check_hba(HARDENED.replace(HBA_SIGNATURE + "\n", ""))
    assert not result.ok and "first line" in result.message


def test_rules_that_shut_out_the_server_or_the_workstations_are_refused():
    no_server = HBA_SIGNATURE + "\nhost all postgres 127.0.0.1/32 reject\n" + HARDENED
    assert not check_hba(no_server).ok
    no_lan = HBA_SIGNATURE + "\nhost all all 127.0.0.1/32 scram-sha-256\n"
    result = check_hba(no_lan)
    assert not result.ok and "Workstations" in result.message


def test_can_still_get_in_needs_an_active_admin(um):
    assert can_still_get_in(db=um._get_db()).ok
    um._get_db().execute_update("UPDATE ut_users SET password_hash='' WHERE username='admin'")
    result = can_still_get_in(db=um._get_db())
    assert not result.ok and "administrator" in result.message


def test_can_still_get_in_tries_the_real_connection():
    def refuse(**kwargs):
        raise RuntimeError('password authentication failed for user "ut_vfx_app"')

    result = can_still_get_in(client={"host": "h", "port": 1, "dbname": "d", "user": "ut_vfx_app",
                                      "password": "x"}, connect=refuse)
    assert not result.ok and "password authentication failed" in result.message


def test_nothing_to_check_is_not_a_yes():
    assert not can_still_get_in().ok


def test_reading_switches_on_a_fresh_database_logs_no_error(tmp_path, caplog):
    """The first start read security_switches before anything created it: an ERROR every time."""
    import logging
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.security import switches
    db = SQLiteManager(str(tmp_path / "fresh.db"))
    with caplog.at_level(logging.WARNING):
        assert switches._read_db(db) == {}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
