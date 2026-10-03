"""
Licences: findings, readings, costs, the licence-server import and renewal
reminders (IT-047 ... IT-075).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from slate.core.domain import licence_compliance as lc
from slate.core.domain import licence_server_report as report


def _in(days):
    return date.today() + timedelta(days=days)


# ------------------------------------------------------------------ findings

def test_an_over_subscribed_licence_still_counts_as_a_renewal():
    """IT-054."""
    text = lc.describe(10, 12, _in(30))
    assert lc.state(10, 12, _in(30)) == lc.OVER
    assert "Renews in 30 days" in text
    assert lc.is_renewal_due(30) and lc.is_renewal_due(-3) and not lc.is_renewal_due(200)


def test_no_advice_to_cut_seats_at_92_percent():
    """IT-055."""
    assert "could" not in lc.describe(12, 11, _in(20)) and "sized about right" in lc.describe(12, 11, _in(20))
    assert "renewing 6 seats" in lc.describe(20, 5, _in(20))


def test_day_and_seat_wording():
    """IT-060."""
    assert [lc.renewal_phrase(d) for d in (0, 1, 31, -1, -10, None)] == [
        "Renews today", "Renews tomorrow", "Renews in 31 days", "Expired yesterday",
        "Expired 10 days ago", "No expiry"]
    assert lc.plural(1, "seat") == "1 seat" and lc.plural(3, "seat") == "3 seats"
    assert "(s)" not in lc.describe(5, 1, _in(300))


def test_the_cost_of_spare_seats():
    """IT-063."""
    assert lc.spare_cost("100000", 10, 6) == Decimal("40000.00")
    assert lc.spare_cost(None, 10, 6) is None
    assert "about ₹40,000.00 a year" in lc.describe(10, 4, _in(300), spare_cost_text="₹40,000.00")


# -------------------------------------------------------------------- parser

LMSTAT = """
lmstat - Copyright (c) 1989-2019 Flexera.
Users of nuke_i:  (Total of 10 licenses issued;  Total of 3 licenses in use)
  "nuke_i" v2023.0910, vendor: foundry
    rahul ws-comp-01 ws-comp-01:0 (v2023.0910) (licsrv/4101 101), start Thu 1/10 9:30
Users of nuke_r:  (Total of 20 licenses issued;  Total of 0 licenses in use)
Users of houdini:  (Total of 4 licenses issued;  Total of 4 licenses in use)
"""

RLMSTAT = """
rlmstat v12.4
	ISV servers:
	   Name           port Running Restarts
	   sesinetd       5053   Yes       0
	------------------------
	sesinetd license usage status on licsrv (port 5053)
	houdini_fx v19.0
		count: 6, # reservations: 0, inuse: 5, exp: 31-dec-2026
	katana v6.0 count: 2, # reservations: 0, inuse: 1, exp: permanent
