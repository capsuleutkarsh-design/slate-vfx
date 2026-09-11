"""
Production reporting.

Bid days and target dates were collected on every shot and never read. These
tests cover the numbers a coordinator is asked for each morning: who is loaded,
what is late, how far through the show is.
"""

from datetime import date

import pytest

from ut_vfx.core.domain.production_summary import build_summary
from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


TODAY = date(2026, 9, 9)


def _shot(name, reel="ReelA", status="WIP", target=None, artist=""):
    shot = Shot(shot_name=name, reel_episode=reel, status=status,
                assigned_artist=artist)
    if target:
        shot.target = target
    return shot


class TestEmptyAndTrivial:

    def test_no_shots(self):
        summary = build_summary([], today=TODAY)
        assert summary.total_shots == 0
        assert summary.percent_complete == 0.0
        assert summary.departments == []

    def test_none_entries_are_ignored(self):
        summary = build_summary([None, _shot("SH010")], today=TODAY)
        assert summary.total_shots == 1


class TestStatusRollup:

    def test_counts_by_status(self):
        shots = [_shot("SH010", status="WIP"),
                 _shot("SH020", status="WIP"),
                 _shot("SH030", status="APPROVED")]
        summary = build_summary(shots, today=TODAY)

        assert summary.status_counts["WIP"] == 2
        assert summary.status_counts["APPROVED"] == 1

    def test_percent_complete_counts_approved_shots(self):
        shots = [_shot("SH010", status="APPROVED"),
                 _shot("SH020", status="APPROVED"),
                 _shot("SH030", status="WIP"),
                 _shot("SH040", status="YTS")]
        summary = build_summary(shots, today=TODAY)
        assert summary.percent_complete == 50.0


class TestDepartmentLoad:

    def test_bid_days_are_totalled_per_department(self):
        a = _shot("SH010")
        a.dept("comp").artist = "Rahul"
        a.dept("comp").bid_days = 3.0
        a.dept("comp").status = "WIP"
        a.dept("roto").artist = "Priya"
        a.dept("roto").bid_days = 1.5
        a.dept("roto").status = "APPROVED"

        b = _shot("SH020")
        b.dept("comp").artist = "Rahul"
        b.dept("comp").bid_days = 2.0
        b.dept("comp").status = "YTS"

        summary = build_summary([a, b], today=TODAY)
        by_key = {d.key: d for d in summary.departments}

        assert by_key["comp"].shots == 2
        assert by_key["comp"].bid_days == 5.0
        assert by_key["comp"].in_progress == 1
        assert by_key["comp"].not_started == 1
        assert by_key["roto"].done == 1

    def test_departments_with_no_work_are_left_out(self):
        shot = _shot("SH010")
        shot.dept("comp").bid_days = 2.0
        shot.dept("comp").status = "WIP"

        summary = build_summary([shot], today=TODAY)
        keys = {d.key for d in summary.departments}

        assert "comp" in keys
        assert "matchmove" not in keys, "an untouched department was reported"

    def test_new_departments_are_reported_like_any_other(self):
        shot = _shot("SH010")
        shot.dept("matchmove").artist = "Vikram"
        shot.dept("matchmove").bid_days = 4.0
        shot.dept("matchmove").status = "WIP"

        summary = build_summary([shot], today=TODAY)
        by_key = {d.key: d for d in summary.departments}
        assert by_key["matchmove"].bid_days == 4.0

    def test_outstanding_days_exclude_finished_work(self):
        shot = _shot("SH010")
        shot.dept("comp").bid_days = 3.0
        shot.dept("comp").status = "APPROVED"
        shot.dept("roto").bid_days = 2.0
        shot.dept("roto").status = "WIP"

        summary = build_summary([shot], today=TODAY)
        assert summary.total_bid_days == 5.0
        assert summary.outstanding_bid_days == 2.0


