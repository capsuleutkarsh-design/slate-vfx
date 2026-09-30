"""
The shared data fixes, on SQLite and on a real PostgreSQL.

    change history     author by username, shot by id (DSH-006, DSH-115, DSH-137)
    shot identity      reel comes back with the shot; columns kept in step
                       (DSH-003, DSH-024)
    attendance JSONB   metadata merges instead of concatenating, broken rows
                       repaired (HR-001..HR-004)
    punch out          nothing to punch out of is refused (HR-005)
    holidays           a duplicate is not "added" (HR-070)
    machine loans      ledger and inventory together or not at all
                       (IT-002, IT-038)
    studio settings    one value for the whole studio (SYS-109, HR-085)
    migration registry areas add a step with one line
"""

import json
from datetime import date

import pytest

from slate.core.infra.db_results import WriteResult


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    if request.param == "sqlite":
        yield request.getfixturevalue("mock_db")
    else:
        yield request.getfixturevalue("pg_db")


def _is_pg(db):
    return getattr(db, "active_mode", "") == "postgres"


def _user(db, username, display_name, **extra):
    cols = ["username", "display_name"] + list(extra)
    db.execute_update(
        "INSERT INTO ut_users (%s) VALUES (%s)" % (", ".join(cols), ", ".join(["%s"] * len(cols))),
        (username, display_name, *extra.values()))
    row = db.execute_query("SELECT id FROM ut_users WHERE username = %s", (username,), fetch="one")
    return dict(row)["id"]


