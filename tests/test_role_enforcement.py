"""
What each role can actually do, checked from the outside.

These came out of driving the dashboard as four people. Every one of them is
a rule the interface *looked* like it enforced and the database did not - or
the other way round. So each is tested where it bites: in the handler that
writes, and in the model that decides which cells open.
"""

import pytest
from PySide6.QtCore import Qt

from slate.core.domain.access import (
    artist_statuses, can_force_save, can_set_status, is_department_scoped,
)
from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import (
    SQLiteHandler, StaleDataError,
)
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel


PRIYA = ["priya", "Priya"]


def _seed(mock_db):
    """Two shots: priya comps SH010, arjun rotos SH020."""
    handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
    a = Shot(shot_name="SH010", reel_episode="ReelA", status="WIP", sow="wires")
    a.dept("comp").artist = "priya"
    a.dept("comp").status = "WIP"
    b = Shot(shot_name="SH020", reel_episode="ReelA", status="WIP")
    b.dept("roto").artist = "arjun"
    b.dept("roto").status = "WIP"
    handler.write_shots([a, b])
    return handler


def _read(mock_db, name):
    handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
    return next(s for s in handler.read_shots() if s.shot_name == name)


class TestAnArtistCannotApproveAnything:
    """The states of their own work, and nothing that is a verdict."""

    def test_the_allowed_list_is_work_states_only(self):
        assert artist_statuses() == {"YTS", "WIP", "READY", "SENT FOR REVIEW"}

    @pytest.mark.parametrize("status", ["APPROVED", "RETAKE", "OMIT", "approved"])
    def test_verdicts_are_not_for_artists(self, status):
        assert not can_set_status(["artist"], status)

    @pytest.mark.parametrize("status", ["WIP", "SENT FOR REVIEW", "ready", "YTS"])
    def test_work_states_are(self, status):
        assert can_set_status(["artist"], status)

    def test_a_supervisor_may_set_anything(self):
        assert can_set_status(["supervisor"], "APPROVED")

    def test_the_database_refuses_an_artists_approval(self, mock_db):
        """Not the dropdown - the write itself."""
        _seed(mock_db)
        artist = SQLiteHandler("PRJ", db_manager=mock_db, user_role="artist")
        shot = _read(mock_db, "SH010")

        with pytest.raises(PermissionError, match="verdict"):
            artist.update_department_status(
                "SH010", "ReelA", "comp", "APPROVED", shot.version,
                actor_identities=PRIYA,
            )
        assert _read(mock_db, "SH010").dept("comp").status == "WIP"

    def test_sent_for_review_still_goes_through(self, mock_db):
        _seed(mock_db)
        artist = SQLiteHandler("PRJ", db_manager=mock_db, user_role="artist")
        shot = _read(mock_db, "SH010")

        artist.update_department_status(
            "SH010", "ReelA", "comp", "SENT FOR REVIEW", shot.version,
            actor_identities=PRIYA,
        )
        assert _read(mock_db, "SH010").dept("comp").status == "SENT FOR REVIEW"


class TestALeadIsConfinedToTheirDepartment:

    def test_a_lead_is_scoped_and_a_supervisor_is_not(self):
        assert is_department_scoped(["lead"])
        assert not is_department_scoped(["supervisor"])
        assert not is_department_scoped(["lead", "supervisor"])
        assert not is_department_scoped(["artist"])

    def _lead(self, mock_db, family="roto"):
        return SQLiteHandler("PRJ", db_manager=mock_db, user_role="lead",
                             department_family=family)

    def test_a_roto_lead_can_change_roto(self, mock_db):
        _seed(mock_db)
        shot = _read(mock_db, "SH020")
        shot.dept("roto").status = "SENT FOR REVIEW"

        assert self._lead(mock_db).write_shots([shot])
        assert _read(mock_db, "SH020").dept("roto").status == "SENT FOR REVIEW"

    def test_a_roto_lead_cannot_reassign_comp(self, mock_db):
        """The exact thing that went through in the audit."""
        _seed(mock_db)
        shot = _read(mock_db, "SH010")
        shot.dept("comp").artist = "someone_else"

        with pytest.raises(PermissionError, match="another department"):
            self._lead(mock_db).write_shots([shot])
        assert _read(mock_db, "SH010").dept("comp").artist == "priya"

    def test_a_lead_cannot_change_the_shot_itself(self, mock_db):
        _seed(mock_db)
        shot = _read(mock_db, "SH020")
        shot.status = "APPROVED"

        with pytest.raises(PermissionError, match="not the shot itself"):
            self._lead(mock_db).write_shots([shot])
        assert _read(mock_db, "SH020").status == "WIP"

    def test_a_lead_cannot_add_shots(self, mock_db):
        _seed(mock_db)
        new = Shot(shot_name="SH999", reel_episode="ReelA")

        with pytest.raises(PermissionError, match="coordinator"):
            self._lead(mock_db).write_shots([new])

    def test_a_lead_with_no_department_on_record_is_told_why(self, mock_db):
        _seed(mock_db)
        shot = _read(mock_db, "SH020")
        shot.dept("roto").status = "READY"

        with pytest.raises(PermissionError, match="job title"):
            self._lead(mock_db, family="").write_shots([shot])

    def test_unchanged_shots_in_a_bulk_save_are_fine(self, mock_db):
        """Save-all writes every loaded shot; only the changed ones matter."""
        handler = _seed(mock_db)
        shots = handler.read_shots()
        next(s for s in shots if s.shot_name == "SH020").dept("roto").status = "READY"

        assert self._lead(mock_db).write_shots(shots)

    def test_the_grid_opens_only_their_columns(self):
        model = ShotTableModel(user_role=["lead"])
        model.department_scope = {"roto"}
        shot = Shot(shot_name="SH020", reel_episode="ReelA")
        model.update_data([shot])

        editable = set()
        for col, (key, _label, _getter) in enumerate(model.COLUMNS):
            row = next(i for i, it in enumerate(model.display_items) if it.get("shot"))
            if model.flags(model.index(row, col)) & Qt.ItemIsEditable:
                editable.add(key)

        assert editable == {"roto"}


class TestWhatIsNotStoredAndNotEditable:

    def test_the_unsaved_flag_never_reaches_the_database(self, mock_db):
        """
        _modified was serialised into the row, so every shot ever edited came
        back "unsaved" for everyone, in every session, forever.
        """
        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        shot = Shot(shot_name="SH010", reel_episode="ReelA")
        shot._modified = True
        handler.write_shots([shot])

        assert _read(mock_db, "SH010")._modified is False

    def test_an_old_row_that_stored_the_flag_still_loads_clean(self):
        assert Shot.from_dict({"shot_name": "SH010", "_modified": True})._modified is False

    def test_plate_range_cannot_be_typed_over(self):
        model = ShotTableModel(user_role=["supervisor"])
        shot = Shot(shot_name="SH010", first_frame=1001, last_frame=1048)
        model.update_data([shot])
        col = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "plate_range")
        row = next(i for i, it in enumerate(model.display_items) if it.get("shot"))
        index = model.index(row, col)

        assert not (model.flags(index) & Qt.ItemIsEditable)
        assert model.setData(index, "5", Qt.ItemDataRole.EditRole) is False


class TestCoordinatorForceSave:

    def test_access_json_is_the_truth_for_a_coordinator(self):
        """The interface folds coordinator into supervisor; access must not."""
        assert not can_force_save(["coordinator"])
        assert can_force_save(["supervisor"])
