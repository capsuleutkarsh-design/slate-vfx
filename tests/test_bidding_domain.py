"""
The bidding rules, without a database.

    PRD-082/121  Decimal money, rounded half-up once (279,300 / 0.85 = 3,28,588.24)
    PRD-083      no cap: 23 Hard shots at 10 lakh a day at 90% is 161 crore
    PRD-085      a margin of 100% or more is refused, not priced at cost
    PRD-087      the pipeline counts the latest open bid per project, per currency
    PRD-088      revisions compare line by line
    PRD-089      line items price exactly
    PRD-095      discount, then GST: 1,00,000 less 10% plus 18% = 1,06,200
    PRD-098      allowed status changes; who may decide
    PRD-102/116  custom complexities; a studio table can replace the defaults
    PRD-106/126  '12.5%', '791 days', '1 day'
    PRD-124      tracking: bid vs planned vs done vs actual, per department
    PRD-132      a day rate of 0 in the settings is not used
"""

import random
from decimal import Decimal

import pytest

from slate.core.domain import bidding as DB
from slate.core.domain import bid_export


@pytest.fixture(autouse=True)
def default_figures():
    DB.set_overrides({})
    yield
    DB.set_overrides({})


def line(label="SH010", days=3, rate=8000, shots=1, dept="comp", complexity="Medium", shot=""):
    return DB.BidLine(label=label, department=dept, complexity=complexity, shot_count=shots,
                      days_per_shot=Decimal(str(days)), day_rate=Decimal(str(rate)), shot_name=shot)


def test_PRD_082_the_budget_is_exact():
    lines = [line(days=Decimal("931"), rate=300)]                 # cost 2,79,300
    totals = DB.price_bid(lines, 15)
    assert totals.cost == Decimal("279300.00")
    assert totals.price == Decimal("328588.24")
    assert DB.price_bid([line(days=Decimal("791"), rate=300)], 10).price == Decimal("263666.67")


def test_PRD_083_no_cap_on_a_large_bid():
    lines = [line("Hard shots", days=7, rate=1000000, shots=23, complexity="Hard")]
    totals = DB.price_bid(lines, 90)
    assert totals.days == Decimal("161.00")
    assert totals.cost == Decimal("161000000.00")
    assert totals.price == Decimal("1610000000.00")


def test_PRD_085_a_margin_of_100_or_more_is_refused():
    with pytest.raises(DB.BidError):
        DB.price_bid([line()], 100)
    with pytest.raises(DB.BidError):
        DB.price_bid([line()], -1)
    problems = DB.check_bid("AVTR3", [line()], Decimal("99.99"))
    assert any("limit of 80%" in p for p in problems)


def test_PRD_095_discount_then_gst():
    lines = [line("Whole job", days=1, rate=80000)]
    totals = DB.price_bid(lines, 20, discount=10, tax=18)
    assert totals.price == Decimal("100000.00")
    assert totals.discount_amount == Decimal("10000.00")
    assert totals.taxable == Decimal("90000.00")
    assert totals.tax_amount == Decimal("16200.00")
    assert totals.total == Decimal("106200.00")
    assert totals.margin_amount == Decimal("20000.00")


def test_PRD_089_mixed_lines_add_up_to_the_paisa():
    lines = [line("Roto - wide shots", days=Decimal("2.5"), rate=Decimal("7999.99"), shots=40, dept="roto"),
             line("Paint", days=Decimal("1.25"), rate=8000, shots=12, dept="prep"),
             line("SH010 comp", days=7, rate=Decimal("8500.50"), dept="comp", shot="SH010")]
    totals = DB.price_bid(lines, 25)
    assert totals.days == Decimal("122.00")
    assert totals.cost == Decimal("799999.00") + Decimal("120000.00") + Decimal("59503.50")
    assert totals.price == (totals.cost / Decimal("0.75")).quantize(Decimal("0.01"))
    assert totals.shots == 53


def test_PRD_121_random_inputs_round_once():
    rng = random.Random(7)
    for _ in range(100):
        lines = [line(days=Decimal(rng.randint(1, 400)) / 4, rate=Decimal(rng.randint(100, 900000)) / 100,
                      shots=rng.randint(1, 50)) for _ in range(rng.randint(1, 5))]
        totals = DB.price_bid(lines, Decimal(rng.randint(0, 7999)) / 100)
        assert totals.cost == sum((l.cost for l in lines), Decimal(0))
        assert totals.price == totals.price.quantize(Decimal("0.01"))
        assert totals.total == totals.taxable + totals.tax_amount


