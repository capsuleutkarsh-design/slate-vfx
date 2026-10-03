"""
Round-2 audit fixes for Scheduling and Bidding (PRD2-*): the rules and the
repositories, on SQLite and on a real PostgreSQL.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from slate.core.domain import bidding as DB
from slate.core.domain import scheduling as DS
from slate.core.infra.bid_repository import Bid, BidRepository, _check_bidding_settings
from slate.core.infra.schedule_repository import ScheduleRepository


@pytest.fixture(autouse=True)
def default_figures():
    DB.set_overrides({})
    yield
    DB.set_overrides({})


# ------------------------------------------------------------------ rules

def test_PRD2_021_people_view_uses_the_dashboard_status_words():
    tasks = [{"artist": "priya", "status": s, "target_date": "2026-10-09", "bid_days": 2}
             for s in ("DELIVERED", "N/A", "Cut", "Cancelled", "WIP")]
    items, _skipped = DS.task_items(tasks, DS.WorkCalendar())
    assert len(items) == 1


def test_PRD2_014_archived_milestones_are_not_overdue_work():
    old = DS.Milestone(id=1, project_code="OLD", name="x", start=date(2026, 1, 1),
                       end=date(2026, 2, 1), archived=True)
    live = DS.Milestone(id=2, project_code="NEW", name="y", start=date(2026, 1, 1), end=date(2026, 2, 1))
    assert not DS.is_overdue(old, date(2026, 10, 1)) and DS.is_overdue(live, date(2026, 10, 1))
    s = DS.summary([old, live], date(2026, 10, 1))
    assert (s.overdue, s.projects, s.total) == (1, 1, 2)


def test_PRD2_006_007_020_a_dragged_bar_says_what_the_hand_did():
    m = DS.Milestone(id=1, project_code="A", name="Comp", start=date(2026, 10, 1), end=date(2026, 10, 17))
    moved = DS.plan_shift([m], 1, 7, root_to=(date(2026, 10, 8), date(2026, 10, 24)))
    assert "7 days later" in moved.message("Comp", preview=True)
    assert "working" not in moved.message("Comp", preview=True)
    end = DS.plan_shift([m], 1, -3, root_to=(date(2026, 10, 1), date(2026, 10, 14)))
    assert "the end of \"Comp\"" in end.message("Comp", preview=True) and "3 days earlier" in end.message("Comp")
    start = DS.plan_shift([m], 1, 0, root_to=(date(2026, 10, 3), date(2026, 10, 17)))
    assert "the start of \"Comp\"" in start.message("Comp", preview=True)


def test_PRD2_005_day_zoom_labels_are_the_day_number():
    ticks = DS.ruler_ticks(date(2026, 10, 5), date(2026, 10, 6), DS.ZOOM_DAY)
    assert [label for _d, label, _m in ticks] == ["5", "6"]


def test_PRD2_040_041_077_price_subtotal_and_below_cost():
    assert DB.WORDS["price"] == "Price" and DB.WORDS["taxable"] == "Subtotal"
    line = DB.BidLine(label="x", shot_count=1, days_per_shot=Decimal(3), day_rate=Decimal(8000))
    fine = DB.price_bid([line], 20, 10)
    assert DB.below_cost_warning(fine, "INR") == ""
    under = DB.price_bid([line], 20, 30)
    assert "below its cost" in DB.below_cost_warning(under, "INR")
    assert DB.signed_money(Decimal("-9000"), "INR").startswith("−")
    assert DB.signed_money(Decimal("5"), "USD").startswith("+")


def test_PRD2_038_044_pipeline_keeps_won_work_and_separates_legacy_jobs():
    bids = [{"id": 1, "bid_group": 1, "status": DB.WON, "project_code": "A", "estimated_budget": 100,
             "currency": "USD"},
            {"id": 2, "bid_group": 1, "status": DB.DRAFT, "project_code": "A", "estimated_budget": 150,
             "currency": "USD"},
            {"id": 3, "status": DB.DRAFT, "project_name": "OLDJOB", "estimated_budget": 5000,
             "currency": "USD"},
            {"id": 4, "status": DB.DRAFT, "project_name": "NULLJOB", "estimated_budget": 7000,
             "currency": "USD"}]
    p = DB.pipeline(bids)
    assert p.won_totals["USD"] == 100 and p.open_totals["USD"] == 12000 and p.open_count == 2


def test_PRD2_055_tracking_skips_omitted_shots_and_counts_delivered():
    lines = [DB.BidLine(label="c", department="comp", shot_count=1, days_per_shot=Decimal(4),
                        day_rate=Decimal(100))]
    tasks = [{"department": "comp", "status": "Delivered", "bid_days": 4, "shot_status": "WIP"},
             {"department": "comp", "status": "", "bid_days": 9, "shot_status": "OMIT"}]
    t = DB.track(lines, tasks)
    assert t.planned_days == 4 and t.done_days == 4


def test_PRD2_058_complexity_names_are_kept_and_case_duplicates_refused():
    DB.set_overrides({"multipliers": {"cg-heavy VFX": 9, "hard": 8}})
    table = DB.multipliers()
    assert table["cg-heavy VFX"] == 9 and table["hard"] == 8 and "Hard" not in table
    assert DB.days_per_shot("HARD") == 8
    with pytest.raises(ValueError, match="twice"):
        _check_bidding_settings({"multipliers": {"Hard": 7, "hard": 8}})


# ------------------------------------------------------------------ repositories

@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    if request.param == "sqlite":
        yield request.getfixturevalue("mock_db")
    else:
        yield request.getfixturevalue("pg_db")


@pytest.fixture
def sched(db):
    for code, active in (("AVTR3", 1), ("OLDPROJ", 0)):
        db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, %s)",
                          (code, code, active))
    return ScheduleRepository(db)


def _ms(name, start, end, dep=None, project="AVTR3", status="Scheduled"):
    return DS.Milestone(project_code=project, name=name, start=date.fromisoformat(start),
                        end=date.fromisoformat(end), depends_on_id=dep, status=status)


def test_PRD2_017_milestones_go_on_active_projects_only(db, sched):
    with pytest.raises(DS.ScheduleError, match="no project NOPE"):
        sched.add(_ms("x", "2026-10-01", "2026-10-02", project="NOPE"), by="priya")
    with pytest.raises(DS.ScheduleError, match="archived"):
        sched.add(_ms("x", "2026-10-01", "2026-10-02", project="OLDPROJ"), by="priya")
    db.execute_update("INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, status) "
                      "VALUES ('OLDPROJ', 'Old one', '2025-01-01', '2025-01-02', 'Scheduled')")
    old = next(m for m in sched.list(include_archived=True) if m.name == "Old one")
    old.name = "Old one renamed"
    assert sched.edit(old, by="priya") == ["name"], "an archived project's milestone stays editable"


def test_PRD2_015_a_delete_can_be_undone_with_its_links(sched):
    a = sched.add(_ms("Roto", "2026-10-01", "2026-10-05"), by="priya")
    b = sched.add(_ms("Comp", "2026-10-06", "2026-10-09", dep=a), by="priya")
    doomed = [sched.get(a)]
    links = [(m.id, m.depends_on_id) for m in sched.dependents([a])]
    sched.delete([a], by="priya")
    assert sched.get(b).depends_on_id is None
    sched.restore_deleted(doomed, links, by="priya")
    back = sched.get(a)
    assert back.name == "Roto" and back.start == date(2026, 10, 1)
    assert sched.get(b).depends_on_id == a


def test_PRD2_035_undo_leaves_a_colleagues_change_alone(sched):
    a = sched.add(_ms("A", "2026-10-01", "2026-10-05"), by="priya")
    b = sched.add(_ms("B", "2026-10-06", "2026-10-09"), by="priya")
    previous = sched.set_status([a, b], "In Progress", by="priya")
    sched.set_status([b], "Blocked", by="rahul")
    kept = sched.restore_statuses(previous, by="priya", expected="In Progress")
    assert kept == ["B"] and sched.get(a).status == "Scheduled" and sched.get(b).status == "Blocked"

    plan = DS.plan_shift(sched.list(), a, 2, unit=DS.CALENDAR_DAYS)
    sched.apply_dates(plan.changes(), by="priya")
    assert sched.undo_shift(plan, by="priya") == []
    assert sched.get(a).start == date(2026, 10, 1)


def test_PRD2_019_office_holidays_are_not_studio_holidays(db, sched):
    for day, name, where in ((date(2026, 11, 9), "Diwali", "All"), (date(2026, 11, 10), "Local", "Mumbai")):
        db.execute_update("INSERT INTO holiday_calendar (holiday_date, name, location) VALUES (%s, %s, %s)",
                          (day, name, where))
    cal = sched.calendar(date(2026, 11, 1), date(2026, 11, 30))
    assert not cal.is_working(date(2026, 11, 9)) and cal.is_working(date(2026, 11, 10))


@pytest.fixture
def bids(db):
    for code in ("AVTR3", "KALKI2"):
        db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, 1)",
                          (code, code))
    return BidRepository(db, username="priya")


def _line(label="SH010 comp", days=3, rate=8000, shot="", reel="", dept="comp"):
    return DB.BidLine(label=label, department=dept, complexity="Medium", shot_count=1,
                      days_per_shot=Decimal(days), day_rate=Decimal(rate), shot_name=shot, reel=reel)


def _bid(code="AVTR3", margin=20, discount=0):
    return Bid(project_code=code, currency="INR", margin=Decimal(margin), discount=Decimal(discount))


def _shot(db, shot, reel):
    db.execute_update("INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                      "data_json, last_updated, version) VALUES ('AVTR3', %s, %s, 'WIP', 2, '{}', "
                      "'2026-09-01', 1)", (reel, shot))


def test_PRD2_037_create_shots_finds_a_shot_in_any_reel(db, bids):
    _shot(db, "SH010", "R1")
    _shot(db, "SH020", "R1")
    _shot(db, "SH020", "R2")
    bid_id = bids.create(_bid(), [_line(shot="SH900"), _line("b", shot="SH010"), _line("c", shot="SH020")])
    BidRepository(db, roles=["Admin"], username="admin").set_status([bid_id], DB.WON)
    result = bids.create_shots(bid_id)
    assert result["created"] == ["SH900"] and result["existing"] == ["SH010"]
    assert [name for name, _why in result["refused"]] == ["SH020"]
    reels = [dict(r).get("reel") for r in db.execute_query(
        "SELECT reel FROM tracking_shots WHERE shot_name = 'SH010'", fetch="all")]
    assert reels == ["R1"], "no reel-less duplicate"


def test_PRD2_038_080_revising_won_work_keeps_it_won(db, bids):
    admin = BidRepository(db, roles=["Admin"], username="admin")
    v1 = bids.create(_bid(), [_line()])
    admin.set_status([v1], DB.WON)
    v2 = bids.revise(v1)
    assert bids.get(v1).status == DB.WON and not bids.get(v1).latest
    assert {b.id for b in bids.list()} == {v1, v2}
    with pytest.raises(DB.BidError, match="newer revision"):
        bids.revise(v1)
    admin.set_status([v2], DB.WON)
    assert bids.get(v1).status == DB.SUPERSEDED
    archived = bids.create(_bid("KALKI2"), [_line()])
    bids.archive([archived])
    with pytest.raises(DB.BidError, match="archived"):
        bids.revise(archived)


def test_PRD2_046_a_once_won_draft_cannot_be_deleted(db, bids):
    admin = BidRepository(db, roles=["Admin"], username="admin")
    bid_id = bids.create(_bid(), [_line()])
    admin.set_status([bid_id], DB.WON)
    admin.set_status([bid_id], DB.DRAFT)
    with pytest.raises(DB.BidError, match="history"):
        admin.delete_draft(bid_id)


def test_PRD2_039_047_a_bid_that_cannot_be_priced_keeps_its_stored_figures(db, bids):
    db.execute_update("INSERT INTO prod_bidding (project_code, estimated_cost, estimated_days, "
                      "target_margin, estimated_budget, total_amount, status, currency) VALUES "
                      "('AVTR3', 21000, 70, 100, 21000, 21000, 'Approved', 'USD')")
    bid = bids.list()[0]
    totals, priced = bid.totals(bids.lines(bid.id))
    assert not priced and totals.total == Decimal("21000.00") and totals.cost == Decimal("21000.00")


def test_PRD2_056_without_bid_write_bids_are_read_only(db, bids):
    viewer = BidRepository(db, roles=["Bid Viewer"], username="vic")
    with pytest.raises(PermissionError, match="Edit bids"):
        viewer.create(_bid(), [_line()])
    draft = bids.create(_bid(), [_line()])
    with pytest.raises(PermissionError):
        viewer.set_status([draft], DB.SENT)


def test_PRD2_073_refusal_says_what_was_being_done(db, bids):
    bid_id = bids.create(_bid(), [_line()])
    BidRepository(db, roles=["Admin"], username="admin").set_status([bid_id], DB.WON)
    coord = BidRepository(db, roles=["Production Coordinator"], username="coord")
    text = coord.decided_refusal(coord.get(bid_id))
    assert "revising or archiving it changes a decision" in text and "Marking" not in text


def test_PRD2_079_a_status_change_can_be_undone(db, bids):
    admin = BidRepository(db, roles=["Admin"], username="admin")
    bid_id = bids.create(_bid(), [_line()])
    before = bids.revisions(bid_id)
    admin.set_status([bid_id], DB.SENT)
    assert bids.get(bid_id).sent_at is not None
    assert admin.undo_status(before, DB.SENT) == []
    back = bids.get(bid_id)
    assert back.status == DB.DRAFT and not back.sent_at
