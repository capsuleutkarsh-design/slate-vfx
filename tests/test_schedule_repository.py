"""
Scheduling's data, on SQLite and on a real PostgreSQL.

    PRD-019  dates become real dates; legacy text is kept, never lost
    PRD-020  links to deleted milestones are cleared; deleting a parent
             detaches its dependents
    PRD-002/038/039  the repository refuses what the rules refuse
    PRD-003/010      add returns the id; edit and delete exist
    PRD-006  a shift is one transaction - a failure leaves every date alone
    PRD-025  milestones of archived projects are hidden by default
    PRD-036  without schedule_write nothing is written
    PRD-037/048/070  who changed what is recorded; completion date; undo
"""

from datetime import date

import pytest

from slate.core.domain import scheduling as DS
from slate.core.infra.schedule_repository import ScheduleRepository


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    if request.param == "sqlite":
        yield request.getfixturevalue("mock_db")
    else:
        yield request.getfixturevalue("pg_db")


def _is_pg(db):
    return getattr(db, "active_mode", "") == "postgres"


def _project(db, code, name="", active=1):
    db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, %s)",
                      (code, name or code, active))


def _ms(project, name, start, end, dep=None, status="Scheduled", owner=""):
    return DS.Milestone(project_code=project, name=name, start=date.fromisoformat(start),
                        end=date.fromisoformat(end), status=status, depends_on_id=dep, owner=owner)


@pytest.fixture
def repo(db):
    _project(db, "AVTR3", "Avatar 3")
    _project(db, "RRR_REDUX", "RRR Redux")
    _project(db, "OLDPROJ", "Old project", active=0)
    return ScheduleRepository(db)


def test_PRD_003_add_returns_the_id_and_the_row_reads_back_as_dates(repo):
    new_id = repo.add(_ms("AVTR3", "Plate turnover", "2026-09-01", "2026-09-05"), by="priya")
    assert isinstance(new_id, int)
    m = repo.get(new_id)
    assert (m.start, m.end) == (date(2026, 9, 1), date(2026, 9, 5))
    assert m.created_by == "priya" and m.project_name == "Avatar 3"


def test_PRD_002_PRD_038_PRD_039_the_repository_refuses_what_the_rules_refuse(repo):
    first = repo.add(_ms("AVTR3", "Comp final", "2026-09-01", "2026-09-30"), by="priya")
    with pytest.raises(DS.ScheduleError, match="another project"):
        repo.add(_ms("RRR_REDUX", "Grade", "2026-10-05", "2026-10-10", dep=first), by="priya")
    with pytest.raises(DS.ScheduleError, match="already exists"):
        repo.add(_ms("AVTR3", "comp FINAL", "2026-09-01", "2026-09-30"), by="priya")
    with pytest.raises(DS.ScheduleError, match="120"):
        repo.add(_ms("AVTR3", "X" * 500, "2026-09-01", "2026-09-30"), by="priya")


def test_PRD_010_edit_changes_fields_and_writes_the_history(db, repo):
    mid = repo.add(_ms("AVTR3", "Roto", "2026-09-01", "2026-09-10"), by="priya")
    m = repo.get(mid)
    m.name, m.owner, m.department = "Roto & prep", "rahul", "roto"
    changed = repo.edit(m, by="priya")
    assert set(changed) == {"name", "owner", "department"}
    again = repo.get(mid)
    assert (again.name, again.owner, again.department) == ("Roto & prep", "rahul", "roto")
    assert again.updated_by == "priya"
    history = db.execute_query("SELECT * FROM change_history WHERE entity_type = 'milestone'",
                               fetch="all")
    fields = {dict(r).get("field_changed") for r in history}
    assert {"milestone", "owner", "department"} <= fields


def test_PRD_020_deleting_a_parent_detaches_its_children(repo):
    parent = repo.add(_ms("AVTR3", "Roto", "2026-09-01", "2026-09-10"), by="priya")
    child = repo.add(_ms("AVTR3", "Comp", "2026-09-11", "2026-09-20", dep=parent), by="priya")
    assert [m.id for m in repo.dependents([parent])] == [child]
    assert repo.delete([parent], by="priya") == 1
    assert repo.get(parent) is None
    assert repo.get(child).depends_on_id is None


