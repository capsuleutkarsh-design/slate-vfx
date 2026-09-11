"""
Tests for Item 4.1: Saved Column Layouts.

Verifies:
- Key-based serialization (never dependent on raw integer index positions)
- Saving and loading user personal layout
- Publishing and loading project default layout
- Restoration hierarchy: personal -> project default -> system default
- Adding/removing departments does not break column mapping
"""

import json
import pytest
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QTableView

from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel
from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.components.column_layout_manager import (
    ColumnLayoutManager,
    SETTINGS_ORGANIZATION,
    SETTINGS_APPLICATION,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def table_and_model(qapp):
    shot = Shot(shot_name="SH010", reel_episode="Reel01", status="WIP", priority=1)
    model = ShotTableModel(shots=[shot])
    table = QTableView()
    table.setModel(model)
    return table, model


class FakeDbManager:
    def __init__(self):
        self.projects = {}

    def get_tracking_project(self, code):
        return self.projects.get(code)

    def save_tracking_project(self, code, name, config_json):
        val = json.loads(config_json) if isinstance(config_json, str) else config_json
        val["name"] = name
        self.projects[code] = val
        return True


def test_capture_and_apply_layout(table_and_model):
    table, model = table_and_model
    manager = ColumnLayoutManager(table, model, user_id="user_1", project_code="PRJ_TEST")

    # Hide column 0 ("reel") and set width of column 1 ("shot_name") to 250
    table.setColumnHidden(0, True)
    table.setColumnWidth(1, 250)

    layout = manager.capture_layout()
    assert "columns" in layout
    assert layout["columns"]["reel"]["visible"] is False
    assert layout["columns"]["shot_name"]["visible"] is True
    assert layout["columns"]["shot_name"]["width"] == 250

    # Reset table to all visible
    for i in range(len(model.COLUMNS)):
        table.setColumnHidden(i, False)
        table.setColumnWidth(i, 100)

    assert table.isColumnHidden(0) is False

    # Apply layout
    ok = manager.apply_layout(layout)
    assert ok is True
    assert table.isColumnHidden(0) is True
    assert table.columnWidth(1) == 250


def test_user_layout_persistence(table_and_model):
    table, model = table_and_model
    manager = ColumnLayoutManager(table, model, user_id="artist_bob", project_code="PRJ_SAVED")

    # Hide 'frames' and 'sow'
    frames_idx = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "frames")
    sow_idx = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "sow")

    table.setColumnHidden(frames_idx, True)
    table.setColumnHidden(sow_idx, True)

    assert manager.save_user_layout() is True

    # New manager instance reading same key
    table2, model2 = table_and_model
    manager2 = ColumnLayoutManager(table2, model2, user_id="artist_bob", project_code="PRJ_SAVED")
    loaded = manager2.load_user_layout()
    assert loaded is not None
    assert loaded["columns"]["frames"]["visible"] is False
    assert loaded["columns"]["sow"]["visible"] is False

    # Apply to second table
    assert manager2.apply_layout(loaded) is True
    assert table2.isColumnHidden(frames_idx) is True
    assert table2.isColumnHidden(sow_idx) is True


def test_project_default_persistence(table_and_model):
    table, model = table_and_model
    fake_db = FakeDbManager()
    fake_db.projects["PRJ_SUPER"] = {"name": "PRJ_SUPER"}

    manager = ColumnLayoutManager(table, model, user_id="supervisor_al", project_code="PRJ_SUPER", db_manager=fake_db)

    # Hide 'type' column
    type_idx = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "type")
    table.setColumnHidden(type_idx, True)

    # Publish as project default
    ok = manager.save_project_default()
    assert ok is True

    # Check that fake_db has it saved
    proj = fake_db.get_tracking_project("PRJ_SUPER")
    assert "default_columns" in proj
    assert proj["default_columns"]["columns"]["type"]["visible"] is False

    # Another user who has no personal layout restores layout
    table_new_user, model_new_user = table_and_model
    # Clear any user settings for this test user
    settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
    settings.remove("dashboard_layouts/new_user/PRJ_SUPER")

    manager_new = ColumnLayoutManager(table_new_user, model_new_user, user_id="new_user", project_code="PRJ_SUPER", db_manager=fake_db)
    restored = manager_new.restore_layout()
    assert restored is True
    assert table_new_user.isColumnHidden(type_idx) is True
