"""
The service desk queue, and what a licence renewal is decided on.

Four bugs, all of which made a screen lie about its own numbers.

A ticket set to "Waiting on You" was title-cased to "Waiting On You" before
being compared with the list of open statuses, matched nothing, and vanished
from the open filter, from the Open count and from its own status filter. It was
still in the database and still somebody's problem.

The response clock stopped only when somebody pressed a separate button, so
taking a ticket and answering it in writing both left it reading as unanswered -
and almost the whole queue showed as having breached.

The clock also ran while the ticket was parked waiting for the person who raised
it to reply, so IT breached a promise on time they never had.

And licence readings were matched to a purchase by product name, so two
contracts for the same product shared one peak and each was reported
over-subscribed on the other's usage.
"""

from datetime import date, datetime, timedelta

import pytest

from slate.core.domain import service_desk as sd


# ------------------------------------------------------------ the status bug

@pytest.mark.parametrize("stored", [
    "Waiting on You", "waiting on you", "WAITING ON YOU", "  Waiting On You  ",
])
def test_waiting_on_you_is_recognised_however_it_was_stored(stored):
    """
    The bug: .title() turned it into "Waiting On You", which is in no list, so
    the ticket dropped out of every filter that mattered.
    """
    assert sd.normalise_status(stored) == "Waiting on You"
    assert sd.is_open(stored), "a ticket waiting on somebody is still open"


def test_title_case_is_what_broke_it():
    """The bug, stated directly, so nobody reintroduces it."""
    assert "Waiting on You".title() == "Waiting On You"
    assert "Waiting On You" not in sd.OPEN_STATUSES
    assert sd.normalise_status("Waiting On You") in sd.OPEN_STATUSES


def test_every_status_survives_the_round_trip():
    for status in sd.STATUSES:
        assert sd.normalise_status(status) == status
        assert sd.normalise_status(status.lower()) == status


def test_resolved_and_closed_are_not_open():
    assert not sd.is_open("Resolved")
    assert not sd.is_open("Closed")
    assert sd.is_open("Open")
    assert sd.is_open("In Progress")


# ------------------------------------------------------------------ the clock

def _ticket(**fields):
    base = {
        "status": "Open",
        "priority": "P3",
        "created_at": datetime(2026, 9, 14, 9, 0),
        "first_response_at": None,
    }
    base.update(fields)
    return base


def test_an_unanswered_ticket_is_measured_against_the_response_promise():
    now = datetime(2026, 9, 14, 10, 0)          # an hour later, P3 promises two
    state = sd.sla_state(_ticket(), now)
    assert state["against"] == "response"
    assert 0.9 < state["hours_left"] < 1.1


def test_an_answered_ticket_is_measured_against_the_fix_promise():
    state = sd.sla_state(
        _ticket(first_response_at=datetime(2026, 9, 14, 9, 30)),
        datetime(2026, 9, 14, 10, 0))
    assert state["against"] == "resolution"


def test_time_spent_waiting_on_the_requester_does_not_count():
    """
    The bug: the resolution clock ran straight through a ticket parked on
    "Waiting on You", so a ticket waiting a week for an answer breached while
    the queue was doing exactly the right thing.
    """
    created = datetime(2026, 9, 14, 9, 0)
    now = created + timedelta(hours=10)          # P2 promises a fix in eight

    running = _ticket(priority="P2", status="In Progress", created_at=created,
                      first_response_at=created)
    assert sd.sla_state(running, now)["state"] == "breached"

    # Same ticket, but four of those hours were spent waiting on the requester.
    parked = _ticket(priority="P2", status="In Progress", created_at=created,
                     first_response_at=created, waiting_seconds=4 * 3600)
    state = sd.sla_state(parked, now)
    assert state["state"] != "breached"
    assert state["hours_left"] == pytest.approx(2.0, abs=0.1)


def test_a_ticket_parked_right_now_keeps_banking_waiting_time():
    created = datetime(2026, 9, 14, 9, 0)
    now = created + timedelta(hours=10)
    parked = _ticket(priority="P2", status="Waiting on You", created_at=created,
                     first_response_at=created,
                     waiting_since=created + timedelta(hours=6))
    assert sd.waiting_hours(parked, now) == pytest.approx(4.0, abs=0.1)


def test_waiting_time_on_a_ticket_nobody_parked_is_zero():
    assert sd.waiting_hours(_ticket(), datetime(2026, 9, 14, 10, 0)) == 0.0


def test_a_closed_ticket_has_no_clock():
    assert sd.sla_state(_ticket(status="Resolved"))["state"] == "closed"
    assert sd.sla_state(_ticket(status="Closed"))["state"] == "closed"


# ---------------------------------------------------------------- the licences

@pytest.fixture
def licences(tmp_path, monkeypatch):
    """A licence repository on a database of its own."""
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.licence_repository import LicenceRepository
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "lic.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"

    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield LicenceRepository(manager)
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


def _two_nuke_contracts(repo):
    repo.save("Nuke", 10, date(2027, 1, 1))
    repo.save("Nuke", 4, date(2027, 6, 1))
    rows = repo.licences()
    assert len(rows) == 2
    return rows[0], rows[1]


def test_two_contracts_for_one_product_keep_separate_peaks(licences):
    """
    The bug: readings were grouped by product name, so the studio licence and
    the project licence shared a peak and each was judged on the other's usage.
    """
    studio, project = _two_nuke_contracts(licences)

    licences.record("Nuke", 9, 10, licence_id=studio["id"])
    licences.record("Nuke", 2, 4, licence_id=project["id"])

    peaks = licences.peaks(90)
    assert peaks[studio["id"]]["peak"] == 9
    assert peaks[project["id"]]["peak"] == 2

    findings = {row["id"]: row for row in licences.compliance(90)}
    assert findings[studio["id"]]["peak"] == 9
    assert findings[project["id"]]["peak"] == 2


def test_readings_taken_before_the_licence_id_still_count(licences):
    """
    An existing studio's history has no licence id. It must keep counting, or
    the day this shipped every licence would report "no readings".
    """
    licences.save("Houdini", 6, date(2027, 1, 1))
    row = licences.licences()[0]

    # A reading as the old code wrote them: name only.
    licences.record("Houdini", 5, 6, licence_id=None)

    finding = licences.compliance(90)[0]
    assert finding["peak"] == 5
    assert finding["state"] != "No readings"


def test_removing_a_licence_takes_its_readings_with_it(licences):
    """
    Leaving readings behind would keep them counting through the name fallback,
    so a cancelled contract would go on flagging its replacement.
    """
    licences.save("Mocha", 3, date(2027, 1, 1))
    row = licences.licences()[0]
    licences.record("Mocha", 3, 3, licence_id=row["id"])

    assert licences.remove(row["id"])
    assert licences.licences() == []
    assert licences.peaks(90) == {}