def test_PRD_093_an_empty_or_free_bid_cannot_be_saved():
    assert "Add at least one line with days." in DB.check_bid("EMPTYPROJ", [], 20)
    assert any("day rate" in p for p in DB.check_bid("EMPTYPROJ", [line(rate=0)], 20))
    assert any("project" in p for p in DB.check_bid("", [line()], 20))
    assert not DB.check_bid("AVTR3", [line()], 20)


def test_PRD_084_a_line_carries_its_own_shot_count():
    group = line("Roto - 40 shots", days=2, rate=8000, shots=40, dept="roto")
    assert group.days == Decimal("80.00") and group.cost == Decimal("640000.00")


def test_PRD_078_a_legacy_bid_opens_with_its_own_rate():
    header = {"shot_count": 17, "complexity": "Hard", "estimated_days": 119,
              "estimated_cost": 53550, "estimated_budget": 71400, "target_margin": 25}
    legacy = DB.legacy_line(header)
    assert legacy.day_rate == Decimal("450.00")
    assert legacy.shot_count == 17 and legacy.days == Decimal("119.00")
    totals = DB.price_bid([legacy], 25)
    assert totals.cost == Decimal("53550.00") and totals.price == Decimal("71400.00")


def test_PRD_098_status_changes_and_who_decides():
    assert DB.can_change(DB.DRAFT, DB.SENT) and DB.can_change(DB.SENT, DB.WON)
    assert DB.can_change(DB.WON, DB.DRAFT)                    # reopen
    assert not DB.can_change(DB.SUPERSEDED, DB.DRAFT)
    assert DB.status_label("Approved") == "Won" and DB.status_label("Rejected") == "Lost"
    assert DB.normalise_status("won") == "Approved"
    for status in DB.STATUSES:
        assert DB.status_tone(status) in {"info", "accent", "ok", "bad", "idle"}
    assert DB.decision_refusal(DB.WON, can_approve=False, superuser=False)
    assert "somebody else" in DB.decision_refusal(DB.WON, can_approve=True, superuser=False,
                                                  creator="priya", me="Priya")
    assert DB.decision_refusal(DB.WON, can_approve=True, superuser=True,
                               creator="admin", me="admin") == ""


def test_PRD_087_the_pipeline_counts_the_latest_open_bid_per_project_per_currency():
    bids = [{"id": i, "project_code": "AVTR3", "status": "Draft", "estimated_budget": 1000 * i,
             "currency": "INR", "created_at": f"2026-09-{i:02d}"} for i in range(1, 11)]
    bids.append({"id": 50, "project_code": "AVTR3", "status": "Draft", "estimated_budget": 357000000,
                 "currency": "INR", "created_at": "2026-09-30", "archived_at": "2026-10-01"})
    bids.append({"id": 51, "project_code": "KALKI2", "status": "Approved", "estimated_budget": 41400,
                 "currency": "USD", "created_at": "2026-09-01"})
    bids.append({"id": 52, "project_code": "KALKI2", "status": "Superseded", "estimated_budget": 9,
                 "currency": "USD"})
    bids.append({"id": 53, "project_code": "RRR", "status": "Rejected", "estimated_budget": 900000,
                 "currency": "INR"})
    p = DB.pipeline(bids)
    assert p.open_totals == {"INR": Decimal(10000)}, "only the newest AVTR3 draft"
    assert p.won_totals == {"USD": Decimal(41400)}
    assert p.open_count == 1 and p.won_count == 1


def test_PRD_088_compare_two_revisions():
    v1 = [line("SH010 comp", days=3), line("Roto", days=2, dept="roto"), line("Paint", days=1, dept="prep")]
    v2 = [line("SH010 comp", days=4), line("Roto", days=2, dept="roto"), line("CG", days=5, dept="cg")]
    changes = {(c.kind, c.label): c for c in DB.compare(v1, v2)}
    assert ("changed", "SH010 comp") in changes
    assert changes[("changed", "SH010 comp")].fields == ["Days per shot"]
    assert ("added", "CG") in changes and ("removed", "Paint") in changes
    assert ("changed", "Roto") not in changes
    assert changes[("changed", "SH010 comp")].cost_delta == Decimal("8000.00")


def test_PRD_106_PRD_126_formats():
    assert DB.fmt_percent(Decimal("12.50")) == "12.5%"
    assert DB.fmt_percent(20) == "20%"
    assert DB.fmt_days(Decimal("791.0")) == "791 days"
    assert DB.fmt_days(Decimal("34.50")) == "34.5 days"
    assert DB.fmt_days(1) == "1 day"


