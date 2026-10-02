"""
The Scheduling tab on screen (offscreen Qt, a scratch SQLite database).

    PRD-001  switching project refills Depends On, without a TypeError
    PRD-009/010  read-only cells; Edit and Delete exist; double-click edits
    PRD-011  the Timeline draws a bar per milestone, a today line, weekends
    PRD-012  the People view has a lane per person
    PRD-015/022/059  the cards; overdue end dates are red
    PRD-017/043/062/065  the dialog checks as you type, focus on the name
    PRD-024/025  owner filter; archived projects behind a toggle
    PRD-034  an empty schedule says how to start
    PRD-036  without schedule_write the write buttons are not there
    PRD-046/047  the status dialog says what changes and starts on the current status
    PRD-051  the shift dialog previews what moves
    PRD-054  empty values are a dash, never 'None'
"""

import os
import sys
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from slate.core.domain import scheduling as DS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


ADMIN = {"username": "admin", "roles": ["Admin"]}
ARTIST = {"username": "artist", "roles": ["Artist"]}


@pytest.fixture
def seeded(mock_db):
    db = mock_db
    for code, name, active in (("AVTR3", "Avatar 3", 1), ("RRR_REDUX", "RRR Redux", 1),
                               ("OLDPROJ", "Old", 0)):
        db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, %s)",
                          (code, name, active))
    from slate.core.infra.schedule_repository import ScheduleRepository
    repo = ScheduleRepository(db)
    today = date.today()
    a = repo.add(DS.Milestone(project_code="AVTR3", name="Roto & prep", start=today - timedelta(30),
                              end=today - timedelta(10), status="In Progress"), by="admin")
    b = repo.add(DS.Milestone(project_code="AVTR3", name="Comp first pass", start=today - timedelta(9),
                              end=today + timedelta(20), depends_on_id=a, owner="priya"), by="admin")
    c = repo.add(DS.Milestone(project_code="RRR_REDUX", name="Review", start=today,
                              end=today + timedelta(5)), by="admin")
    db.execute_update("INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, "
                      "status) VALUES ('OLDPROJ', 'Archived one', '2025-01-01', '2025-01-02', "
                      "'Scheduled')")
    return {"db": db, "repo": repo, "a": a, "b": b, "c": c}


def make_tab(qtbot, user=ADMIN):
    from slate.gui.tabs.prod_scheduling_tab import ProdSchedulingTab
    tab = ProdSchedulingTab(user_data=user)
    qtbot.addWidget(tab)
    tab.resize(1400, 800)
    tab.show()
    return tab


def rows_by_name(tab):
    from slate.gui.tabs.prod_scheduling_tab import C_NAME
    return {tab.grid.item(r, C_NAME).text(): r for r in range(tab.grid.rowCount())}


def test_the_table_cards_and_cells(qtbot, app, seeded):
    from slate.gui.tabs import prod_scheduling_tab as T
    from slate.core.infra.gate import Gate
    from PySide6.QtWidgets import QAbstractItemView
    tab = make_tab(qtbot)
    names = rows_by_name(tab)
    assert set(names) == {"Roto & prep", "Comp first pass", "Review"}, "archived hidden"
    assert tab.grid.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert tab.card_overdue.value_text() == "1"
    assert tab.card_progress.value_text() == "0"            # the overdue one counts as overdue
    assert tab.card_scheduled.value_text() == "2"
    assert "(" not in tab.card_progress.value_text()
    overdue_end = tab.grid.item(names["Roto & prep"], T.C_END)
    assert overdue_end.foreground().color().name().lower() == Gate.BAD.lower()
    review = names["Review"]
    assert tab.grid.item(review, T.C_DEPENDS).text() == "—"
    assert tab.grid.item(review, T.C_OWNER).text() == "—"
    comp = names["Comp first pass"]
    assert tab.grid.item(comp, T.C_DEPENDS).text().startswith("Roto & prep (ends ")
    assert tab.grid.wordWrap() is False
    assert not tab.grid.verticalHeader().isVisible()

    tab.archived_box.setChecked(True)
    assert "Archived one" in rows_by_name(tab)


def test_PRD_036_without_schedule_write_it_is_read_only(qtbot, app, seeded):
    tab = make_tab(qtbot, ARTIST)
    assert not any(b.isVisible() for b in tab.write_buttons)
    assert tab.read_only_badge.isVisible()
    tab.add_milestone()                                 # does nothing, raises nothing


