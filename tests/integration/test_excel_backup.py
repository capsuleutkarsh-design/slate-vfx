"""
Excel is the studio's backup copy and syncs both ways:
edit the sheet and the software picks it up; edit the software and the sheet
is updated.

These tests drive a real .xlsx through ExcelHandler and check that every
department survives the trip in both directions, including the ones added
after the sheet's column layout was designed.
"""

import pytest

from slate.core.domain.access import can_edit_dashboard, can_use_excel
from slate.core.domain.departments import department_keys
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


class TestExcelRoles:
    """Excel import/export is for Coordinator, Lead and Supervisor."""

    @pytest.mark.parametrize("role", ["coordinator", "lead", "supervisor",
                                      "Coordinator", "LEAD"])
    def test_allowed_roles(self, role):
        assert can_use_excel([role]) is True

    @pytest.mark.parametrize("role", ["artist", "tester", ""])
    def test_blocked_roles(self, role):
        assert can_use_excel([role]) is False

    def test_coordinator_and_lead_can_edit_the_dashboard(self):
        """
        Write access used to be hardcoded to supervisor/developer/admin, so a
        coordinator - the person this tool is for - could not save anything.
        """
        assert can_edit_dashboard(["coordinator"]) is True
        assert can_edit_dashboard(["lead"]) is True
        assert can_edit_dashboard(["artist"]) is False

    def test_roles_are_matched_case_insensitively(self):
        assert can_edit_dashboard(["Coordinator"]) is True
        assert can_edit_dashboard(["SUPERVISOR"]) is True


class TestDefaultRoles:
    """The default role set must include the people who use the tool."""

    def test_coordinator_and_lead_exist_as_default_roles(self):
        import inspect
        from slate.core.domain.user_manager import UserManager

        source = inspect.getsource(UserManager._create_default_roles_sql)
        assert '"Coordinator"' in source
        assert '"Lead"' in source


class TestExcelRoundTrip:
    """A shot must survive software -> Excel -> software unchanged."""

    @staticmethod
    def _make_project(tmp_path, column_mapping, name="project.xlsx"):
        from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectConfig

        return ProjectConfig(
            code="PRJ",
            name="Test Project",
            project_number=1,
            excel_path=str(tmp_path / name),
            sheet_name="MASTER",
            header_row=1,
            data_start_row=3,
            column_mapping=column_mapping,
        )

    @pytest.fixture
    def sheet(self, tmp_path):
        """A project sheet with a column per department."""
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter

        headers = ["Shot Name", "Reel", "Status", "Artist", "Frames"]
        mapping = {
            "shot_name": "A", "reel": "B", "overall_status": "C",
            "assigned_artist": "D", "frames": "E",
        }
        col = 6
        for key in department_keys():
            for suffix, label in (("artist", "Artist"), ("status", "Status"),
                                  ("bid", "Bid")):
                headers.append(f"{key} {label}")
                mapping[f"{key}_{suffix}"] = get_column_letter(col)
                col += 1

        wb = Workbook()
        ws = wb.active
        ws.title = "MASTER"
        ws.append(headers)                 # row 1: headers
        ws.append([""] * len(headers))     # row 2: spacer
        path = tmp_path / "project.xlsx"
        wb.save(path)
        return path, mapping, tmp_path

    def test_every_department_survives_the_round_trip(self, sheet):
        from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler

        path, mapping, tmp_path = sheet
        project = self._make_project(tmp_path, mapping)

        shot = Shot(shot_name="SH010", reel_episode="REEL_01", status="WIP")
        shot.dept("comp").artist = "Rahul"
        shot.dept("comp").status = "WIP"
        shot.dept("matchmove").artist = "Vikram"
        shot.dept("matchmove").status = "APPROVED"
        shot.dept("deage").status = "RETAKE"
        shot.dept("ai").artist = "Sana"
        shot._row_idx = 3

        assert ExcelHandler(str(path), project).write_shots([shot])

        shots = ExcelHandler(str(path), project).read_shots()
        assert shots, "nothing was read back from the sheet"

        got = shots[0]
        assert got.shot_name == "SH010"
        assert got.dept("matchmove").artist == "Vikram"
        assert got.dept("matchmove").status == "APPROVED"
        assert got.dept("deage").status == "RETAKE"
        assert got.dept("ai").artist == "Sana"

    def test_sheet_reports_which_departments_it_tracks(self, tmp_path):
        """
        An older sheet has no columns for newer departments. The handler must
        say so, otherwise an Excel import writes blanks over work the sheet
        simply does not track.
        """
        from openpyxl import Workbook
        from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler

        wb = Workbook()
        ws = wb.active
        ws.title = "MASTER"
        ws.append(["Shot Name", "Status", "Comp Status"])
        ws.append(["", "", ""])
        ws.append(["SH010", "WIP", "APPROVED"])
        path = tmp_path / "old_sheet.xlsx"
        wb.save(path)

        project = self._make_project(
            tmp_path,
            {"shot_name": "A", "overall_status": "B", "comp_status": "C"},
            name="old_sheet.xlsx",
        )
        handler = ExcelHandler(str(path), project)

        tracked = handler.mapped_department_keys()
        assert "comp" in tracked
        assert "matchmove" not in tracked, (
            "sheet claims to track a department it has no columns for; an "
            "import would erase that work"
        )

        shots = handler.read_shots()
        assert shots
        assert shots[0].dept("comp").status == "APPROVED"

    def test_full_sheet_tracks_every_department(self, sheet):
        from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler

        path, mapping, tmp_path = sheet
        handler = ExcelHandler(str(path), self._make_project(tmp_path, mapping))

        tracked = handler.mapped_department_keys()
        for key in department_keys():
            assert key in tracked, f"{key} has no column in the generated sheet"


