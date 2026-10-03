"""
IT Support on screen: the desk (IT) and My tickets (the requester), and the
ticket thread both open (IT-077 ... IT-133).
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from slate.core.domain import service_desk as sd


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.domain import people, access

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "desk.db"))
    assert manager.active_mode == "sqlite"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    for username, name, roles in (("ravi", "राहुल शर्मा", '["Artist"]'),
                                  ("it.sana", "Sana Shaikh", '["IT"]'),
                                  ("it.joe", "Joe D", '["IT"]')):
        manager.execute_update("INSERT INTO ut_users (username, display_name, roles) VALUES (%s, %s, %s)",
                               (username, name, roles))
    people.refresh()
    access.reset_cache()
    # Every hour counts in these tests, so they do not depend on the time of day.
    always = sd.BusinessCalendar(start=datetime.min.time(),
                                 end=datetime.max.time().replace(microsecond=0),
                                 days=frozenset(range(7)))
    monkeypatch.setattr(sd, "studio_calendar", lambda db=None: always)
    try:
        yield manager
    finally:
        people.refresh()
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


_KEEP = []


def _keep(widget):
    _KEEP.append(widget)
    return widget


def _ticket(db, who="ravi", text="Nuke will not start", status="Open", priority="P3",
            assigned=None, hours_ago=1, **extra):
    created = datetime.now().replace(microsecond=0) - timedelta(hours=hours_ago)
    db.execute_update(
        "INSERT INTO it_tickets (submitted_by, category, description, status, priority, impact, "
        "urgency, created_at, assigned_to) VALUES (%s, 'Workstation', %s, %s, %s, 'Low', 'Low', %s, %s)",
        (who, text, status, priority, created, assigned))
    ticket_id = db.execute_query("SELECT MAX(id) AS id FROM it_tickets", fetch="one")["id"]
    for column, value in extra.items():
        db.execute_update("UPDATE it_tickets SET %s = %%s WHERE id = %%s" % column, (value, ticket_id))
    return ticket_id


def _desk(db, user="it.sana"):
    from slate.gui.tabs.service_desk_view import ServiceDeskView
    view = _keep(ServiceDeskView(user, db_manager=db))
    view.show()
    return view


def _mine(db, user="ravi"):
    from slate.gui.tabs.my_tickets_view import MyTicketsView
    view = _keep(MyTicketsView(user, db_manager=db))
    view.show()
    return view


def _row_of(view, ticket_id):
    for r in range(view.table.rowCount()):
        if view.table.item(r, 0).text() == str(ticket_id):
            return r
    return -1


# ------------------------------------------------------------------- the desk

def test_a_ticket_with_no_description_opens(db, app):
    """IT-077: '' raised IndexError and the thread never opened."""
    from slate.gui.tabs.my_tickets_view import TicketThreadDialog
    ticket_id = _ticket(db, text="")
    view = _desk(db)
    row = view._all[0]
    dialog = _keep(TicketThreadDialog(row, db, "it.sana", side="it"))
    assert dialog.heading.text() == "Untitled ticket"
    assert view.table.item(_row_of(view, ticket_id), 1).text() == "Untitled ticket"


def test_search_finds_a_number_and_a_display_name(db, app):
    """IT-091."""
    first = _ticket(db, text="Mouse broken")
    _ticket(db, who="it.joe", text="Printer")
    view = _desk(db)
    view.search.setText("#%d" % first)
    view._apply_filters()
    assert [r["id"] for r in view._rows] == [first]
    view.search.setText("राहुल")
    view._apply_filters()
    assert [r["id"] for r in view._rows] == [first]


def test_typing_does_not_read_the_database(db, app, monkeypatch):
    """IT-093: every keystroke used to re-query and rebuild the cards."""
    _ticket(db)
    view = _desk(db)
    calls = []
    real = view.repo.all
    monkeypatch.setattr(view.repo, "all", lambda: calls.append(1) or real())
    for text in ("n", "nu", "nuk", "nuke"):
        view.search.setText(text)
    view._search_timer.timeout.emit()
    assert calls == []
    assert len(view._rows) == 1


def test_cards_say_unresolved_and_dim_mine_at_zero(db, app):
    """IT-094, IT-117."""
    from slate.core.infra.gate import Gate
    _ticket(db)
    view = _desk(db)
    assert view.card_open._label.text() == "UNRESOLVED"
    assert view.card_mine.value_text() == "0"
    assert Gate.IDLE in view.card_mine._strip.styleSheet()


def test_clicking_unassigned_shows_only_unassigned(db, app):
    """IT-105."""
    free = _ticket(db, text="free")
    _ticket(db, text="taken", assigned="it.joe", status="In Progress")
    view = _desk(db)
    view.card_unassigned.clicked.emit()
    assert [r["id"] for r in view._rows] == [free]
    view.filter_priority.setCurrentIndex(view.filter_priority.findData("P1"))
    assert view._rows == []
    assert view.empty.is_filtered()


def test_the_desk_says_waiting_on_requester(db, app):
    """IT-095."""
    ticket_id = _ticket(db, status="Waiting on You", waiting_since=datetime.now())
    view = _desk(db)
    view.filter_status.setCurrentIndex(view.filter_status.findData("all"))
    r = _row_of(view, ticket_id)
    assert view.table.item(r, 5).text() == "Waiting on requester"
    assert view.table.item(r, 7).text() == "Paused - waiting on requester"     # IT-087
    mine = _mine(db)
    assert mine.table.item(_row_of(mine, ticket_id), 4).text() == "Waiting on you"


def test_a_long_summary_stays_on_one_line_with_a_tooltip(db, app):
    """IT-108."""
    text = "A" * 150 + "\nmore detail"
    ticket_id = _ticket(db, text=text)
    view = _desk(db)
    item = view.table.item(_row_of(view, ticket_id), 1)
    assert not view.table.wordWrap()
    assert item.toolTip().startswith("A" * 150)


def test_assign_to_me_asks_before_taking_a_colleagues_ticket(db, app, monkeypatch):
    """IT-081."""
    from slate.gui.components import feedback
    ticket_id = _ticket(db, assigned="it.joe", status="In Progress")
    view = _desk(db)
    view.table.selectRow(_row_of(view, ticket_id))
    asked = []
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: asked.append(a) or False)
    view.take()
    assert asked
    assert view.repo.get(ticket_id)["assigned_to"] == "it.joe"
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    view.table.selectRow(_row_of(view, ticket_id))
    view.take()
    assert view.repo.get(ticket_id)["assigned_to"] == "it.sana"


def test_set_status_opens_on_the_current_status_and_needs_a_note(db, app):
    """IT-084, IT-086."""
    from slate.gui.tabs.service_desk_view import SetStatusDialog
    dialog = _keep(SetStatusDialog([{"status": "In Progress"}]))
    assert dialog.status.currentData() == "In Progress"
    dialog.status.setCurrentIndex(dialog.status.findData("Resolved"))
    assert not dialog.ok_btn.isEnabled()
    dialog.note.setPlainText("Replaced the cable")
    assert dialog.ok_btn.isEnabled()


def test_resolved_tickets_show_whether_the_promise_was_kept(db, app):
    """IT-111."""
    ticket_id = _ticket(db, status="Resolved", resolution_met=1, resolution_hours=2.5)
    view = _desk(db)
    view.filter_status.setCurrentIndex(view.filter_status.findData("all"))
    assert view.table.item(_row_of(view, ticket_id), 7).text() == "Kept"


def test_sla_report_lists_the_month(db, app):
    from slate.gui.tabs.service_desk_view import SlaReportDialog
    now = datetime.now()
    tickets = [{"priority": "P2", "resolved_at": now, "response_met": 1, "resolution_met": 0,
                "resolution_hours": 9}]
    dialog = _keep(SlaReportDialog(tickets))
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 3).text() == "0%"


def test_it_can_log_a_ticket_for_somebody(db, app, monkeypatch):
    """IT-078."""
    from slate.gui.tabs import service_desk_view as module
    from slate.gui.components import feedback

    class Filled(module.RaiseTicketDialog):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.person.set_username("ravi")
            self.category.setCurrentIndex(self.category.findData("Network"))
            self.summary.setText("No network at desk 4")

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "RaiseTicketDialog", Filled)
    toasts = []
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: toasts.append(a[1]))
    view = _desk(db)
    view.new_ticket()
    row = view.repo.all()[0]
    assert row["submitted_by"] == "ravi" and row.get("raised_by") == "it.sana"
    assert toasts and "#%s" % row["id"] in toasts[0]


# --------------------------------------------------------------- the thread

def test_the_thread_is_worded_for_its_side(db, app):
    """IT-096, IT-113."""
    from slate.core.infra.gate import Gate
    from slate.gui.tabs.my_tickets_view import TicketThreadDialog
    ticket_id = _ticket(db, priority="P2", status="Closed")
    view = _desk(db)
    row = view.repo.get(ticket_id)
    dialog = _keep(TicketThreadDialog(row, db, "it.sana", side="it"))
    assert dialog.reply.placeholderText().startswith("Reply to") or not dialog.reply.isEnabled()
    meta = dialog.meta.text()
    assert Gate.WARN in meta and Gate.OK in meta          # priority and status, each its own colour
    # IT-098: a closed ticket is reopened, not replied to.
    assert not dialog.btn_send.isEnabled() and dialog.btn_reopen.isVisibleTo(dialog)
    open_id = _ticket(db)
    dialog = _keep(TicketThreadDialog(view.repo.get(open_id), db, "it.sana", side="it"))
    assert dialog.reply.placeholderText() == "Reply to राहुल शर्मा"


def test_a_long_token_does_not_widen_the_thread(db, app):
    """IT-099, IT-100, IT-101."""
    from slate.gui.tabs.my_tickets_view import TicketThreadDialog
    from slate.core.infra.ticket_repository import TicketRepository
    ticket_id = _ticket(db)
    repo = TicketRepository(db)
    repo.reply({"id": ticket_id}, "it.sana", "x" * 600)
    for n in range(30):
        repo.reply({"id": ticket_id}, "ravi", "Update %d" % n)
    dialog = _keep(TicketThreadDialog(repo.get(ticket_id), db, "it.sana", side="it"))
    dialog.show()
    app.processEvents()
    dialog._scroll_to_end()
    assert dialog.thread_area.horizontalScrollBar().maximum() == 0
    bar = dialog.thread_area.verticalScrollBar()
    assert bar.value() == bar.maximum() > 0
    assert dialog.width() >= 700 or dialog.width() >= int(0.85 * dialog.screen().availableGeometry().width())
    dialog.close()


def test_the_open_thread_shows_new_replies_and_ctrl_enter_sends(db, app):
    """IT-102."""
    from PySide6.QtTest import QTest
    from slate.gui.tabs.my_tickets_view import TicketThreadDialog
    from slate.core.infra.ticket_repository import TicketRepository
    ticket_id = _ticket(db)
    repo = TicketRepository(db)
    dialog = _keep(TicketThreadDialog(repo.get(ticket_id), db, "ravi", side="requester"))
    repo.reply({"id": ticket_id}, "it.sana", "Try restarting the licence server")
    dialog._poll()
    texts = [c["comment_text"] for c in repo.comments(ticket_id)]
    assert dialog.thread_layout.count() >= len(texts)
    dialog.show()
    dialog.reply.setPlainText("Still the same")
    dialog.reply.setFocus()
    QTest.keyClick(dialog.reply, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert any(c["comment_text"] == "Still the same" for c in repo.comments(ticket_id))
    dialog.close()


def test_the_requester_can_confirm_a_fix(db, app):
    """IT-120."""
    from slate.gui.tabs.my_tickets_view import TicketThreadDialog
    from slate.core.infra.ticket_repository import TicketRepository
    repo = TicketRepository(db)
    ticket_id = _ticket(db)
    repo.set_status({"id": ticket_id}, "Resolved", "it.sana", "Swapped the GPU")
    dialog = _keep(TicketThreadDialog(repo.get(ticket_id), db, "ravi", side="requester"))
    assert dialog.btn_fixed.isVisibleTo(dialog) and dialog.btn_reopen.isVisibleTo(dialog)
    dialog._confirm_fixed()
    assert repo.get(ticket_id)["status"] == "Closed"


# ---------------------------------------------------------------- My tickets

def test_my_tickets_reads_priorities_in_words_and_hides_closed(db, app):
    """IT-122, IT-125, IT-126."""
    legacy = _ticket(db, priority="Medium", text="Old one")
    closed = _ticket(db, status="Closed", text="Done")
    waiting = _ticket(db, status="Waiting on You", text="Need your log")
    view = _mine(db)
    assert _row_of(view, closed) == -1
    assert view.table.item(_row_of(view, legacy), 3).text() == "P3 Medium"
    assert _row_of(view, waiting) == 0
    assert view.banner.isVisibleTo(view) and "#%d" % waiting in view.banner_text.text()
    view.include_closed.setChecked(True)
    assert _row_of(view, closed) >= 0


def test_enter_opens_the_selected_ticket(db, app, monkeypatch):
    """IT-124."""
    from slate.gui.tabs import my_tickets_view as module
    ticket_id = _ticket(db)
    view = _mine(db)
    opened = []
    monkeypatch.setattr(view, "_open", lambda row: opened.append(row["id"]))
    view.table.selectRow(0)
    assert view.btn_open.isEnabled()
    view.table.activated.emit(view.table.model().index(0, 1))
    assert opened == [ticket_id]


def test_report_a_problem_needs_a_category_and_a_summary(db, app):
    """IT-129, IT-130, IT-133."""
    from slate.gui.tabs.my_tickets_view import RaiseTicketDialog
    dialog = _keep(RaiseTicketDialog(username="ravi", machines_of=lambda u: ["WS-COMP-07"]))
    assert dialog.category.currentData() == ""
    assert not dialog.send_btn.isEnabled()
    dialog.summary.setText("Nuke crashes")
    assert not dialog.send_btn.isEnabled()
    dialog.category.setCurrentIndex(1)
    assert dialog.send_btn.isEnabled()
    assert dialog.summary.maxLength() == 120
    assert dialog.values()["machine"] == "WS-COMP-07"


def test_sending_says_the_number_and_selects_it(db, app, monkeypatch):
    """IT-119."""
    from slate.gui.tabs import my_tickets_view as module
    from slate.gui.components import feedback

    class Filled(module.RaiseTicketDialog):
        def __init__(self, *a, **k):
            k["machines_of"] = lambda u: []
            super().__init__(*a, **k)
            self.category.setCurrentIndex(1)
            self.summary.setText("Wacom pen dead")

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "RaiseTicketDialog", Filled)
    toasts = []
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: toasts.append(a[1]))
    view = _mine(db)
    view.raise_ticket()
    ticket_id = view._all[0]["id"]
    assert "Ticket #%d sent" % ticket_id in toasts[0]
    assert view._selected()["id"] == ticket_id


def test_it_staff_get_the_queue_and_their_own_tickets(db, app):
    """IT-078 / IT-158."""
    from types import SimpleNamespace
    from PySide6.QtWidgets import QTabWidget
    from slate.gui.components.main_window_builder import MainWindowBuilderMixin
    it = SimpleNamespace(user_roles=["IT"], allowed_tabs=["IT"], _current_username=lambda: "it.sana")
    built = _keep(MainWindowBuilderMixin._build_ticketing_tab(it))
    assert isinstance(built, QTabWidget)
    assert [built.tabText(i) for i in range(built.count())] == ["Queue", "My tickets"]
    artist = SimpleNamespace(user_roles=["Artist"], allowed_tabs=["IT"], _current_username=lambda: "ravi")
    from slate.gui.tabs.my_tickets_view import MyTicketsView
    assert isinstance(_keep(MainWindowBuilderMixin._build_ticketing_tab(artist)), MyTicketsView)


def test_clearing_filters_leaves_a_plain_empty_search(db, app):
    """NEW-it-3, IT-131, NEW-it-4."""
    _ticket(db)
    view = _desk(db)
    view.search.setText("zzz")
    view._apply_filters()
    view.clear_filters()
    assert view.search.text() == "" and len(view._rows) == 1
    assert not view.search.signalsBlocked()
    mine = _mine(db)
    assert mine.table.maximumHeight() == 16777215
    assert mine.layout().stretch(mine.layout().indexOf(mine.table)) == 1
    from slate.gui.tabs.my_tickets_view import RaiseTicketDialog
    dialog = _keep(RaiseTicketDialog(username="ravi", machines_of=lambda u: []))
    assert dialog.detail.property("prose") is True


def test_hash_number_finds_exactly_that_ticket(db, app):
    """IT-091 (round 3): '#1' found tickets 10 and 11 as well."""
    ids = [_ticket(db, text="Ticket %d" % n) for n in range(12)]
    view = _desk(db)
    view.search.setText("#%d" % ids[0])
    view._apply_filters()
    assert [r["id"] for r in view._rows] == [ids[0]]
    view.search.setText(str(ids[0]))
    view._apply_filters()
    assert ids[0] in [r["id"] for r in view._rows]


# ------------------------------------------------------------------ round 2

def test_a_long_token_in_an_event_line_does_not_widen_the_thread(db, app):
    """IT2-001 (IT-099 back, for event lines): one text widget for both."""
    from slate.gui.tabs.my_tickets_view import TicketThreadDialog, _BubbleText
    from slate.core.infra.ticket_repository import TicketRepository
    ticket_id = _ticket(db)
    repo = TicketRepository(db)
    repo.change_priority({"id": ticket_id}, "P2", "\\\\fileserver01\\projects\\" + "x" * 170, "it.sana")
    repo.reply({"id": ticket_id}, "ravi", "after the event")
    dialog = _keep(TicketThreadDialog(repo.get(ticket_id), db, "it.sana", side="it"))
    dialog.show()
    app.processEvents()
    dialog._scroll_to_end()
    assert dialog.thread_area.horizontalScrollBar().maximum() == 0
    assert dialog.thread_holder.width() <= dialog.thread_area.viewport().width()
    lines = [dialog.thread_layout.itemAt(i).widget() for i in range(dialog.thread_layout.count())]
    assert any(isinstance(w, _BubbleText) and "fileserver01" in w.toPlainText() for w in lines)
    dialog.close()


def test_the_thread_has_done_and_it_actions(db, app, monkeypatch):
    """IT2-004 / IT2-005 / IT2-009."""
    from PySide6.QtWidgets import QPushButton
    from slate.gui.tabs import my_tickets_view as module
    from slate.gui.tabs import service_desk_view as desk
    from slate.core.infra.ticket_repository import TicketRepository
    ticket_id = _ticket(db, text="Nuke\nmore detail")
    repo = TicketRepository(db)
    dialog = _keep(module.TicketThreadDialog(repo.get(ticket_id), db, "it.sana", side="it"))
    labels = [b.text() for b in dialog.findChildren(QPushButton) if b.isVisibleTo(dialog)]
    assert "Done" in labels and "Close" not in labels
    assert dialog.btn_take.isVisibleTo(dialog) and dialog.btn_status.isVisibleTo(dialog)
    # The description toggle is on the meta line, above what it hides.
    layout = dialog.layout()
    meta_row = next(layout.itemAt(i).layout() for i in range(layout.count())
                    if layout.itemAt(i).layout() is not None
                    and layout.itemAt(i).layout().indexOf(dialog.meta) >= 0)
    assert meta_row.indexOf(dialog.toggle_body) >= 0
    dialog._take()
    assert repo.get(ticket_id)["assigned_to"] == "it.sana"
    assert not dialog.btn_take.isVisibleTo(dialog)

    class Chosen(desk.SetStatusDialog):
        def exec(self):
            self.status.setCurrentIndex(self.status.findData("Resolved"))
            self.note.setPlainText("Reinstalled")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(desk, "SetStatusDialog", Chosen)
    dialog._set_status()
    assert repo.get(ticket_id)["status"] == "Resolved"
    requester = _keep(module.TicketThreadDialog(repo.get(ticket_id), db, "ravi", side="requester"))
    assert not requester.btn_take.isVisibleTo(requester)
    assert "Still broken" in requester.reply.placeholderText()                  # IT2-002


def test_a_failed_withdraw_keeps_what_was_typed_and_reopen_asks_why(db, app, monkeypatch):
    """IT2-006 / IT2-007."""
    from PySide6.QtWidgets import QInputDialog
    from slate.gui.tabs import my_tickets_view as module
    from slate.gui.components import feedback
    from slate.core.infra.ticket_repository import TicketError, TicketRepository
    ticket_id = _ticket(db)
    repo = TicketRepository(db)
    dialog = _keep(module.TicketThreadDialog(repo.get(ticket_id), db, "ravi", side="requester"))
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(feedback, "warn", lambda *a, **k: None)

    def refuse(*_a, **_k):
        raise TicketError("refused")

    monkeypatch.setattr(dialog.repo, "withdraw", refuse)
    dialog.reply.setPlainText("found a spare")
    dialog._withdraw()
    assert dialog.reply.toPlainText() == "found a spare"

    repo.set_status({"id": ticket_id}, "Resolved", "it.sana", "done")
    dialog.ticket = repo.get(ticket_id)
    monkeypatch.setattr(QInputDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QInputDialog, "textValue", lambda self: "")
    dialog.reply.setPlainText("")
    dialog._reopen()                                    # nothing said: refused
    assert repo.get(ticket_id)["status"] == "Resolved"
    monkeypatch.setattr(QInputDialog, "textValue", lambda self: "Crashes again on save")
    dialog._reopen()
    assert repo.get(ticket_id)["status"] == "Open"
    assert repo.comments(ticket_id)[-1]["comment_text"] == "Crashes again on save"


def test_the_queue_names_the_requester_searches_what_it_shows_and_spares_closed_ones(db, app):
    """IT2-011 / IT2-021 / IT2-023 / IT2-024 / IT2-025 / IT2-028 / IT2-064."""
    from slate.core.infra.gate import Gate
    paused = _ticket(db, status="Waiting on You", waiting_since=datetime.now(), text="Tablet")
    closed = _ticket(db, status="Closed", text="Old", raised_by="it.joe")
    view = _desk(db)
    assert view.table.horizontalHeaderItem(2).text() == "Requester"
    assert view.filter_status.itemText(0) == "Unresolved"
    assert view.count_label.text() == "1 of 2 tickets"
    view.search.setText("paused")
    view._apply_filters()
    assert [r["id"] for r in view._rows] == [paused]
    view.search.setText("")
    view.filter_status.setCurrentIndex(view.filter_status.findData("all"))
    r = _row_of(view, closed)
    assert "Logged by Joe D" in view.table.item(r, 2).toolTip()
    assert view.table.item(r, 6).foreground().color().name() != Gate.WARN.lower()
    view.table.selectRow(r)
    assert view.btn_status.isEnabled() and not view.btn_take.isEnabled()
    assert not view.act_assign.isEnabled() and not view.act_responded.isEnabled()


def test_a_figure_filter_is_shown(db, app):
    """IT2-022."""
    _ticket(db, priority="P1", hours_ago=30)
    _ticket(db)
    view = _desk(db)
    view.card_breached.clicked.emit()
    assert view.card_chip.isVisibleTo(view) and "Breached" in view.card_chip.text()
    assert view.card_breached._selected
    view.clear_card()
    assert not view.card_chip.isVisibleTo(view) and not view.card_breached._selected
    assert len(view._rows) == 2


def test_change_priority_explains_and_says_why_change_is_off(db, app):
    """IT2-013 / IT2-020."""
    from slate.gui.tabs.service_desk_view import PriorityDialog
    dialog = _keep(PriorityDialog({"priority": "P4"}))
    assert dialog.ok_btn.toolTip() == "Choose a different priority first."
    dialog.priority.setCurrentIndex(0)
    assert dialog.ok_btn.toolTip() == "Give a reason first."
    assert "starts now" in dialog.hint.text()
    assert dialog.reason.maxLength() == 200


def test_my_tickets_keeps_a_header_sort_and_marks_new_replies(db, app, monkeypatch):
    """IT2-029 / IT2-031 / IT2-030."""
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.ticket_repository import TicketRepository
    monkeypatch.setitem(GlobalConfig._runtime_overrides, "IT_TICKETS_SEEN", {})
    first = _ticket(db, text="first")
    second = _ticket(db, text="second")
    TicketRepository(db).reply({"id": first}, "it.sana", "On it")
    view = _mine(db)
    assert view.table.horizontalHeaderItem(6).text() == "Last update"
    assert view.table.item(_row_of(view, first), 1).font().bold()
    assert not view.table.item(_row_of(view, second), 1).font().bold()
    view.table.sortItems(0, Qt.SortOrder.AscendingOrder)
    view.refresh()
    assert view.table.item(0, 0).text() == str(first)
    assert view.table.horizontalHeader().sortIndicatorSection() == 0
    assert view.header.indexOf(view.btn_open) < 0                 # in the header's action box
    assert view.btn_open.isVisibleTo(view)


def test_my_machine_can_be_left_off_the_ticket(db, app):
    """IT2-032."""
    from slate.gui.tabs.my_tickets_view import RaiseTicketDialog
    dialog = _keep(RaiseTicketDialog(username="ravi", machines_of=lambda u: ["WS-COMP-07"]))
    assert dialog.machine_note.isChecked()
    dialog.machine_note.setChecked(False)
    assert dialog.values()["machine"] == ""


def test_the_it_pages_switch_from_the_header(db, app):
    """IT2-026: no framed tab widget pushing the title down."""
    from types import SimpleNamespace
    from PySide6.QtWidgets import QTabBar
    from slate.gui.components.main_window_builder import MainWindowBuilderMixin
    it = SimpleNamespace(user_roles=["IT"], allowed_tabs=["IT"], _current_username=lambda: "it.sana")
    pages = _keep(MainWindowBuilderMixin._build_ticketing_tab(it))
    assert pages.documentMode() and not pages.tabBar().isVisibleTo(pages)
    queue_bar = pages.widget(0).findChild(QTabBar)
    queue_bar.setCurrentIndex(1)
    assert pages.currentIndex() == 1
    assert pages.widget(1).findChild(QTabBar).currentIndex() == 1
