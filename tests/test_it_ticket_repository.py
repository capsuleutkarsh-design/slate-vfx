"""
The ticket actions both IT Support screens use, on SQLite and PostgreSQL.

Every action writes the ticket, the line in its conversation and the
notification together (IT-079/080/084/085/097/098/106/107/111/120).
"""

from datetime import datetime, timedelta

import pytest

from slate.core.domain import service_desk as sd
from slate.core.infra.ticket_repository import TicketError, TicketRepository


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "tickets.db"))
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
def db(request):
    manager = request.getfixturevalue("sqlite_db" if request.param == "sqlite" else "pg_db")
    from slate.core.domain import people, access
    people.refresh()
    access.reset_cache()
    for username, name, roles in (("ravi", "Ravi Kumar", '["Artist"]'),
                                  ("it.sana", "Sana Shaikh", '["IT"]'),
                                  ("it.joe", "Joe D", '["IT Support"]')):
        manager.execute_update(
            "INSERT INTO ut_users (username, display_name, roles) VALUES (%s, %s, %s)",
            (username, name, roles))
    people.refresh()
    yield manager
    people.refresh()


@pytest.fixture
def repo(db):
    return TicketRepository(db, calendar=sd.BusinessCalendar(days=frozenset(range(7)),
                                                             start=datetime.min.time(),
                                                             end=datetime.max.time().replace(microsecond=0)))


def _notes(db, who):
    rows = db.execute_query("SELECT message FROM notifications WHERE LOWER(user_id) = LOWER(%s)",
                            (who,), fetch="all") or []
    return [dict(r)["message"] for r in rows]


def _raise(repo, priority_answers=("Low", "Low"), who="ravi", summary="Nuke will not start"):
    return repo.raise_ticket(who, "Software / Licence", summary, "since this morning",
                             *priority_answers)


def test_raising_returns_the_number_and_stores_a_p_code(repo, db):
    ticket_id = _raise(repo)
    ticket = repo.get(ticket_id)
    assert ticket["priority"] == "P4" and ticket["status"] == "Open"
    assert sd.summary_of(ticket["description"]) == "Nuke will not start"


def test_a_p1_tells_it_and_a_p4_does_not(repo, db):
    """IT-107."""
    _raise(repo)
    assert _notes(db, "it.sana") == []
    _raise(repo, ("High", "High"), summary="Server down")
    assert any("Server down" in m for m in _notes(db, "it.sana"))
    assert any("Server down" in m for m in _notes(db, "it.joe"))


def test_it_can_log_a_ticket_for_somebody_else(repo, db):
    """IT-078: a phone call is logged with who logged it."""
    ticket_id = repo.raise_ticket("ravi", "Network", "No network", "", "Low", "Low",
                                  raised_by="it.sana")
    ticket = repo.get(ticket_id)
    assert ticket["submitted_by"] == "ravi"
    assert ticket.get("raised_by") == "it.sana"
    assert any("on Ravi Kumar's behalf" in c["comment_text"] for c in repo.comments(ticket_id))
    assert _notes(db, "ravi")


def test_raising_refuses_an_empty_or_long_summary(repo):
    with pytest.raises(TicketError):
        repo.raise_ticket("ravi", "Network", "  ", "")
    with pytest.raises(TicketError):
        repo.raise_ticket("ravi", "Network", "x" * 121, "")


def test_resolving_needs_a_note_and_writes_it_in_the_thread(repo, db):
    """IT-084."""
    ticket = repo.get(_raise(repo))
    with pytest.raises(TicketError):
        repo.set_status(ticket, "Resolved", "it.sana", "")
    assert repo.set_status(ticket, "Resolved", "it.sana", "Reinstalled the licence")
    after = repo.get(ticket["id"])
    assert after["status"] == "Resolved" and after["resolved_at"] is not None
    assert sd._truthy(after.get("resolution_met"))                  # IT-111
    lines = [c["comment_text"] for c in repo.comments(ticket["id"])]
    assert any("Reinstalled the licence" in line for line in lines)
    assert any("resolved" in m for m in _notes(db, "ravi"))


def test_reopening_clears_resolved_at(repo):
    """IT-085."""
    ticket = repo.get(_raise(repo))
    repo.set_status(ticket, "Resolved", "it.sana", "done")
    repo.set_status(ticket, "Open", "it.sana")
    assert repo.get(ticket["id"])["resolved_at"] is None


