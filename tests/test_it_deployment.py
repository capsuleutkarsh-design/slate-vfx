"""The deployment log (IT-134 ... IT-153), repository on both backends and the tab."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from slate.core.infra.deployment_repository import (
    DeploymentError, DeploymentRepository, split_machines, success_rate,
)


def test_machines_are_split_and_rate_is_rounded():
    """IT-142, IT-150."""
    assert split_machines("WS-01, ws-01; WS-02\nWS-03") == ["WS-01", "WS-02", "WS-03"]
    assert success_rate([{"status": "Success"}, {"status": "Success"}, {"status": "Failed"},
                         {"status": "Pending"}]) == 67
    assert success_rate([{"status": "Pending"}]) is None


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "dep.db"))
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
    return DeploymentRepository(request.getfixturevalue("sqlite_db" if request.param == "sqlite" else "pg_db"))


def test_one_record_per_machine_with_version_and_notes(repo):
    """IT-142, IT-143."""
    ids = repo.record("Nuke", ["WS-01", "WS-02"], "it.sana", version="15.1v3", notes="from the share")
    rows = repo.all()
    assert len(ids) == 2 and len(rows) == 2
    assert {r["target_machine"] for r in rows} == {"WS-01", "WS-02"}
    assert all(r["version"] == "15.1v3" and r["status"] == "Pending" for r in rows)
    with pytest.raises(DeploymentError):
        repo.record("", ["WS-01"], "it.sana")


def test_outcome_records_who_and_when_and_edit_delete_work(repo):
    """IT-143, IT-144."""
    dep_id = repo.record("OCIO config", ["WS-01"], "it.sana")[0]
    repo.set_outcome(dep_id, "Failed", "it.joe", "disk full")
    row = repo.all()[0]
    assert row["status"] == "Failed" and row["completed_by"] == "it.joe"
    assert row["completed_at"] is not None and row["notes"] == "disk full"
    repo.update(dep_id, "OCIO config", "WS-09", "2.1", "moved")
    assert repo.all()[0]["target_machine"] == "WS-09"
    repo.delete(dep_id)
    assert repo.all() == []
    with pytest.raises(DeploymentError):
        repo.delete(dep_id)


def test_the_recorder_is_never_admin_by_default(repo):
    """IT-149."""
    repo.record("Nuke", ["WS-01"], "")
    assert repo.all()[0]["deployed_by"] == "unknown"


# ---------------------------------------------------------------------- tab

@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


_KEEP = []


def _tab(user_data=None):
    from slate.gui.tabs.it_deployment_tab import ItDeploymentTab
    tab = ItDeploymentTab(user_data=user_data if user_data is not None else {"username": "it.sana"})
    _KEEP.append(tab)
    tab.show()
    return tab


def test_the_tab_filters_counts_and_records(sqlite_db, app, monkeypatch):
    """IT-134, IT-135, IT-140, IT-150, IT-151, IT-152."""
    from slate.gui.tabs import it_deployment_tab as module
    from slate.gui.components import feedback
    from PySide6.QtWidgets import QDialog
    repo = DeploymentRepository(sqlite_db)
    a, b, c = (repo.record(p, ["WS-01"], "it.sana")[0] for p in ("A", "B", "C"))
    repo.set_outcome(a, "Success", "it.sana")
    repo.set_outcome(b, "Success", "it.sana")
    repo.set_outcome(c, "Failed", "it.sana")
    repo.record("D", ["WS-01"], "it.sana")
    tab = _tab()
    assert tab.lbl_success.value_text() == "67%"
    assert tab.lbl_total._label.text() == "INSTALLS RECORDED"
    assert tab.lbl_pending._label.text() == "AWAITING OUTCOME"
    assert tab.filter_cb.itemText(0) == "All statuses"
    tab.filter_cb.setCurrentIndex(tab.filter_cb.findData("Failed"))
    visible = [r for r in range(tab.grid.rowCount()) if not tab.grid.isRowHidden(r)]
    assert len(visible) == 1

    class Filled(module.AddDeploymentDialog):
        def exec(self):
            self.pkg_input.setText("Houdini 20.5")
            self.target_input.setText("WS-01, WS-02")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "AddDeploymentDialog", Filled)
    toasts = []
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: toasts.append(a[1]))
    tab.filter_cb.setCurrentIndex(0)
    tab.add_deployment()
    assert tab.grid.rowCount() == 6 and tab.lbl_total.value_text() == "6"
    assert toasts[-1].startswith("Recorded")


def test_flipping_a_finished_outcome_asks(sqlite_db, app, monkeypatch):
    """IT-144."""
    from slate.gui.components import feedback
    repo = DeploymentRepository(sqlite_db)
    dep = repo.record("A", ["WS-01"], "it.sana")[0]
    repo.set_outcome(dep, "Failed", "it.sana")
    tab = _tab()
    tab.grid.selectRow(0)
    asked = []
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: asked.append(a) or False)
    tab.update_status("Success")
    assert asked and repo.all()[0]["status"] == "Failed"


def test_unknown_machines_are_questioned(sqlite_db, app, monkeypatch):
    """IT-142."""
    from slate.gui.components import feedback
    from slate.gui.tabs.it_deployment_tab import AddDeploymentDialog
    dialog = AddDeploymentDialog(machines=["WS-COMP-01"])
    _KEEP.append(dialog)
    dialog.pkg_input.setText("Nuke")
    dialog.target_input.setText("ws-comp-01, NOT-A-REAL-MACHINE")
    assert "NOT-A-REAL-MACHINE" in dialog.hint.text()
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    accepted = []
    dialog.accept = lambda: accepted.append(True)
    dialog._save()
    assert accepted and dialog.target_input.text() == "WS-COMP-01, NOT-A-REAL-MACHINE"


def test_no_old_button_styles_or_hex_left():
    """IT-156, IT-157."""
    import io
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("slate/gui/tabs/it_inventory_tab.py", "slate/gui/tabs/it_deployment_tab.py"):
        text = io.open(os.path.join(root, rel), encoding="utf-8").read()
        for banned in ("setObjectName(\"primaryButton\")", "setObjectName(\"secondaryButton\")",
                       "setObjectName(\"dangerButton\")", "#0D0D0F", "#16161A", "#D9A441",
                       "QPushButton("):
            assert banned not in text, (rel, banned)
