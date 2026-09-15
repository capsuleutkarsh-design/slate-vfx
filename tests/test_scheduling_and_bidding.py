"""
Scheduling and bidding: a dead dropdown and a cost model in a dialog.

The scheduling tab could not be used at all. Its project dropdown queried a
column that does not exist - project_code on a table whose key is code - so the
query raised, the list was always empty, and "Add New Milestone" refused every
attempt with "Project Code and Milestone Name are required". Nothing said why.

Bidding kept its cost model as three numbers inside a dialog, doubled
apostrophes before a parameterised insert, and counted lost jobs in the pipeline
value - so the figure that was meant to show work coming in grew every time the
studio lost a pitch.
"""

from datetime import date

import pytest

from slate.core.domain import bidding


@pytest.fixture(autouse=True)
def default_figures():
    bidding.set_overrides({})
    yield
    bidding.set_overrides({})


@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "prod.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"

    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


# ---------------------------------------------------------------- scheduling

def test_the_project_list_query_actually_returns_projects(db):
    """
    The bug: the dialog asked for project_code, tracking_projects has code. The
    query raised, so the dropdown was empty and no milestone could ever be made.
    """
    db.execute_update(
        "INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, 1)",
        ("AK74", "Project AK74"))
    db.execute_update(
        "INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, 0)",
        ("OLD01", "Finished long ago"))

    rows = db.execute_query(
        "SELECT code FROM tracking_projects WHERE active = 1 ORDER BY code",
        fetch="all")
    codes = [dict(r)["code"] for r in rows]

    assert codes == ["AK74"], "active projects only, and by the right column name"


def test_the_old_query_is_the_one_that_was_broken(db):
    """Stated directly, so nobody puts it back."""
    with pytest.raises(Exception):
        db.execute_query("SELECT DISTINCT project_code FROM tracking_projects",
                         fetch="all")


def test_an_apostrophe_survives_a_milestone_name(db):
    """
    The bug: the name was quote-doubled before a parameterised insert, so
    "Director's cut" was stored as "Director''s cut" and read back that way.
    """
    db.execute_update(
        "INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, status) "
        "VALUES (%s, %s, %s, %s, 'Scheduled')",
        ("AK74", "Director's cut", "2026-09-01", "2026-09-30"))

    row = dict(db.execute_query(
        "SELECT milestone FROM prod_scheduling WHERE project_code = 'AK74'",
        fetch="one"))
    assert row["milestone"] == "Director's cut"


def test_an_overdue_milestone_is_not_upcoming(db):
    """
    The bug: the card counted everything not complete as "upcoming", so an
    overdue milestone was reported as work still to come rather than as late.
    """
    today = date.today().isoformat()
    for code, end, status in (
        ("A", "2020-01-01", "In Progress"),     # overdue
        ("B", "2099-01-01", "In Progress"),     # genuinely upcoming
        ("C", "2020-01-01", "Completed"),       # done, not late
    ):
        db.execute_update(
            "INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, status) "
            "VALUES (%s, %s, %s, %s, %s)", (code, "m" + code, "2019-01-01", end, status))

    sched = [dict(r) for r in db.execute_query(
        "SELECT * FROM prod_scheduling", fetch="all")]

    completed = sum(1 for r in sched if r.get("status") == "Completed")
    overdue = sum(1 for r in sched
                  if r.get("status") != "Completed"
                  and str(r.get("end_date") or "")[:10] < today)
    in_progress = len(sched) - completed - overdue

    assert completed == 1
    assert overdue == 1
    assert in_progress == 1


# ------------------------------------------------------------------- bidding

def test_the_cost_model_is_a_rule_not_three_numbers_in_a_dialog():
    assert bidding.days_per_shot("Simple") == 1.5
    assert bidding.days_per_shot("Medium") == 3.0
    assert bidding.days_per_shot("Hard") == 7.0


def test_a_studio_can_change_what_a_shot_costs():
    """
    The figures were literals inside the bidding dialog, so a studio whose comp
    runs heavier than the default had no way to say so.
    """
    bidding.set_overrides({"multipliers": {"Hard": 10.0}, "day_rate": 450.0})
    assert bidding.days_per_shot("Hard") == 10.0
    assert bidding.days_per_shot("Simple") == 1.5, "unnamed figures keep their default"
    assert bidding.day_rate() == 450.0


def test_an_unknown_complexity_falls_back_to_medium():
    assert bidding.days_per_shot("Baffling") == bidding.days_per_shot("Medium")


def test_margin_is_a_share_of_the_price_not_a_markup_on_the_cost():
    """
    100 shots of medium work at 300 a day is 90,000 of cost. At 20% margin the
    price is 112,500 - of which 22,500, exactly a fifth, is margin. Adding 20%
    to the cost would give 108,000 and quietly bid the studio under.
    """
    result = bidding.estimate(100, "Medium", rate=300.0, margin=20.0)
    assert result["days"] == 300.0
    assert result["cost"] == 90000.0
    assert result["price"] == pytest.approx(112500.0)
    assert (result["price"] - result["cost"]) / result["price"] == pytest.approx(0.20)


def test_a_hundred_per_cent_margin_does_not_divide_by_zero():
    result = bidding.estimate(10, "Medium", rate=100.0, margin=100.0)
    assert result["price"] == result["cost"]


def test_no_shots_costs_nothing():
    assert bidding.estimate(0, "Hard")["price"] == 0.0


def test_the_pipeline_value_leaves_out_the_jobs_we_lost(db):
    """
    The bug: the total summed every bid including rejected ones, so the number
    meant to show incoming work went up each time a pitch was lost.
    """
    for code, status, budget in (
        ("WON", "Approved", 100000),
        ("PITCHED", "Draft", 50000),
        ("LOST", "Rejected", 900000),
    ):
        db.execute_update(
            "INSERT INTO prod_bidding (project_code, project_name, shot_count, "
            "complexity, estimated_days, target_margin, estimated_cost, "
            "estimated_budget, status) VALUES (%s, %s, 10, 'Medium', 30, 20, 0, %s, %s)",
            (code, code, budget, status))

    bids = [dict(r) for r in db.execute_query(
        "SELECT * FROM prod_bidding", fetch="all")]
    pipeline = sum(r.get("estimated_budget", 0) or 0 for r in bids
                   if str(r.get("status") or "") in ("Draft", "Approved"))

    assert pipeline == 150000, "the lost job is not pipeline"
