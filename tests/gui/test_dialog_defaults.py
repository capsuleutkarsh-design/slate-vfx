"""
Enter in a dialog presses its primary action - never Cancel, never a side
action like "Auto-fill from Live Ops".

The IT dialogs built their own QPushButtons, and Qt makes the first
auto-default button in focus order the Enter target: in Add PC that was the
network auto-fill, in Record deployment, Add licence, Record usage and Report
a problem it was Cancel, so Enter in a half-typed form threw it away.
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module", autouse=True)
def forget_database_probes():
    """
    Some of these dialogs look things up in the database as they open. With no
    pooler on the test host that records "PgBouncer did not answer" on the
    manager's class for two minutes, which later tests of the pooler would
    then see. Leave that state as it was found.
    """
    yield
    try:
        from slate.core.infra.postgres_manager import PostgresManager
        PostgresManager._pooler_failed_at.clear()
    except Exception:
        pass


def default_buttons(dialog):
    return [b.text() for b in dialog.findChildren(QPushButton) if b.isDefault()]


def auto_default_buttons(dialog):
    return [b.text() for b in dialog.findChildren(QPushButton) if b.autoDefault()]


def assert_enter_presses(app, dialog, label):
    """The one default button is `label`, and Enter in a field presses it."""
    from PySide6.QtTest import QTest
    pressed = []
    target = next(b for b in dialog.findChildren(QPushButton) if b.text() == label)
    target.clicked.connect(lambda: pressed.append(label))
    for other in dialog.findChildren(QPushButton):
        if other is not target:
            other.clicked.connect(lambda _=False, t=other.text(): pressed.append(t))
    dialog.show()
    app.processEvents()
    assert default_buttons(dialog) == [label]
    assert auto_default_buttons(dialog) == [label]
    field = dialog.findChildren(QLineEdit)[0]
    field.setFocus()
    app.processEvents()
    QTest.keyClick(field, Qt.Key.Key_Return)
    app.processEvents()
    assert pressed and pressed[0] == label, pressed
    dialog.close()


def test_add_pc_enter_saves_not_auto_fill(app):
    from slate.gui.tabs.it_inventory_tab import AddPCDialog
    dialog = AddPCDialog(hub=None)
    dialog.accept = lambda: None
    # Save waits for a usable machine name (IT-023), then takes Enter.
    dialog.inp_name.setText("WS-COMP-07")
    assert_enter_presses(app, dialog, "Save")


def test_record_deployment_enter_records(app):
    from slate.gui.tabs.it_deployment_tab import AddDeploymentDialog
    dialog = AddDeploymentDialog()
    dialog.accept = lambda: None
    assert_enter_presses(app, dialog, "Record")


def test_licence_dialogs_enter_saves(app):
    from slate.gui.tabs.licence_view import LicenceDialog, ReadingDialog
    dialog = LicenceDialog()
    dialog._save = lambda: None
    assert default_buttons(dialog) == ["Save"]
    assert auto_default_buttons(dialog) == ["Save"]
    reading = ReadingDialog([{"software_name": "Nuke", "total_seats": 5, "id": 1}])
    assert default_buttons(reading) == ["Record"]
    assert "Cancel" not in auto_default_buttons(reading)


def test_report_a_problem_enter_sends(app):
    from slate.gui.tabs.my_tickets_view import RaiseTicketDialog
    dialog = RaiseTicketDialog()
    assert default_buttons(dialog) == ["Send to IT"]
    assert "Cancel" not in auto_default_buttons(dialog)
    # The dialog's own background no longer repaints every field in it.
    assert dialog.styleSheet().lstrip().startswith("QDialog")


def test_add_shots_primary_is_default_even_while_disabled(app):
    from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog
    dialog = AddShotsDialog()
    assert not dialog.ok_button.isEnabled()
    assert dialog.ok_button.isDefault()
    assert dialog.ok_button.property("kind") == "primary"
    cancel = next(b for b in dialog.findChildren(QPushButton) if b is not dialog.ok_button)
    assert not cancel.autoDefault()