def test_PRD_102_PRD_116_custom_complexities():
    DB.set_overrides({"multipliers": {"Very Hard": 12}})
    assert "Very Hard" in DB.complexities()
    assert DB.days_per_shot("very hard") == 12.0
    assert "Medium" in DB.complexities()
    DB.set_overrides({"multipliers": {"Roto Light": 1, "Roto Heavy": 4}, "multipliers_complete": True})
    assert DB.complexities() == ["Roto Light", "Roto Heavy"]


def test_PRD_132_a_zero_day_rate_in_the_settings_is_not_used(caplog):
    DB.set_overrides({"day_rate": 0})
    with caplog.at_level("WARNING"):
        assert DB.day_rate() == DB.DEFAULT_DAY_RATE
    assert "not more than zero" in caplog.text


def test_PRD_124_tracking_per_department():
    lines = [line("Comp", days=2, rate=8000, shots=10, dept="comp", shot=""),
             line("Roto", days=1, rate=6000, shots=10, dept="roto")]
    tasks = [
        {"department": "comp", "bid_days": 15, "status": "Approved", "actual_days": 18},
        {"department": "comp", "bid_days": 10, "status": "WIP", "actual_days": None},
        {"department": "roto", "bid_days": 8, "status": "Done", "actual_days": "6"},
        {"department": "cg", "bid_days": 0, "status": "", "actual_days": None},
    ]
    t = DB.track(lines, tasks, [("R1", "SH010", "WIP"), ("R1", "SH020", "OMIT")])
    comp = next(d for d in t.departments if d.department == "comp")
    assert comp.bid_days == Decimal("20.00") and comp.planned_days == Decimal(25)
    assert comp.done_days == Decimal(15) and comp.actual_days == Decimal(18)
    assert comp.over and comp.variance_days == Decimal(5)
    assert comp.burn_percent == Decimal("75.0")
    assert comp.rate == Decimal("8000.00") and comp.planned_cost == Decimal("200000.00")
    roto = next(d for d in t.departments if d.department == "roto")
    assert not roto.over and roto.remaining_days == Decimal(2)
    assert all(d.department != "cg" for d in t.departments), "nothing bid or planned"
    assert t.bid_cost == Decimal("220000.00")
    assert t.actual_recorded


def test_PRD_124_shot_coverage():
    lines = [line("SH010 comp", shot="SH010"), line("SH030 comp", shot="SH030")]
    t = DB.track(lines, [], [("R1", "SH010", "WIP"), ("R1", "SH020", "WIP"), ("R1", "SH040", "Omitted")])
    assert t.bid_not_on_tracker == ["SH030"]
    assert t.tracker_not_in_bid == ["SH020"]


def test_create_shots_groups_departments_per_shot():
    lines = [line("SH010 comp", days=3, shot="SH010", dept="comp"),
             line("SH010 roto", days=1, shot="SH010", dept="roto"),
             line("Roto - 40 shots", days=2, shots=40, dept="roto")]
    wanted = DB.shots_to_create(lines)
    assert list(wanted) == [("", "SH010")]
    assert wanted[("", "SH010")] == {"comp": Decimal(3), "roto": Decimal(1)}


def test_PRD_094_list_rows_and_document():
    from types import SimpleNamespace
    bid = SimpleNamespace(project_code="AVTR3", project_name="Avatar 3", client_name="=cmd|bad",
                          revision=2, status="Approved", currency="INR", shot_count=10,
                          estimated_days=Decimal(30), estimated_cost=Decimal(240000),
                          estimated_budget=Decimal(300000), tax_amount=Decimal(54000),
                          total_amount=Decimal(354000), created_at="2026-09-30T10:00:00",
                          created_by="priya", tax_label="GST", notes="Two rounds of notes.")
    headers, rows = bid_export.bid_list_rows([bid])
    assert headers[0] == "Project" and rows[0][4] == "Won" and rows[0][9] == Decimal(300000)
    lines = [line("SH010 comp", days=3, rate=80000, shots=1, shot="SH010")]
    totals = DB.price_bid(lines, 20, tax=18)
    html = bid_export.bid_document_html(bid, lines, totals, studio="UT Studios")
    assert "₹3,54,000.00" in html and "GST (18%)" in html and "Cost" not in html
    assert "=cmd|bad" in html            # shown as text in a document, escaped HTML
