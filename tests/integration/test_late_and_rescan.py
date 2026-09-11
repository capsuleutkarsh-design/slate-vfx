"""
What happens after the first delivery.

Real shows do not arrive once. Scans come back re-graded, reels turn up weeks
apart, and the same shot gets delivered again with the same filenames. These
tests pin down what the software actually does in each case.
"""

import pytest

from ut_vfx.core.domain.shot_registry import register_ingested_shots
from ut_vfx.core.workers.structure import FolderCreationWorker
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler


TEMPLATE = (
    ["01_Scan", "05_Reels"], [], [],
    ["01_Scan", "07_Comp/Script", "07_Comp/Output", "08_Output/EXR"],
)


def _run(source, target, mock_db, project="PRJ", overwrite=False):
    import ut_vfx.core.workers.structure as structure_module
    structure_module.database_manager = mock_db

    worker = FolderCreationWorker(
        target_dir=target, source_scan_path=source, project_name=project,
        template_data=TEMPLATE, fast_mode=True, format_mapping={},
        overwrite=overwrite,
    )
    logs = []
    worker.log_signal.connect(logs.append)
    worker.run()
    return worker, logs


def _files(root):
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _make_shot(root, reel, shot, frames=(1, 2), ext="exr", tag=""):
    d = root / reel / shot
    d.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (d / f"{shot}.{frame:04d}.{ext}").write_bytes(b"x" * 8 + tag.encode())
    return d