def test_PRD_060_buttons_follow_the_selection(qtbot, app, seeded):
    from slate.gui.components.table_tools import select_keys
    tab = make_tab(qtbot)
    assert not tab.edit_button.isEnabled() and not tab.delete_button.isEnabled()
    select_keys(tab.grid, [seeded["a"], seeded["b"]])
    assert tab.status_button.isEnabled() and tab.delete_button.isEnabled()
    assert not tab.edit_button.isEnabled() and not tab.shift_button.isEnabled()
    select_keys(tab.grid, [seeded["a"]])
    assert tab.edit_button.isEnabled() and tab.shift_button.isEnabled()


def test_PRD_024_filters_and_the_no_match_state(qtbot, app, seeded):
    tab = make_tab(qtbot)
    tab.owner_filter.setCurrentIndex(tab.owner_filter.findData("priya"))
    assert [m.name for m in tab.visible_milestones()] == ["Comp first pass"]
    tab.owner_filter.setCurrentIndex(0)
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("__overdue__"))
    assert [m.name for m in tab.visible_milestones()] == ["Roto & prep"]
    tab.toolbar.search.setText("nothing like this")
    tab.toolbar.filter.apply()
    assert tab.empty.is_filtered() and tab.empty.isVisible()
    tab.clear_filters()
    assert len(tab.visible_milestones()) == 3


def test_PRD_034_an_empty_schedule_says_how_to_start(qtbot, app, mock_db):
    tab = make_tab(qtbot)
    assert tab.grid.rowCount() == 0
    assert tab.empty.isVisible()
    assert "No milestones yet" in tab.empty._heading.text()


def test_PRD_001_PRD_017_PRD_065_the_dialog(qtbot, app, seeded):
    from slate.gui.tabs.milestone_dialog import MilestoneDialog
    repo = seeded["repo"]
    dialog = MilestoneDialog(None, repo=repo, milestones=repo.list(include_archived=True),
                             projects=repo.projects(), default_project="AVTR3", username="admin")
    qtbot.addWidget(dialog)
    dialog.show()
    app.processEvents()
    assert dialog.focusWidget() is dialog.ms_input
    assert dialog.ms_input.maxLength() == 120
    assert dialog.minimumWidth() >= 480
    assert dialog.proj_cb.currentText() == "AVTR3 – Avatar 3" and dialog.project_code() == "AVTR3"
    deps = [dialog.dep_cb.itemText(i) for i in range(dialog.dep_cb.count())]
    assert any(d.startswith("Comp first pass — ends ") for d in deps)
    assert dialog.start_input.calendarWidget().firstDayOfWeek() == Qt.DayOfWeek.Monday

    # Switching project refills the list (it raised a TypeError and kept AVTR3's).
    dialog.proj_cb.setCurrentIndex(dialog.proj_cb.findData("RRR_REDUX"))
    deps = [dialog.dep_cb.itemText(i) for i in range(dialog.dep_cb.count())]
    assert deps[0] == "None" and any(d.startswith("Review") for d in deps)
    assert not any(d.startswith("Comp first pass") for d in deps)

    # Empty name: Save off, dialog open.
    assert not dialog.save_button.isEnabled()
    dialog.ms_input.setText("Review")                       # duplicate in RRR_REDUX
    assert not dialog.save_button.isEnabled()
    assert "already exists" in dialog.name_hint.text()
    dialog.ms_input.setText("Grade")
    dialog.end_input.setDate(dialog.start_input.date().addDays(-3))
    assert dialog.end_input.date() >= dialog.start_input.date() or not dialog.save_button.isEnabled()
    dialog.end_input.setDate(dialog.start_input.date().addDays(3))
    assert dialog.save_button.isEnabled()
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert repo.get(dialog.saved_id).name == "Grade"


def test_PRD_062_clearing_the_dependency_puts_the_start_back(qtbot, app, seeded):
    from slate.gui.tabs.milestone_dialog import MilestoneDialog
    repo = seeded["repo"]
    dialog = MilestoneDialog(None, repo=repo, milestones=repo.list(include_archived=True),
                             projects=repo.projects(), default_project="AVTR3")
    qtbot.addWidget(dialog)
    before = dialog.start_input.date()
    index = next(i for i in range(dialog.dep_cb.count())
                 if dialog.dep_cb.itemData(i) == seeded["b"])
    dialog.dep_cb.setCurrentIndex(index)
    assert dialog.start_input.date() > before
    assert "Starts after \"Comp first pass\" ends" in dialog.dep_hint.text()
    dialog.dep_cb.setCurrentIndex(0)
    assert dialog.start_input.date() == before