"""


def test_lmstat_and_rlmstat_reports_are_read():
    """IT-062."""
    flex = report.parse(LMSTAT)
    assert [(r.product, r.in_use, r.total) for r in flex] == [
        ("nuke_i", 3, 10), ("nuke_r", 0, 20), ("houdini", 4, 4)]
    rlm = report.parse(RLMSTAT)
    assert [(r.product, r.in_use, r.total) for r in rlm] == [("houdini_fx", 5, 6), ("katana", 1, 2)]


def test_products_are_matched_to_licences_only_when_clear():
    nuke = {"id": 1, "software_name": "Nuke"}
    houdini = {"id": 2, "software_name": "Houdini FX"}
    assert report.match_licence("nuke_i", [nuke, houdini]) is nuke
    assert report.match_licence("houdini_fx", [nuke, houdini]) is houdini
    assert report.match_licence("nuke_i", [nuke, dict(nuke, id=3)]) is None   # two contracts
    assert report.match_licence("maya", [nuke]) is None


# --------------------------------------------------------------- repository

@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "lic.db"))
    assert manager.active_mode == "sqlite"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request):
    db = request.getfixturevalue("sqlite_db" if request.param == "sqlite" else "pg_db")
    from slate.core.infra.licence_repository import LicenceRepository
    from slate.core.infra.studio_settings import StudioSettings
    StudioSettings.invalidate()
    return LicenceRepository(db)


def _by_id(repo):
    return {r["id"]: r for r in repo.compliance(90)}


def _ids(repo):
    return [r["id"] for r in repo.licences()]


def test_a_second_contract_does_not_inherit_the_first_ones_usage(repo):
    """IT-047 / IT-069."""
    repo.save("Nuke", 20, _in(200), contract_ref="Studio")
    repo.save("Nuke", 5, _in(200), contract_ref="Project KLC")
    studio, project = sorted(repo.licences(), key=lambda r: -r["total_seats"])
    repo.record("Nuke", 22, 20, licence_id=studio["id"])
    repo.record("Nuke", 30, 20, licence_id=None)                     # an old, name-only reading
    rows = _by_id(repo)
    assert rows[studio["id"]]["peak"] == 22                          # own readings only
    assert rows[project["id"]]["peak"] is None
    assert rows[project["id"]]["state"] == lc.UNKNOWN


def test_name_only_readings_count_for_the_only_licence_of_that_name(repo):
    repo.save("Maya", 30, _in(200))
    maya = repo.licences()[0]
    repo.record("maya", 14, 30, licence_id=None)                     # case-insensitive
    repo.record("Maya", 5, 30, licence_id=maya["id"])
    assert _by_id(repo)[maya["id"]]["peak"] == 14                    # max of own and legacy


def test_removing_takes_name_only_readings_too_and_counts_them_honestly(repo):
    """IT-048, IT-068."""
    repo.save("Maya", 30, _in(200))
    maya = repo.licences()[0]
    repo.record("Maya", 14, 30, licence_id=None)
    repo.record("Maya", 15, 30, licence_id=None)
    repo.record("Maya", 5, 30, licence_id=maya["id"])
    assert repo.reading_count(maya) == 3
    assert repo.remove(maya["id"])
    repo.save("Maya", 30, _in(200))
    assert repo.compliance(90)[0]["peak"] is None


def test_name_only_readings_stay_when_another_contract_has_the_name(repo):
    repo.save("Maya", 30, _in(200), contract_ref="A")
    repo.save("Maya", 10, _in(200), contract_ref="B")
    first = repo.licences()[0]
    repo.record("Maya", 14, 30, licence_id=None)
    repo.remove(first["id"])
    remaining = repo.licences()[0]
    assert _by_id(repo)[remaining["id"]]["peak"] == 14


def test_a_wrong_reading_can_be_corrected_or_deleted(repo):
    """IT-051."""
    repo.save("NukeX", 8, _in(200))
    lic = repo.licences()[0]
    repo.record("NukeX", 2, 8, taken_at=datetime.now() - timedelta(days=2), licence_id=lic["id"])
    repo.record("NukeX", 25, 8, licence_id=lic["id"])
    assert _by_id(repo)[lic["id"]]["state"] == lc.OVER
    typo = repo.history(lic)[0]
    assert typo["seats_in_use"] == 25
    assert repo.update_reading(typo["id"], 3)
    assert _by_id(repo)[lic["id"]]["peak"] == 3
    assert repo.licences()[0]["active_seats"] == 3
    assert repo.delete_reading(typo["id"])
    assert _by_id(repo)[lic["id"]]["peak"] == 2
    assert repo.licences()[0]["active_seats"] == 2


def test_back_filled_readings_keep_their_time_and_the_future_is_refused(repo):
    """IT-062."""
    repo.save("Houdini", 6, _in(200))
    lic = repo.licences()[0]
    friday = (datetime.now() - timedelta(days=3)).replace(hour=16, minute=0, second=0, microsecond=0)
    assert repo.record("Houdini", 6, 6, taken_at=friday, licence_id=lic["id"], recorded_by="it.sana")
    reading = repo.history(lic)[0]
    assert str(reading["taken_at"])[:16] == friday.strftime("%Y-%m-%d %H:%M")
    assert reading.get("recorded_by") == "it.sana"
    assert not repo.record("Houdini", 1, 6, taken_at=datetime.now() + timedelta(days=1),
                           licence_id=lic["id"])


def test_imported_readings_are_marked_as_such(repo):
    repo.save("Nuke", 10, _in(200))
    lic = repo.licences()[0]
    when = datetime.now().replace(microsecond=0) - timedelta(hours=1)
    assert repo.import_readings([(lic, 3)], taken_at=when, recorded_by="it.sana") == (1, 0)
    assert repo.history(lic)[0].get("source") == "server report"
    # IT2-057: the same report again is skipped, not doubled.
    assert repo.import_readings([(lic, 3)], taken_at=when, recorded_by="it.sana") == (0, 1)
    assert len(repo.history(lic)) == 1


def test_cost_vendor_contract_and_no_expiry_round_trip(repo):
    """IT-057, IT-063."""
    assert repo.save("Resolve Studio", 4, None, annual_cost=Decimal("120000"), currency="INR",
                     vendor="Blackmagic via Prime", contract_ref="PO-1182", notes="Dongles")
    row = repo.licences()[0]
    assert row["expiration_date"] is None
    assert Decimal(str(row["annual_cost"])) == Decimal("120000")
    assert (row["vendor"], row["contract_ref"], row["currency"]) == ("Blackmagic via Prime", "PO-1182", "INR")
    repo.record("Resolve Studio", 1, 4, licence_id=row["id"])
    finding = repo.compliance(90)[0]
    assert finding["state"] == lc.UNDER and finding["spare_cost"] == Decimal("90000.00")
    assert "₹90,000 a year" in finding["finding"]                      # IT2-061: no '.00'


def test_same_name_is_found_ignoring_case(repo):
    """IT-056."""
    repo.save("Nuke", 10, _in(200))
    assert repo.same_name("nuke")
    assert not repo.same_name("Nuke", exclude_id=repo.licences()[0]["id"])


def test_renewal_reminders_go_once_per_threshold(repo):
    """IT-067."""
    db = repo.db
    db.execute_update("INSERT INTO ut_users (username, display_name, roles) VALUES (%s, %s, %s)",
                      ("it.sana", "Sana", '["IT"]'))
    from slate.core.domain import people, access
    people.refresh()
    access.reset_cache()
    repo.save("Silhouette", 4, _in(40))
    repo.save("Mocha", 2, _in(400))
    today = date.today()
    assert repo.send_renewal_reminders(today=today) == 1
    assert repo.send_renewal_reminders(today=today) == 0                 # not again
    assert repo.send_renewal_reminders(today=today + timedelta(days=27)) == 1   # 13 days left
    notes = db.execute_query("SELECT message FROM notifications", fetch="all") or []
    assert any("Silhouette" in dict(n)["message"] for n in notes)
    people.refresh()


def test_the_legacy_licence_table_is_copied_once_without_its_key(repo):
    """IT-075: rows of it_licenses appear on the Licences screen; the key is not copied."""
    from slate.core.infra.migrations import it_schema
    db = repo.db
    db.execute_update("INSERT INTO it_licenses (software_name, license_key, seats_total, seats_used, "
                      "expiry_date) VALUES (%s, %s, %s, %s, %s)",
                      ("Old Tool", "SECRET-KEY-123", 3, 1, "2027-01-31"))
    it_schema.adopt_legacy_licences(db)
    it_schema.adopt_legacy_licences(db)
    rows = [r for r in repo.licences() if r["software_name"] == "Old Tool"]
    assert len(rows) == 1
    assert rows[0]["total_seats"] == 3 and str(rows[0]["expiration_date"])[:10] == "2027-01-31"
    assert "SECRET" not in repr(rows[0])


def test_two_contracts_are_told_apart_by_the_seats_the_server_issued():
    """IT-062 (round 3): lmstat says 20 issued; only one Nuke contract has 20 seats."""
    studio = {"id": 1, "software_name": "Nuke", "total_seats": 20}
    project = {"id": 2, "software_name": "Nuke", "total_seats": 5}
    assert report.match_licence("nuke_i", [studio, project], total=20) is studio
    assert report.match_licence("nuke_i", [studio, project], total=7) is None
    assert report.match_licence("nuke_i", [studio, dict(studio, id=3)], total=20) is None



# ------------------------------------------------------------------ round 2

def test_a_licence_measured_before_the_window_is_not_called_never_measured(repo):
    """IT2-053."""
    repo.save("NukeX", 8, _in(300))
    lic = repo.licences()[0]
    repo.record("NukeX", 4, 8, taken_at=datetime.now() - timedelta(days=50), licence_id=lic["id"])
    [row] = repo.compliance(30)
    assert row["state"] == lc.STALE and "50 days ago" in row["finding"]
    assert "No usage has been recorded" not in row["finding"]
    assert repo.compliance(90)[0]["peak"] == 4


def test_renaming_a_licence_takes_its_old_readings_along(repo):
    """IT2-058."""
    repo.save("Nuke", 10, _in(300))
    lic = repo.licences()[0]
    repo.record("Nuke", 6, 10, licence_id=None)                    # name-only, from before ids
    assert repo.save("Nuke Studio", 10, _in(300), lic["id"])
    assert repo.compliance(90)[0]["peak"] == 6
    repo.save("Nuke", 4, _in(300))                                  # a new licence with the old name
    fresh = next(r for r in repo.compliance(90) if r["software_name"] == "Nuke")
    assert fresh["peak"] is None


def test_renewal_reminders_reach_approvers_and_read_well(repo):
    """IT2-056 / IT2-059."""
    from datetime import date
    from slate.core.domain import people, access
    db = repo.db
    for username, roles in (("it.sana", '["IT"]'), ("prod.head", '["Production Head"]')):
        db.execute_update("INSERT INTO ut_users (username, display_name, roles) VALUES (%s, %s, %s)",
                          (username, username, roles))
    people.refresh()
    access.reset_cache()
    repo.save("Houdini FX", 5, date.today() - timedelta(days=12))
    assert repo.send_renewal_reminders(today=date.today()) == 1
    rows = db.execute_query("SELECT user_id, message FROM notifications", fetch="all") or []
    got = {dict(r)["user_id"]: dict(r)["message"] for r in rows}
    assert "prod.head" in got and "it.sana" in got
    assert got["it.sana"] == "Houdini FX expired 12 days ago - renew or remove it."