class TestScanArrivesLater:
    """A reel or shot that turns up after the first delivery."""

    def test_a_new_reel_is_added_without_disturbing_the_old_one(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010")
        worker, _ = _run(first, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelB", "SH030")
        worker2, _ = _run(second, target, mock_db)
        result = register_ingested_shots("PRJ", worker2.ingested_shots, db=mock_db)

        reels = target / "PRJ" / "05_Reels"
        assert (reels / "ReelA" / "SH010").exists()
        assert (reels / "ReelB" / "SH030").exists()
        assert result.created == ["SH030"]

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        assert {s.shot_name for s in handler.read_shots()} == {"SH010", "SH030"}

    def test_a_new_shot_in_an_existing_reel_is_added(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010")
        worker, _ = _run(first, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH020")
        worker2, _ = _run(second, target, mock_db)
        result = register_ingested_shots("PRJ", worker2.ingested_shots, db=mock_db)

        reels = target / "PRJ" / "05_Reels" / "ReelA"
        assert (reels / "SH010").exists() and (reels / "SH020").exists()
        assert result.created == ["SH020"]

    def test_work_booked_on_the_first_delivery_survives_the_second(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010")
        worker, _ = _run(first, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        shot = handler.read_shots()[0]
        shot.status = "WIP"
        shot.dept("comp").artist = "Rahul"
        shot.dept("comp").bid_days = 4.0
        handler.write_shots([shot])

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH020")
        _make_shot(second, "ReelA", "SH010", frames=(3, 4))   # more of an old shot
        worker2, _ = _run(second, target, mock_db)
        register_ingested_shots("PRJ", worker2.ingested_shots, db=mock_db)

        after = next(s for s in handler.read_shots() if s.shot_name == "SH010")
        assert after.status == "WIP"
        assert after.dept("comp").artist == "Rahul"
        assert after.dept("comp").bid_days == 4.0


class TestRedeliveredScan:
    """
    The same shot delivered again. Every delivery is kept - the software
    versions the scan rather than skipping or overwriting it.
    """

    def test_new_frames_of_an_existing_shot_are_all_kept(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010", frames=(1, 2))
        _run(first, target, mock_db)

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH010", frames=(3, 4))
        _run(second, target, mock_db)

        landed = _files(target / "PRJ" / "05_Reels" / "ReelA" / "SH010")
        assert len(landed) == 4, f"expected all four frames, got {sorted(landed)}"

    def test_a_regrade_with_identical_filenames_is_kept_not_skipped(
            self, temp_vfx_root, mock_db):
        """
        This used to lose data: the re-graded plate collided filename-for-
        filename with the one already in the project, was skipped, and was
        left behind on the client drive.
        """
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010", tag="OLD")
        _run(first, target, mock_db)

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH010", tag="NEW")
        worker, _ = _run(second, target, mock_db)

        assert worker.files_skipped == 0
        assert worker.files_moved == 2
        assert _files(second) == set(), "a plate was left on the client drive"

        # Both grades are in the project, in different scan versions.
        landed = target / "PRJ" / "05_Reels" / "ReelA" / "SH010"
        contents = {p.read_bytes() for p in landed.rglob("*") if p.is_file()}
        assert any(b"OLD" in c for c in contents)
        assert any(b"NEW" in c for c in contents)


REAL_TEMPLATE = (
    ["01_Scan", "05_Reels"], [], [],
    ["01_Scan/EXR", "01_Scan/Mov", "07_Comp/Script", "08_Output/EXR"],
)


def _run_real(source, target, mock_db, project="PRJ"):
    """Ingest using a template shaped like the studio's real one."""
    import ut_vfx.core.workers.structure as structure_module
    structure_module.database_manager = mock_db

    worker = FolderCreationWorker(
        target_dir=target, source_scan_path=source, project_name=project,
        template_data=REAL_TEMPLATE, fast_mode=True, format_mapping={},
    )
    worker.run()
    return worker


class TestScanVersioning:
    """
    Every delivery of a shot gets its own scan version folder, whether it
    arrives in the same run or six weeks later.
    """

    def test_first_delivery_is_v001(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        source = temp_vfx_root / "Drive"
        _make_shot(source, "ReelA", "SH010")

        worker = _run_real(source, target, mock_db)

        landed = _files(target / "PRJ" / "05_Reels" / "ReelA" / "SH010")
        assert landed == {
            "01_Scan/v001/EXR/SH010.0001.exr",
            "01_Scan/v001/EXR/SH010.0002.exr",
        }, sorted(landed)
        assert worker.ingested_shots[0]["scan_version"] == "v001"

    def test_a_later_delivery_becomes_v002(self, temp_vfx_root, mock_db):
        """The version follows what is already in the project, not the run."""
        target = temp_vfx_root / "Projects"
        target.mkdir()

        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010", tag="OLD")
        _run_real(first, target, mock_db)

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH010", tag="NEW")
        worker = _run_real(second, target, mock_db)

        assert worker.files_skipped == 0, "a re-delivery was skipped again"
        assert worker.ingested_shots[0]["scan_version"] == "v002"

        landed = _files(target / "PRJ" / "05_Reels" / "ReelA" / "SH010")
        assert landed == {
            "01_Scan/v001/EXR/SH010.0001.exr",
            "01_Scan/v001/EXR/SH010.0002.exr",
            "01_Scan/v002/EXR/SH010.0001.exr",
            "01_Scan/v002/EXR/SH010.0002.exr",
        }, sorted(landed)

    def test_two_deliveries_in_one_run_get_different_versions(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        source = temp_vfx_root / "Drive"
        _make_shot(source, "ReelA", "SH010_ScanA", tag="A")
        _make_shot(source, "ReelA", "SH010_ScanB", tag="B")

        worker = _run_real(source, target, mock_db)

        versions = sorted(e["scan_version"] for e in worker.ingested_shots)
        assert versions == ["v001", "v002"]
        assert worker.files_skipped == 0

    def test_scans_never_land_in_the_delivery_folder(self, temp_vfx_root, mock_db):
        """08_Output is where work goes OUT, not where plates come in."""
        target = temp_vfx_root / "Projects"
        target.mkdir()
        source = temp_vfx_root / "Drive"
        _make_shot(source, "ReelA", "SH010")

        _run_real(source, target, mock_db)

        landed = _files(target / "PRJ" / "05_Reels" / "ReelA" / "SH010")
        assert not any(p.startswith("08_Output") for p in landed), sorted(landed)


class TestNewScanNotification:
    """People must find out a new scan arrived without checking the drive."""

    def _first_delivery(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        first = temp_vfx_root / "Drive1"
        _make_shot(first, "ReelA", "SH010")
        worker = _run_real(first, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)
        return target

    def test_scan_status_shows_the_new_version(self, temp_vfx_root, mock_db):
        target = self._first_delivery(temp_vfx_root, mock_db)
        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")

        assert handler.read_shots()[0].scan_status.startswith("v001")

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH010", tag="NEW")
        worker = _run_real(second, target, mock_db)
        result = register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        assert result.new_scans == ["SH010"]
        assert result.created == []

        after = handler.read_shots()[0]
        assert after.scan_status.startswith("v002"), after.scan_status

    def test_an_internal_note_records_the_arrival(self, temp_vfx_root, mock_db):
        target = self._first_delivery(temp_vfx_root, mock_db)

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH010", tag="NEW")
        worker = _run_real(second, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        notes = handler.read_shots()[0].feedback_internal
        assert any("New scan v002" in n.text for n in notes), \
            [n.text for n in notes]

    def test_flagging_a_new_scan_never_disturbs_booked_work(
            self, temp_vfx_root, mock_db):
        target = self._first_delivery(temp_vfx_root, mock_db)

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        shot = handler.read_shots()[0]
        shot.status = "WIP"
        shot.assigned_artist = "Rahul"
        shot.dept("comp").artist = "Rahul"
        shot.dept("comp").bid_days = 4.0
        shot.priority = 1
        handler.write_shots([shot])

        second = temp_vfx_root / "Drive2"
        _make_shot(second, "ReelA", "SH010", tag="NEW")
        worker = _run_real(second, target, mock_db)
        register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)

        after = handler.read_shots()[0]
        assert after.status == "WIP"
        assert after.assigned_artist == "Rahul"
        assert after.dept("comp").bid_days == 4.0
        assert after.priority == 1
        assert after.scan_status.startswith("v002")