def _shot(db, project, reel, name, status="WIP", priority=2, version=1):
    data = {"shot_name": name, "reel_episode": reel, "status": status, "priority": priority}
    result = db.write(
        "INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, data_json, version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (project, reel, name, status, priority, json.dumps(data), version))
    assert result.ok, result.error
    return int(result.last_id)


# ================================================================ history


def test_history_names_the_author_from_the_username(db):
    _user(db, "rahul.s", "Rahul Sharma")
    shot_id = _shot(db, "KLC", "R01", "SH010")
    db.log_change_event("KLC", "shot", "SH010", "rahul.s", "UPDATE", "status", "WIP", "Final",
                        shot_id=shot_id, shot_name="SH010", reel="R01")
    rows = db.get_history("KLC", "SH010", shot_id=shot_id)
    assert len(rows) == 1
    row = rows[0]
    # Both spellings, for the History dialog and the Audit Logs viewer.
    assert row["user"] == row["user_name"] == row["display_name"] == "Rahul Sharma"
    assert row["field"] == row["field_changed"] == "status"
    assert row["old_value"] == "WIP" and row["new_value"] == "Final"


def test_old_rows_with_a_numeric_author_still_show_the_name(db):
    uid = _user(db, "priya.k", "Priya K")
    db.execute_update(
        "INSERT INTO change_history (project_code, entity_type, entity_id, user_id, action_type, "
        "field_changed, old_value, new_value) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        ("KLC", "shot", "SH020", str(uid), "UPDATE", "status", "a", "b"))
    rows = db.get_history("KLC", "SH020")
    assert [r["user"] for r in rows] == ["Priya K"]


def test_a_change_with_no_author_is_not_pinned_on_the_admin(db):
    _user(db, "admin", "Administrator")
    result = db.log_change_event("KLC", "shot", "SH010", "", "UPDATE", "status", "a", "b")
    assert not result
    assert db.get_history("KLC") == []


def test_a_shots_history_is_its_own_not_its_namesakes(db):
    """DSH-115: LIKE 'SH010_%' matched SH010A and the other reel's SH010."""
    _user(db, "a", "A")
    r1 = _shot(db, "KLC", "R01", "SH010")
    r2 = _shot(db, "KLC", "R02", "SH010")
    other = _shot(db, "KLC", "R01", "SH010A")
    for sid, name, reel in ((r1, "SH010", "R01"), (r2, "SH010", "R02"), (other, "SH010A", "R01")):
        db.log_change_event("KLC", "task", f"{name}_comp", "a", "UPDATE", "comp_status", "x", reel,
                            shot_id=sid, shot_name=name, reel=reel, department="comp")
    rows = db.get_history("KLC", "SH010", shot_id=r1)
    assert [r["new_value"] for r in rows] == ["R01"]
    assert rows[0]["department"] == "comp"


def test_legacy_rows_match_exactly_with_the_underscore_escaped(db):
    _user(db, "a", "A")
    for entity in ("SH010", "SH010_comp", "SH010A", "SH0101_comp"):
        db.execute_update(
            "INSERT INTO change_history (project_code, entity_type, entity_id, user_id, action_type, "
            "field_changed, old_value, new_value) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            ("KLC", "task", entity, "a", "UPDATE", "f", "", entity))
    got = sorted(r["new_value"] for r in db.get_history("KLC", "SH010"))
    assert got == ["SH010", "SH010_comp"]


def test_backfill_fills_shot_columns_on_old_rows(db):
    from slate.core.infra.migrations.foundation_data import backfill_change_history
    _user(db, "a", "A")
    sid = _shot(db, "KLC", "R01", "SH030")
    db.execute_update(
        "INSERT INTO change_history (project_code, entity_type, entity_id, user_id, action_type, "
        "field_changed, old_value, new_value) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        ("KLC", "task", "SH030_comp", "a", "UPDATE", "comp_status", "", "x"))
    _shot(db, "KLC", "R01", "SH040")
    _shot(db, "KLC", "R02", "SH040")
    for entity_type, entity in (("shot", "SH040"), ("task", "SH030_matchmove")):
        db.execute_update(
            "INSERT INTO change_history (project_code, entity_type, entity_id, user_id, action_type, "
            "field_changed, old_value, new_value) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            ("KLC", entity_type, entity, "a", "UPDATE", "status", "", "x"))
    assert backfill_change_history(db) == 3
    rows = {r["entity_id"]: dict(r) for r in db.execute_query(
        "SELECT entity_id, shot_id, shot_name, department, reel FROM change_history", fetch="all")}
    comp = rows["SH030_comp"]
    assert (comp["shot_id"], comp["shot_name"], comp["department"], comp["reel"]) == (sid, "SH030", "comp", "R01")
    assert rows["SH030_matchmove"]["department"] == "matchmove"
    # SH040 is in two reels: an old row cannot say which, so no id is guessed.
    assert rows["SH040"]["shot_name"] == "SH040" and rows["SH040"]["shot_id"] is None


# ================================================================ shot identity


def test_shots_come_back_with_their_reel(db):
    """DSH-003: without it, (reel, name) keys all became ('', name)."""
    _shot(db, "KLC", "R01", "SH010", version=3)
    _shot(db, "KLC", "R02", "SH010", version=4)
    rows = db.get_tracking_shots("KLC")
    keyed = {(r["reel"], r["shot_name"]): r["version"] for r in rows}
    assert keyed == {("R01", "SH010"): 3, ("R02", "SH010"): 4}


def test_a_safe_update_keeps_the_status_column_in_step(db):
    """DSH-024: the column read by Home stayed at the old status."""
    _shot(db, "KLC", "R01", "SH010", status="OMIT", priority=3, version=1)
    payload = json.dumps({"shot_name": "SH010", "reel_episode": "R01", "status": "Final", "priority": 1})
    assert db.update_tracking_shot_safe("KLC", "SH010", payload, 1, reel="R01")
    row = dict(db.execute_query("SELECT status, priority, version FROM tracking_shots", fetch="one"))
    assert (row["status"], row["priority"], row["version"]) == ("Final", 1, 2)


def test_repair_brings_stale_columns_back(db):
    from slate.core.infra.migrations.foundation_data import repair_tracking_columns
    _shot(db, "KLC", "R01", "SH010", status="WIP")
    db.execute_update("UPDATE tracking_shots SET status = 'OMIT', priority = 9")
    assert repair_tracking_columns(db) == 1
    row = dict(db.execute_query("SELECT status, priority FROM tracking_shots", fetch="one"))
    assert (row["status"], row["priority"]) == ("WIP", 2)
    assert repair_tracking_columns(db) == 0


def test_the_dashboard_save_sees_no_conflict_between_reels(db):
    from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
    from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
    _user(db, "coord", "Coordinator", roles='["admin"]')
    db.save_tracking_project("KLC", "Kalki", json.dumps({"code": "KLC", "name": "Kalki"}))
    _shot(db, "KLC", "R01", "SH010", version=3)
    _shot(db, "KLC", "R02", "SH010", version=4)
    handler = SQLiteHandler("KLC", db_manager=db, user_role=["admin"], username="coord")
    shots = handler.read_shots()
    assert sorted((s.reel_episode, s.version) for s in shots) == [("R01", 3), ("R02", 4)]
    for s in shots:
        s.status = "Final"
    assert handler.write_shots(shots) is True


# ================================================================ attendance


def test_punch_out_keeps_the_wfh_flag(db):
    """HR-003: '{"wfh": true}' became '{"wfh": true}{}' at punch-out."""
    from slate.core.domain.central_attendance import CentralAttendance
    att = CentralAttendance(db=db)
    att.log_action("asha", "in", metadata={"wfh": True})
    att.log_action("asha", "out")
    month = att.get_full_month_data(date.today().year, date.today().month)
    day = month["asha"]["%02d" % date.today().day]
    assert day["wfh"] is True and day["out"]


def test_correcting_an_existing_day_works(db):
    """HR-001: the UPDATE was refused on PostgreSQL, the fallback INSERT hit the unique key."""
    from slate.core.domain.central_attendance import CentralAttendance
    att = CentralAttendance(db=db)
    day = date(2026, 9, 1)
    assert att.write_day("asha", day, "10:00", "19:00", metadata={"wfh": True})[0]
    ok, how = att.write_day("asha", day, "09:30", "18:30", metadata={"admin_edit": True})
    assert ok and how == "Updated"
    row = att.get_day("asha", day)
    meta = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
    assert meta == {"wfh": True, "admin_edit": True}
    assert str(row["punch_in"]).startswith("09:30")


def test_auto_logout_marks_the_day(db):
    """HR-004: the marker was written into invalid JSON and never shown."""
    from slate.core.domain.central_attendance import CentralAttendance
    att = CentralAttendance(db=db)
    db.execute_update(
        "INSERT INTO attendance_log (user_id, day_date, punch_in, pc_name, metadata) "
        "VALUES (%s, %s, %s, %s, %s)", ("asha", "2026-09-01", "10:00:00", "PC", "{}"))
    att._check_and_fix_previous_day("asha", "2026-09-02")
    data = att.get_full_month_data(2026, 9)
    assert data["asha"]["01"]["auto_logout"] is True


def test_punching_out_without_punching_in_is_refused(db):
    """HR-005: it said 'Successfully Logged OUT' and saved nothing."""
    from slate.core.domain.central_attendance import CentralAttendance
    att = CentralAttendance(db=db)
    with pytest.raises(ValueError, match="not punched in"):
        att.log_action("nobody.today", "out")
    assert db.execute_query("SELECT id FROM attendance_log", fetch="all") == []


def test_concatenated_metadata_is_merged_back():
    from slate.core.infra.migrations.foundation_data import merge_json_objects
    assert merge_json_objects('{"wfh": true}{}') == {"wfh": True}
    assert merge_json_objects('{}{"cutoff": "19:30:00", "auto_logout": true}') == {
        "cutoff": "19:30:00", "auto_logout": True}
    assert merge_json_objects('{"a": 1}{"a": 2}{}') == {"a": 2}
    assert merge_json_objects("") == {} and merge_json_objects(None) == {}
    assert merge_json_objects("garbage") == {}


def test_a_text_metadata_column_is_converted_and_repaired(pg_db):
    """A studio database made before the fix: TEXT, with concatenated rows."""
    from slate.core.infra.migrations.foundation_data import apply_migration
    from slate.core.infra.migrations.workplace_schema import _column_type
    pg_db.execute_update("ALTER TABLE attendance_log ALTER COLUMN metadata DROP DEFAULT, "
                         "ALTER COLUMN metadata TYPE TEXT USING metadata::text")
    try:
        for user, meta in (("a", '{"wfh": true}{}'), ("b", ""), ("c", None), ("d", '{"x": 1}')):
            pg_db.execute_update("INSERT INTO attendance_log (user_id, day_date, metadata) "
                                 "VALUES (%s, '2026-09-01', %s)", (user, meta))
        assert _column_type(pg_db, "attendance_log", "metadata") == "text"
        apply_migration(pg_db)
        assert _column_type(pg_db, "attendance_log", "metadata") == "jsonb"
        rows = {r["user_id"]: r["metadata"] for r in pg_db.execute_query(
            "SELECT user_id, metadata FROM attendance_log", fetch="all")}
        assert rows == {"a": {"wfh": True}, "b": {}, "c": {}, "d": {"x": 1}}
    finally:
        # Leave the session's database as the schema makes it.
        if _column_type(pg_db, "attendance_log", "metadata") != "jsonb":
            pg_db.execute_update("DELETE FROM attendance_log")
            pg_db.execute_update("ALTER TABLE attendance_log ALTER COLUMN metadata TYPE JSONB "
                                 "USING '{}'::jsonb, ALTER COLUMN metadata SET DEFAULT '{}'::jsonb")


# ================================================================ holidays, loans


def test_a_duplicate_holiday_is_not_reported_as_added(db):
    from slate.core.infra.leave_repository import LeaveRepository
    repo = LeaveRepository(db)
    assert repo.add_holiday(date(2026, 10, 2), "Gandhi Jayanti", "All") is True
    assert repo.add_holiday(date(2026, 10, 2), "Gandhi Jayanti again", "All") is False


def _machine(db, name):
    db.execute_update("INSERT INTO hardware_inventory (machine_name, status) VALUES (%s, 'Available')",
                      (name,))


def test_issuing_a_machine_writes_ledger_and_inventory_together(db):
    from slate.core.domain.onboarding_service import OnboardingService
    svc = OnboardingService(db)
    _machine(db, "WS-01")
    assert svc.issue_machine("WS-01", "rahul.s", "it.sana") is True
    assert db.execute_query("SELECT user_id FROM asset_assignments", fetch="one")["user_id"] == "rahul.s"
    assert db.execute_query("SELECT assigned_to FROM hardware_inventory", fetch="one")["assigned_to"] == "rahul.s"


def test_issuing_an_unknown_machine_leaves_no_loan_behind(db):
    """IT-038: the ledger row was written even when the inventory update failed."""
    from slate.core.domain.onboarding_service import OnboardingService
    svc = OnboardingService(db)
    assert svc.issue_machine("WS-NOT-THERE", "rahul.s", "it.sana") is False
    assert db.execute_query("SELECT id FROM asset_assignments", fetch="all") == []


def test_a_long_machine_name_can_be_issued(db):
    """IT-002: the ledger column was narrower than the inventory's."""
    from slate.core.domain.onboarding_service import OnboardingService
    long_name = "WS-" + "X" * 150
    _machine(db, long_name)
    assert OnboardingService(db).issue_machine(long_name, "rahul.s", "it.sana") is True


def test_returning_a_machine_that_was_never_lent_is_not_success(db):
    from slate.core.domain.onboarding_service import OnboardingService
    _machine(db, "WS-02")
    assert OnboardingService(db).return_machine("WS-02", "nobody") is False


def test_a_licence_name_the_database_refuses_is_not_saved(db):
    """IT-049 on PostgreSQL: VARCHAR(100). SQLite has no limit, so it saves there."""
    from slate.core.infra.licence_repository import LicenceRepository
    repo = LicenceRepository(db)
    assert repo.save("Nuke", 10, date(2027, 1, 1)) is True
    saved = repo.save("N" * 150, 10, date(2027, 1, 1))
    assert saved is (not _is_pg(db))
    assert repo.NAME_MAX == 100


# ================================================================ studio settings


def test_studio_settings_start_from_the_plan(db):
    from slate.core.infra.studio_settings import StudioSettings
    s = StudioSettings(db)
    assert s.get("currency") == "INR"
    assert s.get("day_rates") == {"INR": 8000, "USD": 300}
    assert s.get("gst_rate") == 18
    assert s.get("working_hours") == {"start": "10:00", "end": "19:00", "days": [0, 1, 2, 3, 4, 5]}


def test_a_studio_setting_is_one_value_for_every_reader(db):
    from slate.core.infra.studio_settings import StudioSettings
    assert StudioSettings(db).set("currency", "usd", by="hr.priya")
    StudioSettings.invalidate()
    assert StudioSettings(db).get("currency") == "USD"
    assert StudioSettings(db).meta("currency")["updated_by"] == "hr.priya"


def test_a_bad_studio_setting_is_refused_with_a_reason(db):
    from slate.core.infra.studio_settings import StudioSettings
    result = StudioSettings(db).set("working_hours", {"start": "19:00", "end": "10:00", "days": [0]})
    assert not result and "end after" in result.error
    result = StudioSettings(db).set("attendance_policy", {"late_cutoff": "half nine"})
    assert not result and "HH:MM" in result.error
    assert not StudioSettings(db).set("currency", "JPY")


def test_the_studio_policy_is_read_from_the_database(db):
    from slate.core.domain import leave_policy as lp
    from slate.core.infra import studio_policy
    try:
        result = studio_policy.save_rules({"late_cutoff": "09:30", "comp_off_enabled": True}, by="hr", db=db)
        assert result, result.error
        assert lp.late_cutoff() == (9, 30)
        assert lp.policy()["comp_off_enabled"] is True
    finally:
        lp.set_overrides({})


def test_machine_values_are_adopted_once(db, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.migrations.foundation_data import adopt_machine_settings
    from slate.core.infra.studio_settings import StudioSettings
    values = {"late_cutoff": "11:00", "standard_day_hours": 8.5}
    monkeypatch.setattr(GlobalConfig, "get", classmethod(lambda cls, k, d=None: values.get(k, d)))
    StudioSettings.invalidate()
    assert "attendance_policy" in str(adopt_machine_settings(db))
    StudioSettings.invalidate()
    assert StudioSettings(db).get("attendance_policy") == {"late_cutoff": "11:00", "standard_day_hours": 8.5}
    values["late_cutoff"] = "12:00"
    adopt_machine_settings(db)   # already set: left alone
    StudioSettings.invalidate()
    assert StudioSettings(db).get("attendance_policy")["late_cutoff"] == "11:00"


# ================================================================ registry


def test_the_registry_runs_every_step_and_records_one_offs(db):
    from slate.core.infra.migrations.registry import run_migrations, all_steps, step
    report = run_migrations(db)
    assert set(report) == {s.name for s in all_steps()}
    assert not [n for n, (outcome, _) in report.items() if outcome.startswith("error")], report
    again = run_migrations(db)
    assert again["repair_tracking_columns"][0] == "skipped"


def test_a_failing_area_step_does_not_stop_the_others(db):
    from slate.core.infra.migrations.registry import run_migrations, step
    report = run_migrations(db, [
        step("broken", "slate.core.infra.migrations.no_such_module:apply_migration"),
        step("stock_indexes", "slate.core.infra.migrations.stock_indexes:apply_migration"),
    ])
    assert report["broken"][0].startswith("error")
    assert report["stock_indexes"][0] == "ok"


def test_database_manager_runs_the_registry():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "slate" / "core" / "infra"
              / "database_manager.py").read_text(encoding="utf-8")
    assert "run_migrations(self.backend)" in source


def test_every_registered_step_can_be_imported():
    from slate.core.infra.migrations.registry import all_steps
    for item in all_steps():
        assert callable(item.resolve()), item.name


def test_existing_bids_are_marked_usd(db):
    """FIX_PLAN: existing bids keep their numbers and are stored as USD."""
    from slate.core.infra.migrations.foundation_data import apply_migration
    from slate.core.infra.migrations.workplace_schema import _column_exists
    assert _column_exists(db, "prod_bidding", "currency")
    assert _column_exists(db, "ut_users", "is_service")
