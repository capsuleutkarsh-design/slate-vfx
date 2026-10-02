"""
Bidding's data, on SQLite and on a real PostgreSQL.

    PRD-077  create returns the id
    PRD-078  saving an unchanged bid keeps its figures exactly
    PRD-079/081  a sent/won bid is read-only; the project never changes in edit
    PRD-082  money round-trips to the paisa (17,636,684.16 stays itself)
    PRD-088  revise: v2 with copied lines, v1 superseded and kept
    PRD-089  lines saved, read back, replaced, deleted with the bid
    PRD-091/096  archive several at once, restore, out of the pipeline
    PRD-098/099/123  sent_at, decided_by/at; approve_bid; never your own bid
    PRD-117  omitted shots are not imported
    PRD-124  tracking reads the dashboard's tasks
    PRD-133  project_name is the project's name, not its code
    PRD-138  listing 3,000 bids does not query per bid
    create shots from a won bid, through the shot registry
"""

from decimal import Decimal

import pytest

from slate.core.domain import bidding as DB
from slate.core.infra.bid_repository import Bid, BidRepository


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    if request.param == "sqlite":
        yield request.getfixturevalue("mock_db")
    else:
        yield request.getfixturevalue("pg_db")


@pytest.fixture
def repo(db):
    for code, name in (("AVTR3", "Avatar 3"), ("KALKI2", "Kalki Chapter 2")):
        db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, 1)",
                          (code, name))
    return BidRepository(db, username="priya")


def line(label="SH010 comp", days=3, rate=8000, shots=1, dept="comp", shot=""):
    return DB.BidLine(label=label, department=dept, complexity="Medium", shot_count=shots,
                      days_per_shot=Decimal(str(days)), day_rate=Decimal(str(rate)), shot_name=shot)


def new_bid(code="AVTR3", currency="INR", margin=20, tax=18, client="Studio X"):
    return Bid(project_code=code, client_name=client, currency=currency, day_rate=Decimal(8000),
               margin=Decimal(margin), tax=Decimal(tax))


def test_PRD_077_PRD_133_create_reads_back_exactly(repo):
    bid_id = repo.create(new_bid(), [line(), line("Roto", days=2, rate=6000, shots=10, dept="roto")])
    bid = repo.get(bid_id)
    assert bid.project_name == "Avatar 3"
    assert bid.estimated_cost == Decimal("144000.00")
    assert bid.estimated_budget == Decimal("180000.00")
    assert bid.tax_amount == Decimal("32400.00") and bid.total_amount == Decimal("212400.00")
    assert bid.bid_group == bid_id and bid.revision == 1 and bid.status == DB.DRAFT
    assert bid.created_by == "priya" and bid.line_count == 2
    lines = repo.lines(bid_id)
    assert [l.label for l in lines] == ["SH010 comp", "Roto"]
    assert lines[1].shot_count == 10 and lines[1].day_rate == Decimal("6000.00")


def test_PRD_078_saving_unchanged_keeps_every_figure(repo):
    bid_id = repo.create(new_bid(currency="USD", tax=0, margin=25), [line("Hard", days=7, rate=450, shots=17)])
    before = repo.get(bid_id)
    repo.update(before, repo.lines(bid_id))
    after = repo.get(bid_id)
    assert (after.estimated_cost, after.estimated_budget) == (Decimal("53550.00"), Decimal("71400.00"))


def test_PRD_082_large_money_round_trips(repo):
    lines = [line("Everything", days=Decimal("2939.45"), rate=Decimal("4800.00"))]
    bid_id = repo.create(new_bid(margin=20, tax=0), lines)
    bid = repo.get(bid_id)
    assert bid.estimated_cost == Decimal("14109360.00")
    assert bid.estimated_budget == Decimal("17636700.00")
    lines = [line("Exact", days=1, rate=Decimal("14109347.33"))]
    bid_id = repo.create(new_bid(margin=20, tax=0), lines)
    assert repo.get(bid_id).estimated_budget == Decimal("17636684.16")


def test_PRD_079_PRD_081_sent_bids_are_read_only_and_the_project_is_locked(repo):
    bid_id = repo.create(new_bid(), [line()])
    draft = repo.get(bid_id)
    draft.project_code = "KALKI2"
    repo.update(draft, [line(days=4)])
    assert repo.get(bid_id).project_code == "AVTR3", "edit never moves a bid to another project"
    repo.set_status([bid_id], DB.SENT)
    with pytest.raises(DB.BidError, match="new revision"):
        repo.update(repo.get(bid_id), [line(days=5)])
    assert repo.get(bid_id).sent_at is not None


