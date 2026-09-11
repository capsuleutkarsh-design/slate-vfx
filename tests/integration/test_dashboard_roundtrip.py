"""
End-to-end audit of the VFX dashboard data layer.

The dashboard has never been exercised against a real database round-trip.
These tests drive SQLiteHandler the way the UI does - write shots, read them
back, edit single fields - and assert that what comes out matches what went in.

No GUI, no event loop. Failures here are real defects, not test scaffolding.
"""

import pytest

from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import (
    Shot, DepartmentInfo, FeedbackEntry,
)
from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler


PROJECT = "AUDIT_PRJ"


@pytest.fixture
def handler(mock_db):
    """A supervisor-level handler bound to the isolated test database."""
    return SQLiteHandler(
        project_code=PROJECT,
        db_manager=mock_db,
        user_id=1,
        user_role="supervisor",
    )


def _sample_shot(name="SH010", **overrides):
    shot = Shot(
        shot_name=name,
        reel_episode="REEL_01",
        status="WIP",
        assigned_artist="Rahul",
        edit_frames=120,
        priority=2,
        sow="bg cleanup and screen replacement",
        comp_dept=DepartmentInfo(artist="Rahul", bid_days=3.0, status="WIP"),
        roto_dept=DepartmentInfo(artist="Priya", bid_days=1.5, status="APPROVED"),
        cg_dept=DepartmentInfo(artist="Amit", bid_days=5.0, status="YTS"),
    )
    for key, value in overrides.items():
        setattr(shot, key, value)
    return shot


class TestShotRoundTrip:
    """Write a shot, read it back, and check nothing was lost."""

    def test_shot_survives_a_write_read_cycle(self, handler):
        original = _sample_shot()
        assert handler.write_shots([original]) is True

        shots = handler.read_shots()
        assert len(shots) == 1, f"expected 1 shot back, got {len(shots)}"

        got = shots[0]
        assert got.shot_name == "SH010"
        assert got.reel_episode == "REEL_01"
        assert got.status == "WIP"
        assert got.assigned_artist == "Rahul"
        assert got.edit_frames == 120
        assert got.priority == 2
        assert got.sow == "bg cleanup and screen replacement"

    def test_department_data_survives_a_write_read_cycle(self, handler):
        """Departments are written to both data_json and tracking_tasks."""
        handler.write_shots([_sample_shot()])
        got = handler.read_shots()[0]

        assert got.comp_dept.artist == "Rahul"
        assert got.comp_dept.status == "WIP"
        assert got.comp_dept.bid_days == 3.0

        assert got.roto_dept.artist == "Priya"
        assert got.roto_dept.status == "APPROVED"
        assert got.roto_dept.bid_days == 1.5

        assert got.cg_dept.artist == "Amit"
        assert got.cg_dept.bid_days == 5.0

    def test_feedback_survives_a_write_read_cycle(self, handler):
        shot = _sample_shot()
        shot.feedback_client = [
            FeedbackEntry(date="2026-09-01", source="client",
                          text="too green", logged_by="coord")
        ]
        handler.write_shots([shot])

        got = handler.read_shots()[0]
        assert len(got.feedback_client) == 1
        assert got.feedback_client[0].text == "too green"
        assert got.feedback_client[0].date == "2026-09-01"

    def test_multiple_shots_round_trip(self, handler):
        shots = [_sample_shot(f"SH{n:03d}") for n in (10, 20, 30)]
        assert handler.write_shots(shots) is True

        names = sorted(s.shot_name for s in handler.read_shots())
        assert names == ["SH010", "SH020", "SH030"]


class TestGranularEdit:
    """update_shot_field is what inline table editing calls."""

    def test_editing_a_top_level_field_persists(self, handler):
        handler.write_shots([_sample_shot()])
        before = handler.read_shots()[0]

        assert handler.update_shot_field(
            "SH010", "status", "APPROVED", before.version
        ) is True

        after = handler.read_shots()[0]
        assert after.status == "APPROVED"

    def test_department_edit_via_batch_save_persists(self, handler):
        """
        This is the real inline-edit path: the table mutates the Shot in
        memory, then save_changes() calls write_shots(all_shots).
        """
        handler.write_shots([_sample_shot("SH010"), _sample_shot("SH020")])
        shots = handler.read_shots()

        target = next(s for s in shots if s.shot_name == "SH010")
        target.comp_dept.status = "APPROVED"
        assert handler.write_shots(shots) is True

        after = next(s for s in handler.read_shots() if s.shot_name == "SH010")
        assert after.comp_dept.status == "APPROVED", (
            "department edit did not survive the batch save"
        )

    def test_department_edit_via_dotted_field_persists(self, handler):
        """The documented granular path for a nested field."""
        handler.write_shots([_sample_shot()])
        before = handler.read_shots()[0]

        assert handler.update_shot_field(
            "SH010", "comp_dept.status", "APPROVED", before.version
        ) is True

        after = handler.read_shots()[0]
        assert after.comp_dept.status == "APPROVED"

    def test_unknown_field_name_is_rejected(self, handler):
        """
        An unrecognised field name used to be written as a junk top-level key
        in data_json and reported as a successful save.
        """
        handler.write_shots([_sample_shot()])
        before = handler.read_shots()[0]

        result = handler.update_shot_field(
            "SH010", "comp_status", "APPROVED", before.version
        )
        assert result is False, (
            "unknown field reported success; the edit went nowhere"
        )

    def test_stale_edit_is_rejected(self, handler):
        """Two coordinators editing the same shot must not silently clobber."""
        from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import StaleDataError

        handler.write_shots([_sample_shot()])
        current = handler.read_shots()[0]

        handler.update_shot_field("SH010", "status", "APPROVED", current.version)

        with pytest.raises(StaleDataError):
            handler.update_shot_field("SH010", "status", "RETAKE", current.version)