def test_PRD_048_PRD_070_completion_date_and_undo(repo):
    a = repo.add(_ms("AVTR3", "A", "2026-09-01", "2026-09-10"), by="priya")
    b = repo.add(_ms("AVTR3", "B", "2026-09-11", "2026-09-20", status="In Progress"), by="priya")
    previous = repo.set_status([a, b], "Completed", by="priya")
    assert repo.get(a).completed_on == date.today()
    assert repo.get(b).status == "Completed"
    repo.restore_statuses(previous, by="priya")
    assert repo.get(a).status == "Scheduled" and repo.get(a).completed_on is None
    assert repo.get(b).status == "In Progress"


def test_PRD_006_a_failing_shift_changes_nothing(repo):
    a = repo.add(_ms("AVTR3", "A", "2026-09-01", "2026-09-10"), by="priya")
    b = repo.add(_ms("AVTR3", "B", "2026-09-11", "2026-09-20", dep=a), by="priya")
    with pytest.raises(Exception):
        repo.apply_dates([(a, date(2026, 9, 3), date(2026, 9, 12)),
                          (b, date(2026, 9, 13), date(2026, 9, 22)),
                          (999999, date(2026, 9, 1), date(2026, 9, 2))], by="priya")
    assert repo.get(a).start == date(2026, 9, 1)
    assert repo.get(b).start == date(2026, 9, 11)
    assert repo.apply_dates([(a, date(2026, 9, 3), date(2026, 9, 12))], by="priya") == 1
    assert repo.get(a).end == date(2026, 9, 12)


def test_PRD_025_archived_projects_are_hidden_by_default(db, repo):
    db.execute_update("INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, "
                      "status) VALUES ('OLDPROJ', 'Archived project milestone', '2025-01-01', "
                      "'2025-01-10', 'In Progress')")
    repo.add(_ms("AVTR3", "Live", "2026-09-01", "2026-09-10"), by="priya")
    assert [m.name for m in repo.list()] == ["Live"]
    every = repo.list(include_archived=True)
    assert {m.name for m in every} == {"Live", "Archived project milestone"}
    assert next(m for m in every if m.project_code == "OLDPROJ").archived


def test_PRD_023_list_is_by_project_then_date(repo):
    repo.add(_ms("RRR_REDUX", "R1", "2026-08-01", "2026-08-02"), by="p")
    repo.add(_ms("AVTR3", "Late", "2026-10-01", "2026-10-02"), by="p")
    repo.add(_ms("AVTR3", "Early", "2026-09-01", "2026-09-02"), by="p")
    assert [m.name for m in repo.list()] == ["Early", "Late", "R1"]


def test_PRD_036_without_schedule_write_nothing_is_written(db, repo):
    reader = ScheduleRepository(db, roles=["Artist"])
    with pytest.raises(PermissionError):
        reader.add(_ms("AVTR3", "Sneaky", "2026-09-01", "2026-09-02"), by="artist")
    writer = ScheduleRepository(db, roles=["Production Coordinator"])
    assert writer.add(_ms("AVTR3", "Allowed", "2026-09-01", "2026-09-02"), by="coord")


def test_PRD_012_people_data_reads_tasks_and_only_approved_leave(db, repo):
    db.execute_update("INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                      "data_json, last_updated, version) VALUES ('AVTR3', 'R1', 'SH010', 'WIP', 2, "
                      "'{}', '2026-09-01', 1)")
    shot = db.execute_query("SELECT id FROM tracking_shots WHERE shot_name = 'SH010'", fetch="one")
    db.execute_update("INSERT INTO tracking_tasks (shot_id, project_code, department, status, "
                      "artist_name, bid_days, target_date) VALUES (%s, 'AVTR3', 'comp', 'WIP', "
                      "'priya', 3, '2026-10-09')", (dict(shot)["id"],))
    for status in ("Approved", "Pending HR"):
        db.execute_update("INSERT INTO leave_requests (user_id, type, start_date, end_date, status) "
                          "VALUES ('priya', 'Casual', %s, %s, %s)",
                          (date(2026, 10, 8), date(2026, 10, 8), status))
    tasks, away = repo.people_data(date(2026, 10, 1), date(2026, 10, 31))
    assert [t["shot_name"] for t in tasks] == ["SH010"]
    assert len(away) == 1 and away[0].person == "priya"


