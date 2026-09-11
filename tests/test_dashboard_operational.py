"""
The things a coordinator needs before the dashboard is usable for a day's work:
they can get shots in, and they can find them.
"""

import pytest
from PySide6.QtCore import Qt

from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel


def _shots():
    a = Shot(shot_name="SH030", reel_episode="ReelB", status="WIP",
             edit_frames=200, priority=1)
    b = Shot(shot_name="SH010", reel_episode="ReelA", status="APPROVED",
             edit_frames=85, priority=3)
    c = Shot(shot_name="SH020", reel_episode="ReelA", status="RETAKE",
             edit_frames=120, priority=2)
    return [a, b, c]


def _column(model, key):
    return [c[0] for c in model.COLUMNS].index(key)


def _values(model, key):
    col = _column(model, key)
    return [model.data(model.index(row, col), Qt.ItemDataRole.DisplayRole)
            for row in range(model.rowCount())]


class TestSorting:
    """The table had no sorting at all - clicking a heading did nothing."""

    def test_sort_by_text_column(self, qtbot):
        model = ShotTableModel(shots=_shots())

        model.sort(_column(model, "shot_name"), Qt.SortOrder.AscendingOrder)
        assert _values(model, "shot_name") == ["SH010", "SH020", "SH030"]

        model.sort(_column(model, "shot_name"), Qt.SortOrder.DescendingOrder)
        assert _values(model, "shot_name") == ["SH030", "SH020", "SH010"]

    def test_numeric_columns_sort_as_numbers(self, qtbot):
        """85 must come before 120, not after it as a string would."""
        model = ShotTableModel(shots=_shots())

        model.sort(_column(model, "frames"), Qt.SortOrder.AscendingOrder)
        assert _values(model, "frames") == ["85", "120", "200"]

    def test_priority_sorts_numerically(self, qtbot):
        model = ShotTableModel(shots=_shots())

        model.sort(_column(model, "priority"), Qt.SortOrder.AscendingOrder)
        assert _values(model, "priority") == ["1", "2", "3"]

    def test_blank_cells_sort_last_in_both_directions(self, qtbot):
        """An unscheduled shot is not 'the earliest target date'."""
        shots = _shots()
        shots[0].target = "2026-10-01"
        shots[1].target = "2026-09-15"
        # shots[2] deliberately has no target

        model = ShotTableModel(shots=shots)

        model.sort(_column(model, "target"), Qt.SortOrder.AscendingOrder)
        assert _values(model, "target")[-1] == "-"

        model.sort(_column(model, "target"), Qt.SortOrder.DescendingOrder)
        assert _values(model, "target")[-1] == "-"

    def test_sort_survives_a_data_refresh(self, qtbot):
        model = ShotTableModel(shots=_shots())
        model.sort(_column(model, "shot_name"), Qt.SortOrder.DescendingOrder)

        model.update_data(_shots())   # e.g. after a poll or a save
        assert _values(model, "shot_name") == ["SH030", "SH020", "SH010"]

    def test_sorting_a_department_column_works(self, qtbot):
        shots = _shots()
        shots[0].dept("matchmove").status = "WIP"
        shots[1].dept("matchmove").status = "APPROVED"

        model = ShotTableModel(shots=shots)
        model.sort(_column(model, "matchmove"), Qt.SortOrder.AscendingOrder)

        values = _values(model, "matchmove")
        assert values[0] == "APPROVED"
        assert values[-1] == "-"      # the shot with no matchmove work

    def test_sorting_an_out_of_range_column_is_ignored(self, qtbot):
        model = ShotTableModel(shots=_shots())
        model.sort(999, Qt.SortOrder.AscendingOrder)   # must not raise
        assert model.rowCount() == 3