class TestPermissions:
    """Artists must not be able to write."""

    def test_artist_cannot_write(self, mock_db):
        artist_handler = SQLiteHandler(
            project_code=PROJECT, db_manager=mock_db,
            user_id=2, user_role="artist",
        )
        with pytest.raises(Exception):
            artist_handler.write_shots([_sample_shot()])


class TestArtistScoping:
    """
    Artists read the dashboard. Scoping used to match only the shot-level
    'assigned_artist' (effectively the comp lead), so an artist assigned to
    roto/prep/CG saw an empty screen.
    """

    @staticmethod
    def _scope(shots, identities):
        """Mirror of DashboardWidget._filter_shots_for_current_user."""
        out = []
        for shot in shots:
            names = shot.get_all_artists()
            if any(str(n or "").strip().lower() in identities for n in names):
                out.append(shot)
        return out

    def test_department_artist_sees_their_shots(self):
        shot = _sample_shot()          # comp=Rahul, roto=Priya, cg=Amit
        # Priya is only on roto, Amit only on CG.
        assert self._scope([shot], {"priya"}) == [shot]
        assert self._scope([shot], {"amit"}) == [shot]
        assert self._scope([shot], {"rahul"}) == [shot]

    def test_unrelated_artist_sees_nothing(self):
        shot = _sample_shot()
        assert self._scope([shot], {"someone_else"}) == []


class TestDashboardWidgetOpens:
    """
    The widget has never been verified to construct. If this fails, nothing
    else in the dashboard matters.
    """

    def test_widget_constructs_for_a_coordinator(self, qtbot, mock_db):
        from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "coord1",
            "display_name": "Coordinator One",
            "roles": ["Supervisor"],
        })
        qtbot.addWidget(widget)

        assert widget.table is not None
        assert widget.table_model is not None
        assert widget.user_role == "supervisor"
        assert widget._user_can_edit() is True

    def test_widget_constructs_for_an_artist_readonly(self, qtbot, mock_db):
        from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "priya",
            "display_name": "Priya",
            "roles": ["Artist"],
        })
        qtbot.addWidget(widget)

        assert widget._user_can_edit() is False
        assert widget._is_artist_scope() is True


class TestStatusMirroring:
    """
    Changing a shot's overall status also sets its comp status. That mirroring
    used to write into a legacy key the reader now ignores.
    """

    def test_status_change_mirrors_into_comp(self, handler):
        handler.write_shots([_sample_shot()])
        before = handler.read_shots()[0]

        handler.update_shot_field("SH010", "status", "APPROVED", before.version)

        after = handler.read_shots()[0]
        assert after.status == "APPROVED"
        assert after.dept("comp").status == "APPROVED"

    def test_artist_change_mirrors_into_comp(self, handler):
        handler.write_shots([_sample_shot()])
        before = handler.read_shots()[0]

        handler.update_shot_field("SH010", "assigned_artist", "Vikram", before.version)

        after = handler.read_shots()[0]
        assert after.assigned_artist == "Vikram"
        assert after.dept("comp").artist == "Vikram"

    def test_new_departments_survive_a_full_save_cycle(self, handler):
        """matchmove/deage/ai must persist like any other department."""
        shot = _sample_shot()
        shot.dept("matchmove").artist = "Vikram"
        shot.dept("matchmove").status = "WIP"
        shot.dept("deage").status = "RETAKE"
        shot.dept("ai").bid_days = 2.0
        handler.write_shots([shot])

        after = handler.read_shots()[0]
        assert after.dept("matchmove").artist == "Vikram"
        assert after.dept("matchmove").status == "WIP"
        assert after.dept("deage").status == "RETAKE"
        assert after.dept("ai").bid_days == 2.0


class TestOperationalWiring:
    """The new pieces must actually be reachable from the real widget."""

    def _widget(self, qtbot, role):
        from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "u1", "display_name": "User One", "roles": [role],
        })
        qtbot.addWidget(widget)
        return widget

    def test_coordinator_gets_the_editing_tools(self, qtbot, mock_db):
        widget = self._widget(qtbot, "Coordinator")

        assert widget._user_can_edit() is True
        assert hasattr(widget, "add_shots_btn"), "no Add Shots button"
        assert widget.save_btn.isEnabled() is True

    def test_artist_gets_a_read_only_dashboard(self, qtbot, mock_db):
        widget = self._widget(qtbot, "Artist")

        assert widget._user_can_edit() is False
        assert not hasattr(widget, "add_shots_btn")
        assert widget.save_btn.isEnabled() is False

    def test_actions_are_present(self, qtbot, mock_db):
        widget = self._widget(qtbot, "Supervisor")

        for action in ("add_shots_click", "production_summary_click",
                       "export_to_excel_click"):
            assert callable(getattr(widget, action, None)), f"{action} missing"

    def test_table_sorting_is_switched_on(self, qtbot, mock_db):
        widget = self._widget(qtbot, "Supervisor")

        assert widget.table.isSortingEnabled() is True
        assert widget.table.horizontalHeader().isSortIndicatorShown() is True
