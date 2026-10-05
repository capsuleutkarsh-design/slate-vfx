"""
SQLite runs the same tracking and user code as PostgreSQL.

The SQLite manager used to carry its own copy of the dashboard save, the
project list and the user sync. Most tests run on SQLite, so they checked the
copy - which is how "saving an archived project un-archives it" reached the
studio's PostgreSQL behind a green suite. Both managers now hand this work to
TrackingRepository / UserRepository, and these tests run on both.
"""

import json

import pytest

from slate.core.security.admin_guard import LastAdminRefused


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    return request.getfixturevalue("mock_db" if request.param == "sqlite" else "pg_db")


def test_an_archived_project_stays_archived_and_off_the_list(db):
    assert db.save_tracking_project("ARC", "Archived", '{"code": "ARC"}')
    assert db.save_tracking_project("LIVE", "Live", '{"code": "LIVE"}')
    db.execute_update("UPDATE tracking_projects SET active = 0 WHERE code = %s", ("ARC",))
    assert db.save_tracking_project("ARC", "Archived", '{"code": "ARC", "x": 1}')
    row = db.execute_query("SELECT active FROM tracking_projects WHERE code = %s", ("ARC",), fetch="one")
    assert int(row["active"]) == 0
    assert [p["code"] for p in db.get_all_tracking_projects()] == ["LIVE"]


def test_shots_tasks_and_users_round_trip(db):
    db.save_tracking_project("P1", "P1", '{"code": "P1"}')
    shot = {"shot_name": "SH010", "reel_episode": "R1", "status": "WIP"}
    assert db.save_tracking_shots("P1", [("SH010", "WIP", 2, json.dumps(shot))])
    shots = db.get_tracking_shots("P1")
    assert [(s["shot_name"], s["reel"]) for s in shots] == [("SH010", "R1")]

    task = {"shot_id": shots[0]["id"], "department": "Comp", "status": "WIP", "artist": "ann"}
    assert db.save_tracking_tasks("P1", [task])
    assert db.save_tracking_tasks("P1", [dict(task, status="Done")])     # an upsert, not a second row
    tasks = db.get_tracking_tasks("P1")
    assert [(t["department"], t["status"], t["artist"]) for t in tasks] == [("Comp", "Done", "ann")]

    assert db.sync_users({"ann": {"display_name": "Ann Lee", "roles": ["Artist"], "password_hash": "h"}})
    assert db.get_user_id("ann") is not None
    assert db.get_user_id("ann lee") == db.get_user_id("ann")


def test_the_last_administrator_is_protected_on_both(db):
    assert db.sync_users({"boss": {"display_name": "Boss", "roles": ["Admin"], "password_hash": "h"}})
    with pytest.raises(LastAdminRefused):
        db.sync_users({"boss": {"roles": ["Artist"]}})