def test_PRD_010_edit_from_the_tab_saves_and_reselects(qtbot, app, seeded, monkeypatch):
    from slate.gui.components.table_tools import select_keys, selected_keys
    from slate.gui.tabs import milestone_dialog
    tab = make_tab(qtbot)

    def run(dialog, *args, **kwargs):
        dialog.ms_input.setText("Comp first pass v2")
        dialog._save()
        return dialog.result()

    monkeypatch.setattr(milestone_dialog.MilestoneDialog, "exec", run)
    select_keys(tab.grid, [seeded["b"]])
    tab.edit_milestone()
    assert seeded["repo"].get(seeded["b"]).name == "Comp first pass v2"
    assert selected_keys(tab.grid) == [seeded["b"]]


def test_PRD_046_PRD_047_status_dialog(qtbot, app, seeded):
    from slate.gui.tabs.prod_scheduling_tab import StatusDialog
    repo = seeded["repo"]
    one = StatusDialog(None, [repo.get(seeded["a"])])
    qtbot.addWidget(one)
    assert one.status() == "In Progress"
    assert any(b.text() == "Cancel" for b in one.findChildren(type(one.ok_button)))
    mixed = StatusDialog(None, [repo.get(seeded["a"]), repo.get(seeded["c"])])
    qtbot.addWidget(mixed)
    assert mixed.status() == "" and not mixed.ok_button.isEnabled()


def test_PRD_070_status_change_has_undo(qtbot, app, seeded, monkeypatch):
    from slate.gui.components import feedback
    from slate.gui.components.table_tools import select_keys
    from slate.gui.tabs import prod_scheduling_tab as T
    tab = make_tab(qtbot)
    toasts = []
    monkeypatch.setattr(feedback, "toast", lambda parent, msg, level="info", action=None, **k:
                        toasts.append((msg, action)))
    monkeypatch.setattr(T.StatusDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(T.StatusDialog, "status", lambda self: "On Hold")
    select_keys(tab.grid, [seeded["c"]])
    tab.update_status()
    assert seeded["repo"].get(seeded["c"]).status == "On Hold"
    message, (label, undo) = toasts[-1]
    assert message == "1 milestone set to On Hold." and label == "Undo"
    undo()
    assert seeded["repo"].get(seeded["c"]).status == "Scheduled"


def test_PRD_051_shift_dialog_previews_and_applies(qtbot, app, seeded, monkeypatch):
    from slate.gui.components import feedback
    from slate.gui.tabs.shift_dates_dialog import ShiftDatesDialog
    from slate.gui.components.table_tools import select_keys
    tab = make_tab(qtbot)
    dialog = ShiftDatesDialog(None, milestones=tab.all_milestones, root_id=seeded["c"],
                              calendar=DS.WorkCalendar((5, 6)), days=2)
    qtbot.addWidget(dialog)
    assert dialog.preview.rowCount() == 1
    assert dialog.apply_button.text() == "Shift dates" and dialog.apply_button.isEnabled()
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: None)
    old = seeded["repo"].get(seeded["c"])
    tab.apply_plan(dialog.plan)
    new = seeded["repo"].get(seeded["c"])
    assert new.start > old.start


def test_PRD_011_the_timeline_draws_bars_today_and_weekends(qtbot, app, seeded):
    from slate.gui.tabs.prod_scheduling_tab import VIEW_TIMELINE
    tab = make_tab(qtbot)
    tab.set_view(VIEW_TIMELINE)
    gantt = tab.timeline.gantt
    assert gantt.bar_count == 3
    assert gantt.arrow_count == 1
    assert gantt.today_line is not None
    assert gantt.shade_count > 0
    gantt.set_zoom(DS.ZOOM_DAY)
    assert gantt.scale.px_per_day == DS.ZOOMS[DS.ZOOM_DAY]
    assert gantt.bar_count == 3
    image = gantt.image()
    assert image.width() > 300 and image.height() > 50
    # Folding a project hides its bars.
    gantt._header_clicked("project:AVTR3")
    assert gantt.bar_count == 1
    # The filters apply to the timeline too.
    gantt._header_clicked("project:AVTR3")
    tab.project_filter.setCurrentIndex(tab.project_filter.findData("RRR_REDUX"))
    assert gantt.bar_count == 1