class TestArtistLoad:

    def test_load_is_summed_across_shots_and_departments(self):
        a = _shot("SH010")
        a.dept("comp").artist = "Rahul"
        a.dept("comp").bid_days = 3.0
        a.dept("comp").status = "WIP"

        b = _shot("SH020")
        b.dept("comp").artist = "Rahul"
        b.dept("comp").bid_days = 2.0
        b.dept("comp").status = "WIP"
        b.dept("roto").artist = "Priya"
        b.dept("roto").bid_days = 1.0
        b.dept("roto").status = "WIP"

        summary = build_summary([a, b], today=TODAY)
        by_name = {x.name: x for x in summary.artists}

        assert by_name["Rahul"].shots == 2
        assert by_name["Rahul"].bid_days == 5.0
        assert by_name["Priya"].bid_days == 1.0

    def test_finished_work_does_not_count_towards_current_load(self):
        shot = _shot("SH010")
        shot.dept("comp").artist = "Rahul"
        shot.dept("comp").bid_days = 3.0
        shot.dept("comp").status = "APPROVED"

        summary = build_summary([shot], today=TODAY)
        rahul = summary.artists[0]
        assert rahul.bid_days == 3.0
        assert rahul.outstanding_days == 0.0

    def test_busiest_artist_comes_first(self):
        a = _shot("SH010")
        a.dept("comp").artist = "Rahul"
        a.dept("comp").bid_days = 8.0
        a.dept("comp").status = "WIP"
        a.dept("roto").artist = "Priya"
        a.dept("roto").bid_days = 1.0
        a.dept("roto").status = "WIP"

        summary = build_summary([a], today=TODAY)
        assert summary.artists[0].name == "Rahul"


class TestDates:

    def test_overdue_shots_are_listed_worst_first(self):
        a = _shot("SH010", target="2026-09-01", status="WIP")     # 8 days late
        b = _shot("SH020", target="2026-09-07", status="WIP")     # 2 days late
        c = _shot("SH030", target="2026-12-01", status="WIP")     # not due

        summary = build_summary([a, b, c], today=TODAY)

        assert [e.shot_name for e in summary.late] == ["SH010", "SH020"]
        assert summary.late[0].days_late == 8

    def test_approved_shots_are_never_late(self):
        shot = _shot("SH010", target="2026-01-01", status="APPROVED")
        summary = build_summary([shot], today=TODAY)
        assert summary.late == []

    def test_due_soon_window(self):
        soon = _shot("SH010", target="2026-09-12", status="WIP")   # 3 days out
        later = _shot("SH020", target="2026-10-30", status="WIP")

        summary = build_summary([soon, later], today=TODAY, due_within_days=7)
        assert [e.shot_name for e in summary.due_soon] == ["SH010"]

    @pytest.mark.parametrize("value", ["2026-09-01", "01-09-2026", "01/09/2026"])
    def test_common_date_formats_are_understood(self, value):
        shot = _shot("SH010", target=value, status="WIP")
        summary = build_summary([shot], today=TODAY)
        assert len(summary.late) == 1

    def test_an_unreadable_date_is_ignored_not_crashed_on(self):
        shot = _shot("SH010", target="whenever", status="WIP")
        summary = build_summary([shot], today=TODAY)
        assert summary.late == []
        assert summary.due_soon == []


class TestUnassigned:

    def test_shots_with_nobody_on_them_are_flagged(self):
        assigned = _shot("SH010")
        assigned.dept("comp").artist = "Rahul"
        assigned.dept("comp").status = "WIP"

        orphan = _shot("SH020")

        summary = build_summary([assigned, orphan], today=TODAY)
        assert summary.unassigned == ["SH020"]

    def test_a_shot_level_artist_counts_as_assigned(self):
        shot = _shot("SH010", artist="Rahul")
        summary = build_summary([shot], today=TODAY)
        assert summary.unassigned == []


class TestSummaryDialog:
    """The dialog must render whatever the roll-up produces, including nothing."""

    def _shots(self):
        a = _shot("SH010", target="2026-09-01", status="WIP", artist="Rahul")
        a.dept("comp").artist = "Rahul"
        a.dept("comp").bid_days = 3.0
        a.dept("comp").status = "WIP"
        a.dept("matchmove").artist = "Vikram"
        a.dept("matchmove").bid_days = 2.0
        a.dept("matchmove").status = "YTS"

        b = _shot("SH020", status="APPROVED")
        b.dept("comp").artist = "Priya"
        b.dept("comp").bid_days = 1.0
        b.dept("comp").status = "APPROVED"

        c = _shot("SH030", status="YTS")   # nobody on it
        return [a, b, c]

    def test_dialog_builds(self, qtbot):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.production_summary_dialog import (
            ProductionSummaryDialog,
        )

        dialog = ProductionSummaryDialog(self._shots(), project_name="Test Show",
                                         today=TODAY)
        qtbot.addWidget(dialog)

        summary = dialog.summary
        assert summary.total_shots == 3
        assert summary.percent_complete == pytest.approx(33.3, abs=0.1)
        assert [e.shot_name for e in summary.late] == ["SH010"]
        assert summary.unassigned == ["SH030"]

    def test_dialog_handles_an_empty_project(self, qtbot):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.production_summary_dialog import (
            ProductionSummaryDialog,
        )

        dialog = ProductionSummaryDialog([], project_name="Empty", today=TODAY)
        qtbot.addWidget(dialog)
        assert dialog.summary.total_shots == 0