def test_the_calendar_has_the_holidays_with_their_names(db, repo):
    db.execute_update("INSERT INTO holiday_calendar (holiday_date, name, location) VALUES (%s, %s, %s)",
                      (date(2026, 11, 9), "Diwali", "All"))
    cal = repo.calendar(date(2026, 11, 1), date(2026, 11, 30))
    assert cal.holidays == {date(2026, 11, 9): "Diwali"}
    assert not cal.is_working(date(2026, 11, 9))


# ---------------------------------------------------------------- migration

OLD_SCHEDULE = """
    CREATE TABLE prod_scheduling (
        id {serial},
        project_code TEXT, milestone TEXT, start_date TEXT, end_date TEXT,
        status TEXT, depends_on_id INTEGER
    )"""


def test_PRD_019_PRD_020_the_migration_converts_old_rows_and_keeps_their_text(db):
    from slate.core.infra.migrations import change_feed, production_schema
    from slate.core.infra.migrations.workplace_schema import _column_type

    pg = _is_pg(db)
    db.execute_update("DROP TABLE prod_scheduling" + (" CASCADE" if pg else ""))
    db.execute_update(OLD_SCHEDULE.format(
        serial="SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"))
    rows = [
        (1, "ISO", "2026-09-01", "2026-09-05", None),
        (2, "Day first", "30/09/2026", "15/10/2026", 1),
        (3, "Nothing", "None", "", None),
        (4, "Rubbish", "next week", "2026-10-01", 99),
        (5, "Reversed", "2026-10-10", "2026-10-01", None),
    ]
    for row in rows:
        db.execute_update("INSERT INTO prod_scheduling (id, milestone, start_date, end_date, "
                          "depends_on_id, project_code, status) VALUES (%s, %s, %s, %s, %s, 'P', "
                          "'Scheduled')", row)
    try:
        assert production_schema.apply_migration(db)
        assert production_schema.apply_migration(db), "running it again is a no-op"
        got = {dict(r)["id"]: dict(r) for r in db.execute_query(
            "SELECT id, start_date, end_date, depends_on_id, legacy_dates FROM prod_scheduling",
            fetch="all")}
        read = lambda v: DS.parse_date(v)
        assert read(got[2]["start_date"]) == date(2026, 9, 30)
        assert read(got[2]["end_date"]) == date(2026, 10, 15)
        assert got[2]["depends_on_id"] == 1
        assert got[3]["start_date"] is None and got[3]["end_date"] is None
        assert got[4]["start_date"] is None and "next week" in got[4]["legacy_dates"]
        assert got[4]["depends_on_id"] is None and "#99" in got[4]["legacy_dates"]
        assert read(got[5]["start_date"]) == date(2026, 10, 10), "reversed rows are kept"
        if pg:
            assert _column_type(db, "prod_scheduling", "start_date") == "date"
            assert _column_type(db, "prod_scheduling", "end_date") == "date"
            # Deleting a parent now clears the link on its own.
            db.execute_update("DELETE FROM prod_scheduling WHERE id = 1")
            child = db.execute_query("SELECT depends_on_id FROM prod_scheduling WHERE id = 2",
                                     fetch="one")
            assert dict(child)["depends_on_id"] is None
    finally:
        change_feed.apply_migration(db)


def test_PRD_075_PRD_122_the_model_knows_the_columns_the_migration_creates(db):
    from slate.core.infra.models.operations import (ProdBidLineModel, ProdBiddingModel,
                                                    ProdSchedulingModel)
    from slate.core.infra.migrations.workplace_schema import _column_exists

    for model, table in ((ProdSchedulingModel, "prod_scheduling"),
                         (ProdBiddingModel, "prod_bidding"),
                         (ProdBidLineModel, "prod_bid_lines")):
        for column in model.__table__.columns.keys():
            assert _column_exists(db, table, column), f"{table}.{column} is in the model only"