def test_waiting_twice_is_a_no_op(repo):
    """IT-080."""
    ticket = repo.get(_raise(repo))
    assert repo.set_status(ticket, sd.WAITING, "it.sana")
    first = repo.get(ticket["id"])["waiting_since"]
    assert not repo.set_status(ticket, sd.WAITING, "it.sana")
    assert repo.get(ticket["id"])["waiting_since"] == first


def test_the_requesters_reply_puts_it_back_with_it(repo, db):
    """IT-079."""
    ticket = repo.get(_raise(repo))
    repo.assign(ticket, "it.sana", "it.sana")
    repo.set_status(ticket, sd.WAITING, "it.sana")
    repo.reply(ticket, "ravi", "Here is the log")
    after = repo.get(ticket["id"])
    assert after["status"] == "In Progress"
    assert after["waiting_since"] is None
    assert any("Ravi Kumar replied" in m for m in _notes(db, "it.sana"))


def test_an_it_reply_picks_the_ticket_up(repo, db):
    """IT-097."""
    ticket = repo.get(_raise(repo))
    repo.reply(ticket, "it.joe", "Looking now")
    after = repo.get(ticket["id"])
    assert after["assigned_to"] == "it.joe" and after["status"] == "In Progress"
    assert after["first_response_at"] is not None
    assert _notes(db, "ravi")


def test_a_reply_on_a_closed_ticket_reopens_it(repo):
    """IT-098."""
    ticket = repo.get(_raise(repo))
    repo.set_status(ticket, "Closed", "it.sana", "fixed")
    repo.reply(ticket, "ravi", "still broken")
    assert repo.get(ticket["id"])["status"] == "Open"


def test_internal_notes_never_reach_the_requester(repo):
    """IT-106."""
    ticket = repo.get(_raise(repo))
    repo.reply(ticket, "it.sana", "On it")
    repo.add_note(ticket, "it.sana", "Licence server is flaky, check rlm log")
    mine = [c["comment_text"] for c in repo.comments(ticket["id"])]
    theirs = [c["comment_text"] for c in repo.comments(ticket["id"], include_internal=True)]
    assert not any("flaky" in t for t in mine)
    assert any("flaky" in t for t in theirs)


def test_changing_priority_needs_a_reason_and_keeps_it(repo):
    """IT-106."""
    ticket = repo.get(_raise(repo))
    with pytest.raises(TicketError):
        repo.change_priority(ticket, "P2", "", "it.sana")
    assert repo.change_priority(ticket, "P2", "Blocks a delivery", "it.sana")
    assert repo.get(ticket["id"])["priority"] == "P2"
    assert any("Blocks a delivery" in c["comment_text"] for c in repo.comments(ticket["id"]))


def test_assign_to_another_and_unassign(repo, db):
    """IT-081."""
    ticket = repo.get(_raise(repo))
    repo.assign(ticket, "it.joe", "it.sana")
    assert repo.get(ticket["id"])["assigned_to"] == "it.joe"
    assert any("gave you ticket" in m for m in _notes(db, "it.joe"))
    repo.assign(ticket, None, "it.sana")
    assert repo.get(ticket["id"])["assigned_to"] is None


def test_requester_actions(repo):
    """IT-120: fixed, withdraw, reopen."""
    ticket = repo.get(_raise(repo))
    repo.set_status(ticket, "Resolved", "it.sana", "done")
    repo.reopen(ticket, "ravi", "still broken")
    assert repo.get(ticket["id"])["status"] == "Open"
    repo.set_status(ticket, "Resolved", "it.sana", "really done")
    repo.confirm_fixed(ticket, "ravi")
    assert repo.get(ticket["id"])["status"] == "Closed"
    other = repo.get(_raise(repo, summary="Mouse"))
    repo.withdraw(other, "ravi", "found a spare")
    assert repo.get(other["id"])["status"] == "Closed"
    assert any("Withdrawn" in c["comment_text"] for c in repo.comments(other["id"]))


def test_responded_by_phone_stops_the_response_clock_once(repo):
    """IT-104."""
    ticket = repo.get(_raise(repo))
    assert repo.mark_responded(ticket, "it.sana")
    assert repo.get(ticket["id"])["first_response_at"] is not None
    assert not repo.mark_responded(ticket, "it.sana")


def test_it_staff_are_the_people_with_manage_it(repo):
    assert repo.it_staff() == ["it.joe", "it.sana"]
