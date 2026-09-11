"""
Artists set their own status.

The dashboard used to be read-only for artists, so every "started" and "ready
for review" travelled through a coordinator. That made the coordinator a typist
and left the board permanently a day behind - and people stop trusting numbers
that are always yesterday's.

The way in is deliberately narrow: the status of a department row you are
personally named on, and nothing else.
"""

import pytest

from slate.core.domain.access import can_edit_dashboard, can_edit_own_status
from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel


PROJECT = "SELF_PRJ"
RAHUL = ["rahul", "Rahul"]
PRIYA = ["priya"]


def _shot(name="SH010", reel="ReelA"):
    shot = Shot(shot_name=name, reel_episode=reel, status="YTS")
    shot.dept("comp").artist = "Rahul"
    shot.dept("comp").status = "YTS"
    shot.dept("roto").artist = "Priya"
    shot.dept("roto").status = "WIP"
    return shot


@pytest.fixture
def seeded(mock_db):
    """A shot with comp on Rahul and roto on Priya."""
    supervisor = SQLiteHandler(PROJECT, db_manager=mock_db, user_role="supervisor")
    supervisor.write_shots([_shot()])
    return supervisor


def _artist(mock_db):
    return SQLiteHandler(PROJECT, db_manager=mock_db, user_role="artist")


class TestPermission:

    def test_an_artist_may_set_their_own_status_but_not_edit_freely(self):
        assert can_edit_dashboard(["artist"]) is False
        assert can_edit_own_status(["artist"]) is True

    def test_a_tester_may_not(self):
        assert can_edit_own_status(["tester"]) is False


class TestTheWritePath:
    """Checked in the handler, so skipping the interface does not widen it."""

    def test_an_artist_can_set_the_status_of_their_own_department(
            self, mock_db, seeded):
        artist = _artist(mock_db)
        shot = seeded.read_shots()[0]

        assert artist.update_department_status(
            "SH010", "ReelA", "comp", "WIP", shot.version,
            actor_identities=RAHUL,
        ) is True

        after = seeded.read_shots()[0]
        assert after.dept("comp").status == "WIP"

    def test_an_artist_cannot_touch_another_department(self, mock_db, seeded):
        artist = _artist(mock_db)
        shot = seeded.read_shots()[0]

        with pytest.raises(PermissionError):
            artist.update_department_status(
                "SH010", "ReelA", "roto", "APPROVED", shot.version,
                actor_identities=RAHUL,      # Rahul is on comp, not roto
            )

        assert seeded.read_shots()[0].dept("roto").status == "WIP"

    def test_an_artist_on_no_department_is_refused(self, mock_db, seeded):
        artist = _artist(mock_db)
        shot = seeded.read_shots()[0]

        with pytest.raises(PermissionError):
            artist.update_department_status(
                "SH010", "ReelA", "comp", "WIP", shot.version,
                actor_identities=["someone_else"],
            )

    def test_an_unknown_department_is_rejected(self, mock_db, seeded):
        artist = _artist(mock_db)
        shot = seeded.read_shots()[0]

        assert artist.update_department_status(
            "SH010", "ReelA", "not_a_department", "WIP", shot.version,
            actor_identities=RAHUL,
        ) is False

    def test_a_supervisor_does_not_need_to_be_assigned(self, mock_db, seeded):
        shot = seeded.read_shots()[0]

        assert seeded.update_department_status(
            "SH010", "ReelA", "roto", "APPROVED", shot.version,
            actor_identities=["nobody"],
        ) is True
        assert seeded.read_shots()[0].dept("roto").status == "APPROVED"

    def test_nothing_else_on_the_shot_moves(self, mock_db, seeded):
        artist = _artist(mock_db)
        shot = seeded.read_shots()[0]
        artist.update_department_status(
            "SH010", "ReelA", "comp", "WIP", shot.version, actor_identities=RAHUL,
        )

        after = seeded.read_shots()[0]
        assert after.status == "YTS"                       # shot status untouched
        assert after.dept("roto").status == "WIP"          # other department
        assert after.dept("comp").artist == "Rahul"        # assignment
        assert after.dept("comp").bid_days == 0.0          # bid

    def test_a_stale_edit_is_rejected(self, mock_db, seeded):
        from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import StaleDataError

        artist = _artist(mock_db)
        shot = seeded.read_shots()[0]

        artist.update_department_status(
            "SH010", "ReelA", "comp", "WIP", shot.version, actor_identities=RAHUL,
        )
        # A status the artist may set: the point here is the stale version,
        # not the verdict rule (which has its own tests).
        with pytest.raises(StaleDataError):
            artist.update_department_status(
                "SH010", "ReelA", "comp", "SENT FOR REVIEW", shot.version,
                actor_identities=RAHUL,
            )

    def test_it_is_refused_while_offline(self, mock_db, seeded, monkeypatch):
        from slate.core.domain.access import OfflineError
        from slate.gui.tabs.vfx_dashboard_pro.core import sqlite_handler

        monkeypatch.setattr(sqlite_handler, "is_offline_fallback", lambda: True)
        artist = _artist(mock_db)

        with pytest.raises(OfflineError):
            artist.update_department_status(
                "SH010", "ReelA", "comp", "WIP", 1, actor_identities=RAHUL,
            )


