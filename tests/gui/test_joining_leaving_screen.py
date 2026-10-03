"""
Joining & Leaving, offscreen: one row per person and direction (HR-104),
names and dates (HR-111), header words (HR-116), the en-dash heading (HR-117),
an empty state until somebody is picked (HR-113), no Owner column and Machine
for IT only (HR-115), who ticked a line (HR-110), employment and department
lists shared with Users & Roles (HR-108, HR-119), the machine picker with
specs (HR-118).
"""

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _tidy(app):
    yield
    import gc
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.processEvents()
    gc.collect()
    QApplication.processEvents()


@pytest.fixture
def studio(mock_db, app):
    from slate.core.domain import people
    from slate.core.domain.user_manager import UserManager
    from slate.core.domain.onboarding_service import OnboardingService, JOINING, LEAVING
    um = UserManager(db=mock_db)
    um.add_user("aarav", "pw", ["Artist"], "Aarav Sharma", "Comp", joined_on="2026-09-01")
    um.add_user("sai", "pw", ["Artist"], "Sai Menon", "Paint")
    people.refresh()
    service = OnboardingService(mock_db)
    service.start("aarav", JOINING)
    service.start("aarav", LEAVING, effective_date=date.today() + timedelta(days=20))
    service.start("sai", JOINING, effective_date=date(2026, 10, 5))
    yield mock_db
    people.refresh()


def _cells(table, column):
    return [table.item(r, column).text() for r in range(table.rowCount())]


def test_a_person_joining_and_leaving_has_two_rows(studio):
    from slate.gui.tabs.joining_leaving_view import JoiningLeavingView
    view = JoiningLeavingView("hr.meera", "HR", studio)
    assert view.PEOPLE_COLUMNS == ["Person", "Joining / leaving", "Date", "Your tasks", "Total open"]
    names = _cells(view.people_table, 0)
    assert names.count("Aarav Sharma") == 2 and "Sai Menon" in names
    assert "aarav" not in names


def test_the_first_person_is_picked_then_one_direction_is_shown(studio):
    """HR2-083: the first row is chosen instead of an empty half-screen."""
    from slate.gui.tabs.joining_leaving_view import JoiningLeavingView
    view = JoiningLeavingView("hr.meera", "HR", studio)
    assert not view.detail.isHidden() and view.pick_empty.isHidden()
    assert view._selected_person is not None and view.people_table.selectedItems()
    row = [r for r in range(view.people_table.rowCount())
           if view.people_table.item(r, 0).text() == "Aarav Sharma"
           and view.people_table.item(r, 1).text() == "Leaving"][0]
    view.people_table.selectRow(row)
    assert not view.detail.isHidden()
    assert view.heading.text().startswith("Aarav Sharma – leaving –")
    assert "Owner" not in view.task_columns and "Machine" not in view.task_columns
    tasks = _cells(view.task_table, 1)
    assert "Signed contract received" not in tasks, "joining lines stay with joining"


def test_a_tick_shows_who_did_it(studio):
    from slate.gui.tabs.joining_leaving_view import JoiningLeavingView
    view = JoiningLeavingView("hr.meera", "HR", studio)
    view.people_table.selectRow(_cells(view.people_table, 0).index("Sai Menon"))
    view.task_table.selectRow(0)
    view._tick(True)
    view.people_table.selectRow(_cells(view.people_table, 0).index("Sai Menon"))
    assert any(text.startswith("hr.meera on") or "on " in text
               for text in _cells(view.task_table, 2))


def test_it_sees_the_machine_column(studio):
    from slate.gui.tabs.joining_leaving_view import JoiningLeavingView
    view = JoiningLeavingView("it.sana", "IT", studio)
    assert "Machine" in view.task_columns


def test_the_start_dialog_uses_the_shared_lists(studio):
    from slate.gui.tabs.joining_leaving_view import StartPersonDialog
    from slate.core.domain.onboarding_service import OnboardingService, JOINING
    from slate.core.domain.departments import staff_department_names
    dialog = StartPersonDialog(OnboardingService(studio), JOINING)
    values = [dialog.employment.itemData(i) for i in range(dialog.employment.count())]
    assert values == ["Staff", "Freelance", "Contract"]
    items = [dialog.department.itemText(i) for i in range(dialog.department.count())]
    assert items == staff_department_names()


def test_the_machine_picker_shows_specs(app):
    from slate.gui.tabs.joining_leaving_view import MachinePickerDialog
    dialog = MachinePickerDialog([
        {"machine_name": "WS-1", "type": "Workstation", "gpu": "RTX 4090", "cpu": "", "ram": "",
         "location": "Mumbai", "status": "Available"},
        {"machine_name": "WS-2", "type": "Laptop", "gpu": "", "cpu": "", "ram": "",
         "location": "Chennai", "status": "Available"}], "Sai Menon (sai)")
    assert dialog.table.item(0, 2).text() == "RTX 4090"
    dialog.table.selectRow(1)
    assert dialog.machine() == "WS-2"


def test_who_gets_which_half(app, mock_db):
    """NEW-people-4: admins and people who are HR and IT work both halves."""
    from slate.gui.tabs.joining_leaving_view import joining_teams
    assert joining_teams(["Developer"], ["ALL"]) == ["HR", "IT"]
    assert joining_teams(["HR"], ["HRMS"]) == ["HR"]
    assert joining_teams(["IT"], ["IT"]) == ["IT"]


# ------------------------------------------------------------ round 2

def test_the_start_dialog_prefills_from_the_person(studio):
    """HR2-076: Employment and Department come from the record, not 'Staff'."""
    from slate.gui.tabs.joining_leaving_view import StartPersonDialog
    from slate.core.domain.onboarding_service import OnboardingService, LEAVING
    from slate.core.domain.user_manager import UserManager
    from slate.core.domain import people
    UserManager(db=studio).add_user("pari", "pw", ["Artist"], "Pari Shah", "Paint",
                                    employment="Freelance", joined_on="2026-06-01")
    people.refresh()
    dialog = StartPersonDialog(OnboardingService(studio), LEAVING)
    dialog.person.set_username("pari")
    assert dialog.employment.currentData() == "Freelance"
    assert dialog.department.currentText() == "Paint"
    assert dialog.effective.minimumDate().toString("yyyy-MM-dd") == "2026-06-01"


def test_leaving_offers_people_who_have_gone_and_the_hint_shows(studio):
    """HR2-077 / HR2-078."""
    from slate.gui.tabs.joining_leaving_view import StartPersonDialog
    from slate.core.domain.onboarding_service import OnboardingService, JOINING, LEAVING
    from slate.core.domain.user_manager import UserManager
    from slate.core.domain import people
    UserManager(db=studio).add_user("kabir.left", "pw", ["Artist"], "Kabir", "Comp",
                                    last_day="2026-01-15")
    people.refresh()
    leaving = StartPersonDialog(OnboardingService(studio), LEAVING)
    texts = [leaving.person.itemText(i) for i in range(leaving.person.count())]
    assert "Kabir (kabir.left) (left)" in texts
    leaving.person.set_username("kabir.left")
    assert leaving.start_button.isEnabled() and "already left" in leaving.person_hint.text()

    joining = StartPersonDialog(OnboardingService(studio), JOINING)
    joining.person.lineEdit().setText("nobody at all")
    assert "No such person" in joining.person_hint.text()
    joining.person.lineEdit().setText("Kabir")
    assert "deactivated or has left" in joining.person_hint.text()
    assert not joining.start_button.isEnabled()
