"""
The bridge between the two halves of the software.

Build & Ingest already works out every reel and shot on a client drive. These
tests check that a delivery lands in the dashboard on its own, and - the part
that matters most - that re-ingesting never disturbs work already booked
against a shot.
"""

import pytest

from ut_vfx.core.domain.shot_registry import (
    IngestedShot, register_ingested_shots, NEW_SHOT_STATUS,
)
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler


PROJECT = "BRIDGE_PRJ"


@pytest.fixture
def handler(mock_db):
    return SQLiteHandler(project_code=PROJECT, db_manager=mock_db,
                         user_id=1, user_role="supervisor")


def _delivery():
    return [
        {"reel": "ReelA", "shot": "SH010", "path": "/proj/05_Reels/ReelA/SH010"},
        {"reel": "ReelA", "shot": "SH020", "path": "/proj/05_Reels/ReelA/SH020"},
        {"reel": "ReelB", "shot": "SH030", "path": "/proj/05_Reels/ReelB/SH030"},
    ]


class TestIngestCreatesShots:

    def test_a_delivery_lands_in_the_dashboard(self, mock_db, handler):
        result = register_ingested_shots(
            project_code=PROJECT, shots=_delivery(),
            project_name="Bridge Project", db=mock_db,
        )

        assert result.ok, result.error
        assert sorted(result.created) == ["SH010", "SH020", "SH030"]

        shots = {s.shot_name: s for s in handler.read_shots()}
        assert set(shots) == {"SH010", "SH020", "SH030"}
        assert shots["SH010"].reel_episode == "ReelA"
        assert shots["SH030"].reel_episode == "ReelB"
        assert shots["SH010"].status == NEW_SHOT_STATUS

    def test_new_shots_know_their_department_folders(self, mock_db, handler):
        register_ingested_shots(project_code=PROJECT, shots=_delivery(),
                                db=mock_db)

        shot = next(s for s in handler.read_shots() if s.shot_name == "SH010")
        assert shot.folder_paths, "no folder paths recorded"
        # Folders come from the department registry, so every department the
        # studio tracks has somewhere to point at.
        assert "comp" in shot.folder_paths
        assert "matchmove" in shot.folder_paths
        assert "ReelA" in shot.folder_paths["comp"]
        assert "SH010" in shot.folder_paths["comp"]

    def test_the_project_is_created_if_it_does_not_exist(self, mock_db):
        result = register_ingested_shots(
            project_code=PROJECT, shots=_delivery(),
            project_name="Bridge Project", folder_base="/proj", db=mock_db,
        )
        assert result.project_created is True

        project = mock_db.get_tracking_project(PROJECT)
        assert project is not None

    def test_accepts_ingestedshot_objects_too(self, mock_db, handler):
        shots = [IngestedShot(reel="ReelA", shot="SH010")]
        result = register_ingested_shots(project_code=PROJECT, shots=shots,
                                         db=mock_db)
        assert result.created == ["SH010"]


class TestReIngestIsSafe:
    """
    A client re-delivers a shot, or someone re-runs the ingest. Neither may
    reset work the coordinator has already booked.
    """

    def test_existing_shots_are_left_completely_alone(self, mock_db, handler):
        register_ingested_shots(project_code=PROJECT, shots=_delivery(),
                                db=mock_db)

        # Coordinator does a day's work on SH010.
        shot = next(s for s in handler.read_shots() if s.shot_name == "SH010")
        shot.status = "WIP"
        shot.assigned_artist = "Rahul"
        shot.dept("comp").artist = "Rahul"
        shot.dept("comp").bid_days = 4.0
        shot.dept("matchmove").artist = "Vikram"
        handler.write_shots([shot])

        # The same drive is ingested again.
        result = register_ingested_shots(project_code=PROJECT, shots=_delivery(),
                                         db=mock_db)

        assert result.created == []
        assert sorted(result.already_present) == ["SH010", "SH020", "SH030"]

        after = next(s for s in handler.read_shots() if s.shot_name == "SH010")
        assert after.status == "WIP"
        assert after.assigned_artist == "Rahul"
        assert after.dept("comp").artist == "Rahul"
        assert after.dept("comp").bid_days == 4.0
        assert after.dept("matchmove").artist == "Vikram"

    def test_a_second_delivery_adds_only_the_new_shots(self, mock_db, handler):
        register_ingested_shots(project_code=PROJECT, shots=_delivery(),
                                db=mock_db)

        second = _delivery() + [{"reel": "ReelB", "shot": "SH040"}]
        result = register_ingested_shots(project_code=PROJECT, shots=second,
                                         db=mock_db)

        assert result.created == ["SH040"]
        assert len(handler.read_shots()) == 4

    def test_duplicate_rows_in_one_delivery_create_one_shot(self, mock_db, handler):
        doubled = _delivery() + _delivery()
        result = register_ingested_shots(project_code=PROJECT, shots=doubled,
                                         db=mock_db)

        assert sorted(result.created) == ["SH010", "SH020", "SH030"]
        assert len(handler.read_shots()) == 3