class TestExcelIsWriteOnly:
    """
    Excel is a passbook: the software writes to it and never reads from it
    automatically. Importing is a deliberate, confirmed action only.
    """

    def test_opening_a_project_never_imports_from_excel(self):
        import inspect
        from slate.gui.tabs.vfx_dashboard_pro.ui.components import (
            dashboard_project_mixin,
        )

        source = inspect.getsource(dashboard_project_mixin)
        assert "_sync_excel_to_database" not in source, (
            "opening a project still pulls from Excel; the sheet could "
            "overwrite the database"
        )

    def test_refresh_never_imports_from_excel(self):
        import inspect
        from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import (
            DashboardWidget,
        )

        source = inspect.getsource(DashboardWidget.check_for_updates)
        assert "_sync_excel_to_database" not in source, (
            "a modified sheet still triggers an automatic import"
        )

    def test_there_is_no_way_to_import_from_excel_at_all(self):
        """
        A passbook is written to, never read from. There used to be a manual
        import "for onboarding"; it was a loaded gun pointing at the database
        and it is gone - from the menu, the actions and the sync service.
        """
        from slate.gui.tabs.vfx_dashboard_pro.ui.components.dashboard_actions_mixin import (
            DashboardActionsMixin,
        )
        from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_sync_service import (
            DashboardSyncService,
        )

        assert not hasattr(DashboardActionsMixin, "import_from_excel_click")
        assert not hasattr(DashboardSyncService, "sync_excel_to_database")
        assert not hasattr(DashboardSyncService, "is_excel_newer_than_db")
        assert hasattr(DashboardActionsMixin, "export_to_excel_click")


class TestStaffDepartments:
    """Designations offered when adding a user match the tracked departments."""

    def test_every_tracked_department_can_be_a_designation(self):
        from slate.core.domain.departments import (
            load_departments, staff_department_names,
        )

        options = staff_department_names()
        for dept in load_departments():
            assert dept.name in options, (
                f"{dept.name} is a tracked department but cannot be assigned "
                f"as someone's designation"
            )

    def test_non_production_functions_are_offered_too(self):
        from slate.core.domain.departments import staff_department_names

        options = staff_department_names()
        for name in ("Production", "IT", "HR", "Admin"):
            assert name in options