def test_PRD_088_revise_copies_and_supersedes(repo):
    v1 = repo.create(new_bid(), [line(), line("Roto", dept="roto")])
    repo.set_status([v1], DB.SENT)
    v2 = repo.revise(v1, by="rahul")
    first, second = repo.get(v1), repo.get(v2)
    assert first.status == DB.SUPERSEDED
    assert second.status == DB.DRAFT and second.revision == 2 and second.bid_group == v1
    assert [l.label for l in repo.lines(v2)] == ["SH010 comp", "Roto"]
    assert [b.id for b in repo.list()] == [v2], "the table shows the latest revision"
    assert {b.id for b in repo.list(all_revisions=True)} == {v1, v2}
    assert [b.revision for b in repo.revisions(v1)] == [1, 2]
    with pytest.raises(DB.BidError):
        repo.revise(v1)


def test_PRD_089_lines_are_replaced_and_go_with_a_deleted_draft(db, repo):
    bid_id = repo.create(new_bid(), [line(), line("Paint", dept="prep")])
    bid = repo.get(bid_id)
    repo.update(bid, [line("Only one", days=2)])
    assert [l.label for l in repo.lines(bid_id)] == ["Only one"]
    admin = BidRepository(db, roles=["Admin"], username="admin")
    admin.delete_draft(bid_id)
    assert repo.get(bid_id) is None
    left = db.execute_query("SELECT COUNT(*) AS n FROM prod_bid_lines WHERE bid_id = %s", (bid_id,),
                            fetch="one")
    assert int(dict(left)["n"]) == 0


def test_PRD_096_hard_delete_only_for_admins_and_plain_drafts(db, repo):
    bid_id = repo.create(new_bid(), [line()])
    with pytest.raises(PermissionError):
        BidRepository(db, roles=["Production Head"]).delete_draft(bid_id)
    repo.set_status([bid_id], DB.SENT)
    with pytest.raises(DB.BidError, match="archive"):
        BidRepository(db, roles=["Admin"]).delete_draft(bid_id)


def test_PRD_091_PRD_096_archive_several_and_restore(repo):
    a = repo.create(new_bid(), [line()])
    b = repo.create(new_bid("KALKI2"), [line()])
    assert repo.archive([a, b], by="priya") == 2
    assert repo.list() == []
    assert {x.id for x in repo.list(include_archived=True)} == {a, b}
    assert DB.pipeline([x.as_dict() for x in repo.list(include_archived=True)]).open_count == 0
    repo.restore([a])
    assert [x.id for x in repo.list()] == [a]


def test_PRD_098_PRD_099_PRD_123_who_decides(db, repo):
    bid_id = repo.create(new_bid(), [line()], by="priya")
    mine = BidRepository(db, roles=["Production Head"], username="priya")
    with pytest.raises(PermissionError, match="somebody else"):
        mine.set_status([bid_id], DB.WON)
    coordinator = BidRepository(db, roles=["Production Coordinator"], username="coord")
    with pytest.raises(PermissionError, match="Approve bids"):
        coordinator.set_status([bid_id], DB.WON)
    head = BidRepository(db, roles=["Production Head"], username="rahul")
    head.set_status([bid_id], DB.WON)
    bid = repo.get(bid_id)
    assert bid.status == DB.WON and bid.decided_by == "rahul" and bid.decided_at is not None
    with pytest.raises(PermissionError):
        coordinator.set_status([bid_id], DB.DRAFT)       # reopening a decision is a decision
    head.set_status([bid_id], DB.DRAFT)
    assert repo.get(bid_id).decided_by == ""
    history = db.execute_query("SELECT * FROM change_history WHERE entity_type = 'bid' "
                               "AND field_changed = 'status'", fetch="all")
    assert len(history) == 2


def test_a_superseded_bid_cannot_change_status(repo):
    v1 = repo.create(new_bid(), [line()])
    repo.revise(v1)
    with pytest.raises(DB.BidError):
        repo.set_status([v1], DB.SENT)


def test_PRD_117_omitted_shots_are_not_imported(db, repo):
    for shot, status in (("SH010", "WIP"), ("SH020", "OMIT"), ("SH030", "Omitted")):
        db.execute_update("INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                          "data_json, last_updated, version) VALUES ('AVTR3', 'R1', %s, %s, 2, '{}', "
                          "'2026-09-01', 1)", (shot, status))
    shots, skipped = repo.importable_shots("AVTR3")
    assert shots == [("R1", "SH010")] and skipped == 2


