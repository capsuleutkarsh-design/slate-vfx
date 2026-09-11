"""
The department list is data, not code.

These tests guard the promise that adding a department to
slate/data/departments.json is enough - no code change, no migration, and no
loss of shots written before the department existed.
"""

import json
import io

import pytest

from slate.core.domain import departments as dept_module
from slate.core.domain.departments import (
    load_departments, department_keys, get_department, families, reset_cache,
)
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot, DepartmentInfo


@pytest.fixture(autouse=True)
def clean_cache():
    reset_cache()
    yield
    reset_cache()


class TestRegistry:

    def test_studio_departments_are_all_present(self):
        keys = department_keys()
        for expected in ("comp", "slapcomp", "roto", "prep", "cg",
                         "matchmove", "deage", "dmp", "mgfx", "ai"):
            assert expected in keys, f"{expected} missing from departments.json"

    def test_comp_and_slapcomp_share_a_family(self):
        assert get_department("comp").family == get_department("slapcomp").family

    def test_families_groups_related_departments(self):
        grouped = families()
        comp_family = {d.key for d in grouped["comp"]}
        assert comp_family == {"comp", "slapcomp"}

    def test_every_department_maps_to_a_project_folder(self):
        """A department with no folder has nowhere for its work to live."""
        with io.open("slate/data/templates.json", encoding="utf-8") as handle:
            templates = json.load(handle)
        standard = templates["standard"].get("structure", templates["standard"])
        tops = {f.split("/")[0] for f in standard["shot_folders"]}

        missing = [d.key for d in load_departments()
                   if d.folder and d.folder.split("/")[0] not in tops]
        assert not missing, f"departments with no folder in the template: {missing}"

    def test_a_broken_config_falls_back_to_defaults(self, tmp_path, monkeypatch):
        """A bad edit to departments.json must not leave the app with no columns."""
        broken = tmp_path / "departments.json"
        broken.write_text("{ not valid json", encoding="utf-8")
        monkeypatch.setattr(dept_module, "DEPARTMENTS_FILE", broken)
        reset_cache()

        assert "comp" in department_keys()
        assert len(load_departments()) >= 7


class TestShotDepartments:

    def test_new_department_is_usable_without_a_code_change(self):
        shot = Shot(shot_name="SH010")
        shot.dept("matchmove").status = "WIP"
        shot.dept("matchmove").artist = "Vikram"

        restored = Shot.from_dict(shot.to_dict())
        assert restored.dept("matchmove").status == "WIP"
        assert restored.dept("matchmove").artist == "Vikram"

    def test_legacy_attribute_access_still_works(self):
        """48 call sites use shot.comp_dept; they must keep working."""
        shot = Shot(shot_name="SH010")
        shot.comp_dept.status = "APPROVED"

        assert shot.dept("comp").status == "APPROVED"
        assert shot.comp_dept is shot.departments["comp"]

    def test_legacy_constructor_keyword_still_works(self):
        shot = Shot(shot_name="SH010",
                    comp_dept=DepartmentInfo(artist="Rahul", status="WIP"))
        assert shot.dept("comp").artist == "Rahul"
        assert shot.comp_dept.status == "WIP"

    def test_shots_saved_before_this_change_still_load(self):
        """Existing rows store one '<key>_dept' object per department."""
        legacy = {
            "shot_name": "OLD_SHOT",
            "status": "WIP",
            "comp_dept": {"status": "WIP", "artist": "Rahul", "bid_days": 3.0},
            "roto_dept": {"status": "APPROVED", "artist": "Priya"},
        }
        shot = Shot.from_dict(legacy)

        assert shot.dept("comp").status == "WIP"
        assert shot.dept("comp").artist == "Rahul"
        assert shot.dept("comp").bid_days == 3.0
        assert shot.dept("roto").status == "APPROVED"
        # Departments that did not exist when the row was written are empty,
        # not missing.
        assert shot.dept("matchmove").status == ""

    def test_get_all_artists_covers_every_department(self):
        shot = Shot(shot_name="SH010", assigned_artist="Rahul")
        shot.dept("roto").artist = "Priya"
        shot.dept("matchmove").artist = "Vikram"
        shot.dept("deage").artist = "Sana"

        artists = {a.lower() for a in shot.get_all_artists()}
        assert artists == {"rahul", "priya", "vikram", "sana"}


class TestTableColumns:

    def test_every_department_gets_a_column(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel

        model = ShotTableModel()
        column_keys = [col[0] for col in model.COLUMNS]

        for key in department_keys():
            assert key in column_keys, f"{key} has no table column"

    def test_department_columns_show_their_status(self, qtbot):
        from PySide6.QtCore import Qt
        from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel

        shot = Shot(shot_name="SH010")
        shot.dept("matchmove").status = "WIP"
        shot.dept("deage").status = "RETAKE"

        model = ShotTableModel()
        model.update_data([shot])

        values = {}
        for col, spec in enumerate(model.COLUMNS):
            values[spec[0]] = model.data(model.index(0, col),
                                         Qt.ItemDataRole.DisplayRole)

        assert values["matchmove"] == "WIP"
        assert values["deage"] == "RETAKE"

    def test_department_columns_are_editable_by_a_supervisor(self, qtbot):
        from PySide6.QtCore import Qt
        from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel

        shot = Shot(shot_name="SH010")
        model = ShotTableModel(shots=[shot], user_role="supervisor")

        col = [c[0] for c in model.COLUMNS].index("matchmove")
        index = model.index(0, col)

        assert model.flags(index) & Qt.ItemFlag.ItemIsEditable
        assert model.setData(index, "APPROVED", Qt.ItemDataRole.EditRole) is True
        assert shot.dept("matchmove").status == "APPROVED"


class TestDetailPanel:
    """The detail panel is where coordinators assign artists and bid days."""

    def test_every_department_gets_a_row(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.shot_detail import ShotDetailWidget

        shot = Shot(shot_name="SH010")
        panel = ShotDetailWidget(shot, user_role="supervisor",
                                 all_users=["Rahul", "Priya", "Vikram"])
        qtbot.addWidget(panel)

        for key in department_keys():
            assert key in panel.depts, f"{key} has no row in the detail panel"

    def test_editing_a_new_department_saves_back_to_the_shot(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.shot_detail import ShotDetailWidget

        shot = Shot(shot_name="SH010")
        panel = ShotDetailWidget(shot, user_role="supervisor",
                                 all_users=["Rahul", "Vikram"])
        qtbot.addWidget(panel)

        widgets = panel.depts["matchmove"]
        widgets["artist_combo"].setCurrentText("Vikram")
        widgets["status_combo"].setCurrentText("WIP")
        panel.save_data()

        assert shot.dept("matchmove").artist == "Vikram"
        assert shot.dept("matchmove").status == "WIP"
