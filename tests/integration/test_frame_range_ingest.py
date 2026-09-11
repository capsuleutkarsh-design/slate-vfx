"""
Recording how long a shot actually is.

The delivered plate is the only honest answer to "how many frames is this
shot?" - the number somebody types into the dashboard is an intention. The
ingest reads the real range off the frames as they land, so the dashboard, the
bid and the Olive timeline all work from the same figure without anyone going
to the drive.
"""

import pytest

from slate.core.domain.shot_registry import register_ingested_shots
from slate.core.workers.structure import FolderCreationWorker
from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


TEMPLATE = (
    ["01_Scan", "05_Reels"], [], [],
    ["01_Scan", "07_Comp/Script", "08_Deliver/EXR"],
)


def _run(source, target, mock_db, project="PRJ"):
    import slate.core.workers.structure as structure_module
    structure_module.database_manager = mock_db

    worker = FolderCreationWorker(
        target_dir=target, source_scan_path=source, project_name=project,
        template_data=TEMPLATE, fast_mode=True, format_mapping={},
    )
    worker.run()
    return worker


def _plate(root, reel, shot, first, last, ext="exr"):
    folder = root / reel / shot
    folder.mkdir(parents=True, exist_ok=True)
    for frame in range(first, last + 1):
        (folder / f"{shot}.{frame:04d}.{ext}").write_bytes(b"x" * 8)
    return folder


def _entry(worker, shot_name):
    return next(e for e in worker.ingested_shots if e["shot"] == shot_name)


class TestWhatTheIngestRecords:

    def test_the_real_first_and_last_frame(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _plate(drive, "ReelA", "SH010", 1001, 1048)

        entry = _entry(_run(drive, target, mock_db), "SH010")

        assert (entry["first_frame"], entry["last_frame"]) == (1001, 1048)

    def test_a_shot_numbered_from_one(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _plate(drive, "ReelA", "SH010", 1, 96)

        entry = _entry(_run(drive, target, mock_db), "SH010")

        assert (entry["first_frame"], entry["last_frame"]) == (1, 96)

    def test_each_shot_gets_its_own_range(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _plate(drive, "ReelA", "SH010", 1001, 1010)
        _plate(drive, "ReelA", "SH020", 2001, 2050)

        worker = _run(drive, target, mock_db)

        assert _entry(worker, "SH010")["last_frame"] == 1010
        assert _entry(worker, "SH020")["first_frame"] == 2001

    def test_a_lone_movie_does_not_collapse_the_range(self, temp_vfx_root, mock_db):
        """
        A single MOV is reported as a one-frame sequence.

        Letting that count would turn a 96-frame shot into a one-frame shot.
        """
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        folder = _plate(drive, "ReelA", "SH010", 1001, 1096)
        (folder / "SH010_ref.mov").write_bytes(b"x" * 8)

        entry = _entry(_run(drive, target, mock_db), "SH010")

        assert (entry["first_frame"], entry["last_frame"]) == (1001, 1096)

    def test_a_shot_delivered_only_as_a_movie_still_gets_something(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        folder = drive / "ReelA" / "SH010"
        folder.mkdir(parents=True)
        (folder / "SH010.mov").write_bytes(b"x" * 8)

        entry = _entry(_run(drive, target, mock_db), "SH010")

        # One frame is what a single file honestly reports; it is not wrong,
        # it just is not a sequence.
        assert entry.get("first_frame") == entry.get("last_frame")


class TestWhatReachesTheDashboard:

    def test_a_new_shot_is_stored_with_its_range(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _plate(drive, "ReelA", "SH010", 1001, 1048)

        worker = _run(drive, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        shot = handler.read_shots()[0]

        assert (shot.first_frame, shot.last_frame) == (1001, 1048)
        assert shot.frame_count == 48

    def test_a_re_delivered_plate_updates_the_range(self, temp_vfx_root, mock_db):
        """A re-graded plate can be a different length from the first one."""
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _plate(first, "ReelA", "SH010", 1001, 1010)
        worker = _run(first, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        second = temp_vfx_root / "Drive2"
        _plate(second, "ReelA", "SH010", 1001, 1080)
        worker2 = _run(second, target, mock_db)
        register_ingested_shots("PRJ", worker2.ingested_shots, db=mock_db)

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        shot = handler.read_shots()[0]

        assert shot.last_frame == 1080

    def test_the_typed_edit_length_is_left_alone(self, temp_vfx_root, mock_db):
        """The two numbers mean different things and must not overwrite."""
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _plate(drive, "ReelA", "SH010", 1001, 1048)

        worker = _run(drive, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        shot = handler.read_shots()[0]

        assert shot.edit_frames == 0.0


class TestHowItReads:

    def test_the_range_reads_plainly(self):
        assert Shot(first_frame=1001, last_frame=1048).frame_range_text == "1001-1048"

    def test_a_shot_with_no_range_shows_nothing(self):
        assert Shot().frame_range_text == ""
        assert Shot().frame_count == 0

    def test_a_range_survives_being_saved_and_read_back(self):
        shot = Shot(shot_name="SH010", first_frame=1001, last_frame=1048)

        restored = Shot.from_dict(shot.to_dict())

        assert (restored.first_frame, restored.last_frame) == (1001, 1048)

    def test_a_shot_saved_before_this_existed_still_loads(self):
        """Older rows have no frame range; they must not fail to load."""
        old_row = {"shot_name": "SH010", "status": "WIP", "edit_frames": 48.0}

        shot = Shot.from_dict(old_row)

        assert shot.first_frame == 0
        assert shot.frame_range_text == ""