class TestAddShotsDialog:
    """Manual shot entry, for what does not arrive on a scan drive."""

    def test_parses_one_name_per_line(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog

        dialog = AddShotsDialog()
        qtbot.addWidget(dialog)
        dialog.shots_input.setPlainText("SH010\nSH020\n\n  SH030  \n")

        assert dialog.shot_names() == ["SH010", "SH020", "SH030"]

    def test_ignores_shots_already_in_the_project(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog

        dialog = AddShotsDialog(existing_shots=["SH010", "SH020"])
        qtbot.addWidget(dialog)
        dialog.shots_input.setPlainText("SH010\nSH030\nsh020")

        assert dialog.shot_names() == ["SH030"]
        assert sorted(dialog.skipped_names()) == ["SH010", "sh020"]

    def test_duplicates_in_the_pasted_list_are_collapsed(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog

        dialog = AddShotsDialog()
        qtbot.addWidget(dialog)
        dialog.shots_input.setPlainText("SH010\nSH010\nSH020")

        assert dialog.shot_names() == ["SH010", "SH020"]

    def test_ok_is_disabled_until_there_is_something_to_add(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog

        dialog = AddShotsDialog(existing_shots=["SH010"])
        qtbot.addWidget(dialog)

        assert dialog.ok_button.isEnabled() is False

        dialog.shots_input.setPlainText("SH010")      # already exists
        assert dialog.ok_button.isEnabled() is False

        dialog.shots_input.setPlainText("SH999")
        assert dialog.ok_button.isEnabled() is True

    def test_returns_the_chosen_status_and_priority(self, qtbot):
        from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog

        dialog = AddShotsDialog()
        qtbot.addWidget(dialog)
        dialog.reel_input.setCurrentText("ReelA")
        dialog.shots_input.setPlainText("SH010")
        dialog.status_input.setCurrentText("WIP")
        dialog.priority_input.setValue(1)

        values = dialog.get_values()
        assert values == {"reel": "ReelA", "shots": ["SH010"],
                          "status": "WIP", "priority": 1}


class TestNewProjectDefaults:
    """
    A newly created project must be able to back up every department. The
    hand-written default mapping had no comp columns at all - the studio's
    main department could not be written to its own backup sheet.
    """

    def test_every_department_gets_excel_columns(self):
        from slate.core.domain.departments import department_keys
        from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import (
            _extend_mapping_with_departments,
        )

        legacy = {
            "shot_name": "D", "assigned_artist": "K", "internal_status": "N",
            "roto_bid": "S", "roto_artist": "T", "roto_status": "U",
            "roto_eta": "V", "dmp_status": "W", "dmp_mandays": "X",
            "dmp_eta": "Y", "cg_status": "Z", "cg_artist": "AA",
            "cg_mandays": "AB", "cg_eta": "AC",
        }
        mapping = _extend_mapping_with_departments(legacy)

        for key in department_keys():
            has_artist = f"{key}_artist" in mapping
            has_status = any(f"{key}_{s}" in mapping
                             for s in ("status", "required", "comp"))
            assert has_artist, f"{key} has no artist column"
            assert has_status, f"{key} has no status column"

    def test_existing_columns_are_never_moved(self):
        """Re-mapping must not shuffle a sheet layout already in use."""
        from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import (
            _extend_mapping_with_departments,
        )

        legacy = {"shot_name": "D", "roto_artist": "T", "cg_status": "Z"}
        mapping = _extend_mapping_with_departments(legacy)

        assert mapping["shot_name"] == "D"
        assert mapping["roto_artist"] == "T"
        assert mapping["cg_status"] == "Z"

    def test_no_two_fields_share_a_column(self):
        from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import (
            _extend_mapping_with_departments,
        )

        mapping = _extend_mapping_with_departments(
            {"shot_name": "D", "roto_artist": "T"}
        )
        letters = list(mapping.values())
        assert len(letters) == len(set(letters)), "two fields share a column"

    def test_folder_template_matches_the_departments(self):
        from slate.core.domain.departments import department_keys
        from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import (
            _default_folder_template,
        )

        template = _default_folder_template()
        for key in department_keys():
            assert key in template, f"{key} has no folder path"
            assert "{reel}" in template[key] and "{shot}" in template[key]


class TestUndo:
    """A status changed by mistake used to be permanent."""

    def _model(self, role="supervisor"):
        shot = Shot(shot_name="SH010", reel_episode="ReelA", status="YTS",
                    priority=3)
        shot.dept("comp").status = "YTS"
        return ShotTableModel(shots=[shot], user_role=role), shot

    def _index(self, model, key):
        col = [c[0] for c in model.COLUMNS].index(key)
        return model.index(0, col)

    def test_nothing_to_undo_on_a_fresh_grid(self, qtbot):
        model, _ = self._model()
        assert model.can_undo() is False
        assert model.undo() is None

    def test_a_cell_edit_can_be_taken_back(self, qtbot):
        model, shot = self._model()
        model.setData(self._index(model, "status"), "APPROVED",
                      Qt.ItemDataRole.EditRole)
        assert shot.status == "APPROVED"

        undone = model.undo()
        assert shot.status == "YTS"
        assert undone["shot"] == "SH010"
        assert undone["column"] == "status"

    def test_a_department_cell_can_be_taken_back(self, qtbot):
        model, shot = self._model()
        model.setData(self._index(model, "comp"), "WIP", Qt.ItemDataRole.EditRole)
        model.undo()
        assert shot.dept("comp").status == "YTS"

    def test_undo_walks_back_several_edits(self, qtbot):
        model, shot = self._model()
        model.setData(self._index(model, "status"), "WIP", Qt.ItemDataRole.EditRole)
        model.setData(self._index(model, "priority"), "1", Qt.ItemDataRole.EditRole)

        model.undo()
        assert shot.priority == 3
        assert shot.status == "WIP"      # the earlier edit still stands

        model.undo()
        assert shot.status == "YTS"
        assert model.can_undo() is False

    def test_an_edit_that_changes_nothing_is_not_recorded(self, qtbot):
        model, _ = self._model()
        model.setData(self._index(model, "status"), "YTS", Qt.ItemDataRole.EditRole)
        assert model.can_undo() is False

    def test_the_stack_is_bounded(self, qtbot):
        model, _ = self._model()
        for n in range(model.UNDO_LIMIT + 20):
            model.setData(self._index(model, "sow"), f"note {n}",
                          Qt.ItemDataRole.EditRole)
        assert len(model._undo_stack) == model.UNDO_LIMIT

    def test_a_refresh_clears_pending_undo(self, qtbot):
        """The shot objects are replaced, so the old steps no longer apply."""
        model, _ = self._model()
        model.setData(self._index(model, "status"), "WIP", Qt.ItemDataRole.EditRole)
        assert model.can_undo() is True

        model.update_data([Shot(shot_name="SH010", reel_episode="ReelA")])
        assert model.can_undo() is False