class TestEdgeCases:

    def test_no_shots_is_a_no_op(self, mock_db):
        result = register_ingested_shots(project_code=PROJECT, shots=[],
                                         db=mock_db)
        assert result.ok
        assert result.created == []

    def test_missing_project_code_is_reported(self, mock_db):
        result = register_ingested_shots(project_code="", shots=_delivery(),
                                         db=mock_db)
        assert not result.ok
        assert "project code" in result.error

    def test_a_database_failure_does_not_raise(self):
        class DeadDB:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise RuntimeError("database unavailable")
                return boom

        result = register_ingested_shots(project_code=PROJECT,
                                         shots=_delivery(), db=DeadDB())
        assert not result.ok
        assert result.created == []


class TestWorkerReportsShots:
    """FolderCreationWorker must hand over what it found."""

    def test_worker_records_reel_and_shot(self, temp_vfx_root, mock_db):
        from ut_vfx.core.workers.structure import FolderCreationWorker
        import ut_vfx.core.workers.structure as structure_module

        structure_module.database_manager = mock_db

        source = temp_vfx_root / "ClientDrive"
        for reel, shots in (("ReelA", ("SH010", "SH020")), ("ReelB", ("SH030",))):
            for shot in shots:
                d = source / reel / shot
                d.mkdir(parents=True)
                (d / f"{shot}.0001.exr").write_bytes(b"data")

        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker = FolderCreationWorker(
            target_dir=target, source_scan_path=source, project_name="PRJ",
            template_data=(["01_Scan", "05_Reels"], [], [],
                           ["01_Scan", "07_Comp", "08_Output"]),
            fast_mode=True, format_mapping={},
        )
        worker.run()

        found = {(e["reel"], e["shot"]) for e in worker.ingested_shots}
        assert found == {("ReelA", "SH010"), ("ReelA", "SH020"),
                         ("ReelB", "SH030")}


class TestTheProjectItselfReachesTheDashboard:
    """
    Shots reaching the database is not enough: the dashboard lists projects
    through ProjectManager, which used to throw away any project row missing
    project_number or excel_path - which is every project the ingest creates.
    A coordinator ingested a drive, opened the dashboard, and the project was
    not in the list. Nothing else in the dashboard matters if this fails.
    """

    def test_an_ingested_project_appears_in_the_project_list(
            self, temp_vfx_root, mock_db):
        from ut_vfx.core.domain.shot_registry import register_ingested_shots
        from ut_vfx.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectManager

        register_ingested_shots(
            "NEWPRJ",
            [{"reel": "ReelA", "shot": "SH010", "path": "", "scan_version": "v001"}],
            db=mock_db, project_name="New Project",
            folder_base=str(temp_vfx_root / "NEWPRJ"),
        )

        manager = ProjectManager()

        assert "NEWPRJ" in manager.projects, (
            "the ingest created the project but the dashboard cannot load it"
        )
        project = manager.projects["NEWPRJ"]
        assert project.folder_base == str(temp_vfx_root / "NEWPRJ")
        assert manager.get_folder_path("NEWPRJ", "scan", "ReelA", "SH010")
