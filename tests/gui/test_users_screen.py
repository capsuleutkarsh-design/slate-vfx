"""
Users & Roles screen, offscreen (audit 2026-09: HR-124/125/128/133/136/137/
138/147/148/149/150/151).
"""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402


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
def um(mock_db, app):
    from slate.core.domain import access, people
    from slate.core.domain.user_manager import UserManager
    access.reset_cache()
    manager = UserManager(db=mock_db)
    manager.add_user("sup.vikram", "pw1234", ["Supervisor"], "Vikram Malhotra", "Comp", location="Mumbai")
    manager.add_user("aarav", "pw1234", ["Artist"], "Aarav Sharma", "Comp",
                     reports_to="sup.vikram", location="Mumbai", employment="Staff", joined_on="2019-04-01")
    manager.add_user("diya", "pw1234", ["Artist"], "Diya Nair", "Roto", location="Chennai")
    people.refresh()
    yield manager
    access.reset_cache()
    people.refresh()


def _panel(um):
    from slate.gui.tabs.admin_users_tab import UsersPanel
    return UsersPanel(um)


def _visible(panel, column=0):
    return [panel.grid.item(r, column).text() for r in range(panel.grid.rowCount())
            if not panel.grid.isRowHidden(r)]


def test_actions_wait_for_a_selection(um):
    panel = _panel(um)
    assert not any(b.isEnabled() for b in panel._needs_selection)
    panel.grid.selectRow(0)
    assert panel.edit_btn.isEnabled() and panel.reset_btn.isEnabled()


def test_location_is_shown_and_search_covers_every_column(um):
    panel = _panel(um)
    assert "Location" in panel.COLUMNS
    panel.search.setText("chennai")
    assert _visible(panel) == ["diya"]
    panel.search.setText("vikram")             # the manager column, by name
    assert set(_visible(panel)) == {"aarav", "sup.vikram"}


def test_the_table_sorts_and_edits_the_right_person(um, monkeypatch):
    panel = _panel(um)
    assert panel.grid.isSortingEnabled()
    panel.grid.sortItems(1)                     # by display name
    row = [panel.grid.item(r, 1).text() for r in range(panel.grid.rowCount())].index("Diya Nair")
    panel.grid.selectRow(row)
    assert panel._selected_username() == "diya"


def test_people_without_an_approver_are_flagged(um):
    panel = _panel(um)
    rows = {panel.grid.item(r, 0).text(): r for r in range(panel.grid.rowCount())}
    tip = panel.grid.item(rows["diya"], panel.COLUMNS.index("Reports to")).toolTip()
    assert "leave goes to HR" in tip


def test_the_add_dialog(um):
    from slate.gui.tabs.admin_users_tab import UserDialog
    dialog = UserDialog(um)
    assert dialog.dept_input.currentText() == "", "no department chosen for you"
    managers = [dialog.reports_input.itemData(i) for i in range(dialog.reports_input.count())]
    assert "sup.vikram" in managers and "aarav" not in managers and "diya" not in managers
    locations = [dialog.location_input.itemText(i) for i in range(dialog.location_input.count())]
    assert "Mumbai" in locations and "Chennai" in locations
    dialog.username_input.setText("ravi kumar")
    assert not dialog.error.isHidden() and "username is" in dialog.error.text().lower()
    dialog.username_input.setText("Aarav")
    assert "already taken" in dialog.error.text()
    buttons = [b.text() for b in dialog.findChildren(QPushButton) if b.isVisibleTo(dialog)]
    assert buttons[-2:] == ["Cancel", "Add user"], "Cancel first, the action last"


def test_clearing_a_field_in_the_edit_dialog_clears_it(um):
    from slate.gui.tabs.admin_users_tab import UserDialog
    from slate.core.domain.user_manager import UserManager
    record = um.get_all_users()["aarav"]
    dialog = UserDialog(um, username="aarav", record=record)
    dialog.reports_input.setCurrentIndex(0)                    # Nobody
    dialog.employment_input.setCurrentIndex(0)                 # Not recorded
    values = dialog.payload()
    assert values["reports_to"] == UserManager.CLEAR
    assert values["employment"] == UserManager.CLEAR
    assert values["location"] == "Mumbai"


def test_each_action_follows_the_selected_account(um):
    """NEW-people-5."""
    um.deactivate_user("diya")
    panel = _panel(um)
    panel.show_inactive.setChecked(True)
    rows = {panel.grid.item(r, 0).text(): r for r in range(panel.grid.rowCount())}
    panel.grid.selectRow(rows["aarav"])
    assert panel.deactivate_btn.isEnabled() and not panel.reactivate_btn.isEnabled()
    panel.grid.selectRow(rows["diya"])
    assert panel.reactivate_btn.isEnabled() and not panel.deactivate_btn.isEnabled()
    panel.grid.selectRow(rows["admin"])
    assert not panel.deactivate_btn.isEnabled() and not panel.delete_btn.isEnabled()
    assert panel.edit_btn.isEnabled()