def test_PRD_124_tracking_reads_the_dashboard(db, repo):
    bid_id = repo.create(new_bid(), [line("Comp", days=2, shots=10, dept="comp")])
    db.execute_update("INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                      "data_json, last_updated, version) VALUES ('AVTR3', 'R1', 'SH010', 'WIP', 2, '{}', "
                      "'2026-09-01', 1)")
    shot = dict(db.execute_query("SELECT id FROM tracking_shots", fetch="one"))["id"]
    db.execute_update("INSERT INTO tracking_tasks (shot_id, project_code, department, status, "
                      "artist_name, bid_days, target_date, actual_days) VALUES (%s, 'AVTR3', 'comp', "
                      "'Approved', 'priya', 25, '', 27)", (shot,))
    t = repo.tracking(repo.get(bid_id))
    comp = t.departments[0]
    assert comp.bid_days == Decimal(20) and comp.planned_days == Decimal(25)
    assert comp.done_days == Decimal(25) and comp.actual_days == Decimal(27) and comp.over


def test_create_shots_from_a_won_bid(db, repo):
    bid_id = repo.create(new_bid(), [line("SH010 comp", days=3, shot="SH010"),
                                     line("SH010 roto", days=1, dept="roto", shot="SH010"),
                                     line("SH020 comp", days=2, shot="SH020"),
                                     line("Roto - 40 shots", days=2, shots=40, dept="roto")])
    with pytest.raises(DB.BidError):
        repo.create_shots(bid_id)
    db.execute_update("INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                      "data_json, last_updated, version) VALUES ('AVTR3', '', 'SH020', 'WIP', 2, '{}', "
                      "'2026-09-01', 1)")
    BidRepository(db, roles=["Admin"], username="admin").set_status([bid_id], DB.WON)
    result = repo.create_shots(bid_id)
    assert result["created"] == ["SH010"] and result["existing"] == ["SH020"]
    assert result["group_lines"] == 1 and not result["error"]
    tasks = {(dict(t)["department"]): dict(t) for t in db.get_tracking_tasks("AVTR3")}
    assert float(tasks["comp"]["bid_days"]) == 3.0 and float(tasks["roto"]["bid_days"]) == 1.0


def test_PRD_138_listing_many_bids_is_two_queries(db, repo, monkeypatch):
    for _ in range(30):
        repo.create(new_bid(), [line(), line()])
    calls = []
    real = db.execute_query

    def counted(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "execute_query", counted)
    bids = repo.list()
    assert len(bids) == 30 and all(b.line_count == 2 for b in bids)
    assert len(calls) == 2


def test_PRD_082_the_migration_repairs_float4_budgets(db):
    from slate.core.infra.migrations.production_schema import repair_bid_budgets
    db.execute_update("INSERT INTO prod_bidding (project_code, estimated_cost, target_margin, "
                      "estimated_budget, status) VALUES ('OLD', 14109347.33, 20, 17636684.0, 'Draft')")
    db.execute_update("INSERT INTO prod_bidding (project_code, estimated_cost, target_margin, "
                      "estimated_budget, status) VALUES ('TYPED', 1000, 20, 5000, 'Draft')")
    assert repair_bid_budgets(db) == 1
    rows = {dict(r)["project_code"]: DB.money(dict(r)["estimated_budget"])
            for r in db.execute_query("SELECT project_code, estimated_budget FROM prod_bidding",
                                      fetch="all")}
    assert rows == {"OLD": Decimal("17636684.16"), "TYPED": Decimal("5000.00")}


def test_existing_bids_are_dollars_and_get_a_group(db):
    from slate.core.infra.migrations.production_schema import apply_migration
    db.execute_update("INSERT INTO prod_bidding (project_code, estimated_budget, status, currency) "
                      "VALUES ('OLD', 100, 'Draft', 'USD')")
    apply_migration(db)
    bid = BidRepository(db).list()[0]
    assert bid.currency == "USD" and bid.bid_group == bid.id and bid.revision == 1
    assert [l.label for l in BidRepository(db).lines(bid.id)] == ["Whole job"]


def test_NEW_production_1_revise_archive_and_create_shots_are_gated(db, repo):
    bid_id = repo.create(new_bid(), [line(shot="SH010")], by="priya")
    BidRepository(db, roles=["Admin"], username="admin").set_status([bid_id], DB.WON)
    coord = BidRepository(db, roles=["Production Coordinator"], username="coord")
    with pytest.raises(PermissionError):
        coord.revise(bid_id)
    with pytest.raises(PermissionError):
        coord.archive([bid_id])
    maker = BidRepository(db, roles=["Production Head"], username="priya")
    assert maker.decided_refusal(maker.get(bid_id))           # her own bid
    head = BidRepository(db, roles=["Production Head"], username="rahul")
    assert head.decided_refusal(head.get(bid_id)) == ""
    assert repo.get(bid_id).status == DB.WON, "nothing changed"
    no_dash = BidRepository(db, roles=["Production Coordinator"], username="coord")
    no_dash.roles = ["Bid Clerk"]
    with pytest.raises(PermissionError, match="dashboard"):
        no_dash.create_shots(bid_id)
    draft = coord.create(new_bid("KALKI2"), [line()])
    assert coord.archive([draft]) == 1, "drafts are anybody's to archive"