def test_PRD_011_a_dragged_bar_goes_through_the_shift_preview(qtbot, app, seeded, monkeypatch):
    from slate.gui.tabs import shift_dates_dialog
    seen = {}

    def run(dialog, *args):
        seen["root_to"] = dialog.root_to
        seen["moves"] = len(dialog.plan.moved)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(shift_dates_dialog.ShiftDatesDialog, "exec", run)
    tab = make_tab(qtbot)
    m = seeded["repo"].get(seeded["c"])
    tab._timeline_dragged(m.id, m.start + timedelta(3), m.end + timedelta(3))
    assert seen["root_to"] == (m.start + timedelta(3), m.end + timedelta(3))
    assert seen["moves"] == 1


def test_PRD_012_the_people_view(qtbot, app, seeded):
    from slate.gui.tabs.prod_scheduling_tab import VIEW_PEOPLE
    db = seeded["db"]
    db.execute_update("INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                      "data_json, last_updated, version) VALUES ('AVTR3', 'R1', 'SH010', 'WIP', 2, "
                      "'{}', '2026-09-01', 1)")
    shot = dict(db.execute_query("SELECT id FROM tracking_shots", fetch="one"))["id"]
    target = (date.today() + timedelta(10)).isoformat()
    db.execute_update("INSERT INTO tracking_tasks (shot_id, project_code, department, status, "
                      "artist_name, bid_days, target_date) VALUES (%s, 'AVTR3', 'comp', 'WIP', "
                      "'rahul', 3, %s)", (shot, target))
    tab = make_tab(qtbot)
    tab.set_view(VIEW_PEOPLE)
    assert set(tab.people_plan.people) == {"priya", "rahul"}
    assert tab.people.gantt.bar_count == 2
    assert tab.people.problems.count() >= 1


def test_PRD_013_the_help_describes_what_is_built():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    help_text = json.loads((root / "slate/core/help_content.json").read_text(encoding="utf-8"))
    page = help_text["scheduling"]["content"]
    for words in ("Timeline", "People", "Shift dates", "working days", "Undo", "Edit the schedule"):
        assert words.casefold() in page.casefold(), words
    assert "same records the dashboard shows" not in page, "assignments are read-only here"


def test_PRD_035_a_failed_read_is_not_an_empty_schedule(qtbot, app, seeded, monkeypatch):
    from slate.core.infra.schedule_repository import ScheduleRepository
    from slate.gui.components.state_notice import StateNotice
    tab = make_tab(qtbot)

    def broken(self, include_archived=False):
        raise RuntimeError('column "start_date" does not exist')

    monkeypatch.setattr(ScheduleRepository, "list", broken)
    tab.load_data()
    notice = tab.findChild(StateNotice)
    assert notice is not None and notice.isVisible()
    assert tab.grid.rowCount() == 3, "the rows already shown are not wiped as 'no milestones'"


def test_PRD_073_a_large_schedule_fills_quickly(qtbot, app, mock_db):
    import time
    from slate.gui.tabs.prod_scheduling_tab import ProdSchedulingTab
    tab = make_tab(qtbot)
    start = date(2026, 1, 1)
    big = [DS.Milestone(id=i, project_code="P%d" % (i % 7), name=f"m{i}",
                        start=start + timedelta(i % 300), end=start + timedelta(i % 300 + 3),
                        status="Scheduled", depends_on_id=i - 1 if i > 1 else None)
           for i in range(1, 20001)]
    tab.by_id = DS.index(big)
    began = time.perf_counter()
    tab._fill(big)
    assert time.perf_counter() - began < 8.0
    assert tab.grid.rowCount() == 20000


def test_PRD_061_PRD_125_artists_and_coordinators_do_not_get_bidding():
    from slate.core.domain.permissions_catalog import STUDIO_ROLES
    for role in ("Roto Artist", "Paint Artist", "Compositor", "CG", "DMP"):
        assert "Scheduling" not in STUDIO_ROLES[role] and "Bidding" not in STUDIO_ROLES[role]
    assert "Bidding" not in STUDIO_ROLES["Production Coordinator"]
    assert "Scheduling" in STUDIO_ROLES["Production Coordinator"]
    import inspect
    from slate.core.domain import user_manager
    source = inspect.getsource(user_manager.UserManager._create_default_roles_sql)
    artist_line = next(l for l in source.splitlines() if l.strip().startswith('"Artist":'))
    assert "Scheduling" not in artist_line and "Bidding" not in artist_line
