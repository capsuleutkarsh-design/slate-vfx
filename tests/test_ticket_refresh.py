"""
The IT queue shows tickets raised on other workstations without a restart.

Tabs are built once and kept. The queue read the database only when it was
built, so a ticket raised elsewhere appeared only after Slate was restarted.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "tickets.db"))
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


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


_KEEP = []   # views live to the end: tearing them down trips a known EmptyState teardown warning


def _view(cls, *args, **kwargs):
    view = cls(*args, **kwargs)
    _KEEP.append(view)
    return view


def _raise_ticket(db, who, text):
    """What another workstation does when somebody reports a problem."""
    assert db.execute_update(
        "INSERT INTO it_tickets (submitted_by, category, description, status, priority, impact, urgency) "
        "VALUES (%s, 'Workstation', %s, 'Open', 'P3', 'Low', 'Low')", (who, text))


def _tick(view):
    view._auto_refresh._last = 0          # as if the 30 seconds had passed
    view._auto_refresh._tick()


def test_the_queue_shows_a_ticket_raised_elsewhere(db, app):
    from slate.gui.tabs.service_desk_view import ServiceDeskView
    view = _view(ServiceDeskView, "it1", db_manager=db)
    view.show()
    assert view.table.rowCount() == 0

    _raise_ticket(db, "ravi", "Nuke will not start")
    _tick(view)
    assert view.table.rowCount() == 1
    assert "Nuke will not start" in view.table.item(0, 1).text()


def test_a_refresh_keeps_the_ticket_it_selected(db, app):
    from slate.gui.tabs.service_desk_view import ServiceDeskView
    _raise_ticket(db, "ravi", "First")
    _raise_ticket(db, "sana", "Second")
    view = _view(ServiceDeskView, "it1", db_manager=db)
    view.show()
    view.table.selectRow(1)
    chosen = view._selected()[0]["id"]

    _raise_ticket(db, "mira", "Third")
    _tick(view)
    assert [r["id"] for r in view._selected()] == [chosen]
    assert view.btn_take.isEnabled()


def test_the_raisers_list_shows_it_picking_the_ticket_up(db, app):
    from slate.gui.tabs.my_tickets_view import MyTicketsView
    _raise_ticket(db, "ravi", "Licence missing")
    view = _view(MyTicketsView, "ravi", db_manager=db)
    view.show()
    assert view.table.item(0, 5).text() == "Not yet picked up"

    db.execute_update("UPDATE it_tickets SET assigned_to=%s", ("it1",))
    _tick(view)
    assert view.table.item(0, 5).text() == "it1"


def test_nothing_is_read_while_hidden(db, app):
    from slate.gui.tabs.service_desk_view import ServiceDeskView
    view = _view(ServiceDeskView, "it1", db_manager=db)          # never shown
    _raise_ticket(db, "ravi", "Hidden")
    _tick(view)
    assert view.table.rowCount() == 0