class TestWhichCellsAreOffered:
    """flags() and setData() must agree, or the grid offers a lie."""

    def _model(self, identities, role="artist"):
        return ShotTableModel(shots=[_shot()], user_role=role,
                              user_identities=identities)

    def _index(self, model, key):
        col = [c[0] for c in model.COLUMNS].index(key)
        return model.index(0, col)

    def test_an_artist_can_edit_their_own_department_cell(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        assert model.flags(self._index(model, "comp")) & Qt.ItemFlag.ItemIsEditable

    def test_an_artist_cannot_edit_another_department_cell(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        assert not (model.flags(self._index(model, "roto"))
                    & Qt.ItemFlag.ItemIsEditable)

    def test_an_artist_cannot_edit_the_shot_status(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        assert not (model.flags(self._index(model, "status"))
                    & Qt.ItemFlag.ItemIsEditable)

    def test_an_artist_cannot_edit_the_target_or_the_artist_column(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        for key in ("target", "artist", "priority", "sow"):
            assert not (model.flags(self._index(model, key))
                        & Qt.ItemFlag.ItemIsEditable), key

    def test_setdata_refuses_a_cell_that_is_not_theirs(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        assert model.setData(self._index(model, "roto"), "APPROVED",
                             Qt.ItemDataRole.EditRole) is False
        assert model.shots[0].dept("roto").status == "WIP"

    def test_setdata_accepts_their_own_cell(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        assert model.setData(self._index(model, "comp"), "WIP",
                             Qt.ItemDataRole.EditRole) is True
        assert model.shots[0].dept("comp").status == "WIP"

    def test_an_artist_edit_is_announced_for_immediate_saving(self, qtbot):
        """Artists have no Save button, so the edit must be written at once."""
        from PySide6.QtCore import Qt

        model = self._model(RAHUL)
        seen = []
        model.own_status_edited.connect(
            lambda shot, dept, status: seen.append((shot.shot_name, dept, status))
        )
        model.setData(self._index(model, "comp"), "WIP", Qt.ItemDataRole.EditRole)

        assert seen == [("SH010", "comp", "WIP")]

    def test_a_supervisor_edit_is_not_saved_immediately(self, qtbot):
        """They have a Save button; batching is deliberate."""
        from PySide6.QtCore import Qt

        model = self._model([], role="supervisor")
        seen = []
        model.own_status_edited.connect(lambda *a: seen.append(a))
        model.setData(self._index(model, "comp"), "WIP", Qt.ItemDataRole.EditRole)

        assert seen == []

    def test_an_unknown_person_gets_a_read_only_grid(self, qtbot):
        from PySide6.QtCore import Qt

        model = self._model(["nobody"])
        for key in ("comp", "roto", "status"):
            assert not (model.flags(self._index(model, key))
                        & Qt.ItemFlag.ItemIsEditable), key


class TestUnsavedChanges:
    """Closing the tab used to discard an afternoon with no prompt."""

    def _widget(self, qtbot, role="Supervisor"):
        from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "coord", "display_name": "Coordinator", "roles": [role],
        })
        qtbot.addWidget(widget)
        return widget

    def test_a_clean_board_has_nothing_pending(self, qtbot, mock_db):
        widget = self._widget(qtbot)
        widget.all_shots = [_shot()]

        assert widget.has_unsaved_changes() is False
        assert widget.unsaved_shots() == []

    def test_an_edited_shot_is_counted(self, qtbot, mock_db):
        widget = self._widget(qtbot)
        shot = _shot()
        shot._modified = True
        widget.all_shots = [shot, _shot("SH020")]

        assert widget.has_unsaved_changes() is True
        assert [s.shot_name for s in widget.unsaved_shots()] == ["SH010"]

    def test_the_indicator_shows_the_count(self, qtbot, mock_db):
        widget = self._widget(qtbot)
        first, second = _shot("SH010"), _shot("SH020")
        first._modified = second._modified = True
        widget.all_shots = [first, second]

        widget.update_unsaved_indicator()
        assert "2 unsaved" in widget.unsaved_label.text()

    def test_the_indicator_clears_when_nothing_is_pending(self, qtbot, mock_db):
        widget = self._widget(qtbot)
        widget.all_shots = [_shot()]

        widget.update_unsaved_indicator()
        assert widget.unsaved_label.text() == ""

    def test_closing_a_clean_board_is_not_interrupted(self, qtbot, mock_db):
        widget = self._widget(qtbot)
        widget.all_shots = [_shot()]

        assert widget.confirm_discarding_changes("close") is True

    def test_discarding_is_allowed_when_chosen(self, qtbot, mock_db, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as dw

        widget = self._widget(qtbot)
        shot = _shot()
        shot._modified = True
        widget.all_shots = [shot]

        monkeypatch.setattr(dw.QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard))
        assert widget.confirm_discarding_changes("close") is True

    def test_cancelling_stops_the_close(self, qtbot, mock_db, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as dw

        widget = self._widget(qtbot)
        shot = _shot()
        shot._modified = True
        widget.all_shots = [shot]

        monkeypatch.setattr(dw.QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
        assert widget.confirm_discarding_changes("close") is False

    def test_a_failed_save_does_not_become_a_silent_discard(
            self, qtbot, mock_db, monkeypatch):
        """Choosing Save must not proceed if the save did not work."""
        from PySide6.QtWidgets import QMessageBox
        from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as dw

        widget = self._widget(qtbot)
        shot = _shot()
        shot._modified = True
        widget.all_shots = [shot]

        monkeypatch.setattr(dw.QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save))
        # A save that changes nothing: the shot stays pending.
        monkeypatch.setattr(widget, "save_changes", lambda: None)

        assert widget.confirm_discarding_changes("close") is False
