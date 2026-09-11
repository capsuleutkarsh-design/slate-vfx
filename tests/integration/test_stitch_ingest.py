"""
Ingesting a shot the client split across two folders.

The client sends SH010_A and SH010_B: one shot, one comp, one bid, one
delivery - but two folders. Without help the ingest makes two shots out of it,
and every later stage doubles up.

The reference behaviour is a stitch delivered as a *single* folder holding two
sequences, which already lands correctly: one shot, both plates side by side in
one scan version. A confirmed stitch must produce exactly that.
"""

import pytest

from ut_vfx.core.domain.shot_registry import register_ingested_shots
from ut_vfx.core.domain.stitch_detect import survey_source, apply_groups
from ut_vfx.core.domain.workers.structure import FolderCreationWorker
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler


TEMPLATE = (
    ["01_Scan", "05_Reels"], [], [],
    ["01_Scan", "07_Comp/Script", "07_Comp/Output", "08_Output/EXR"],
)


def _run(source, target, mock_db, project="PRJ", stitch_mapping=None):
    import ut_vfx.core.domain.workers.structure as structure_module
    structure_module.database_manager = mock_db

    worker = FolderCreationWorker(
        target_dir=target, source_scan_path=source, project_name=project,
        template_data=TEMPLATE, fast_mode=True, format_mapping={},
        stitch_mapping=stitch_mapping,
    )
    logs = []
    worker.log_signal.connect(logs.append)
    worker.run()
    return worker, logs


def _make_plate(root, reel, folder, basename, frames=(1, 2)):
    d = root / reel / folder
    d.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (d / f"{basename}.{frame:04d}.exr").write_bytes(b"x" * 8)
    return d


def _files(root):
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


class TestSurveyingTheDrive:
    """What the coordinator is shown before anything moves."""

    def test_the_survey_finds_the_stitch_on_a_real_tree(self, temp_vfx_root):
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")
        _make_plate(drive, "ReelA", "SH020", "SH020")

        groups = survey_source(drive)

        assert len(groups) == 1
        assert groups[0].shot_name == "SH010"
        assert groups[0].parts == ["SH010_A", "SH010_B"]
        assert groups[0].reel == "ReelA"

    def test_the_survey_reports_the_reel_the_ingest_would_use(self, temp_vfx_root):
        """A forced target reel has to be reflected, or the keys will not match."""
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")

        groups = survey_source(drive, target_reel_name="Reel_99")

        assert [g.reel for g in groups] == ["Reel_99"]

    def test_an_ordinary_delivery_asks_nothing(self, temp_vfx_root):
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010", "SH010")
        _make_plate(drive, "ReelA", "SH020", "SH020")

        assert survey_source(drive) == []


class TestConfirmedStitch:
    """The coordinator said yes: the parts become one shot."""

    def test_both_parts_land_in_one_shot_and_one_scan_version(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")

        mapping = apply_groups([], survey_source(drive))
        worker, _ = _run(drive, target, mock_db, stitch_mapping=mapping)

        reel = target / "PRJ" / "05_Reels" / "ReelA"
        assert (reel / "SH010").exists()
        assert not (reel / "SH010_A").exists()
        assert not (reel / "SH010_B").exists()

        # One delivery, so one scan version holding both plates.
        versions = sorted(p.name for p in (reel / "SH010" / "01_Scan").iterdir()
                          if p.is_dir())
        assert versions == ["v001"]

        moved = _files(reel / "SH010" / "01_Scan" / "v001")
        assert "EXR/SH010_A.0001.exr" in moved
        assert "EXR/SH010_B.0001.exr" in moved
        assert len(moved) == 4

    def test_it_counts_and_registers_as_a_single_shot(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")

        mapping = apply_groups([], survey_source(drive))
        worker, _ = _run(drive, target, mock_db, stitch_mapping=mapping)

        assert worker.shots_count == 1
        assert [s["shot"] for s in worker.ingested_shots] == ["SH010"]

        result = register_ingested_shots("PRJ", worker.ingested_shots, db=mock_db)
        assert result.created == ["SH010"]

        handler = SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor")
        assert [s.shot_name for s in handler.read_shots()] == ["SH010"]

    def test_a_three_part_stitch_is_still_one_shot(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        for part in ("A", "B", "C"):
            _make_plate(drive, "ReelA", f"SH010_{part}", f"SH010_{part}")

        mapping = apply_groups([], survey_source(drive))
        worker, _ = _run(drive, target, mock_db, stitch_mapping=mapping)

        shot = target / "PRJ" / "05_Reels" / "ReelA" / "SH010"
        assert worker.shots_count == 1
        assert sorted(p.name for p in (shot / "01_Scan").iterdir()
                      if p.is_dir()) == ["v001"]
        assert len(_files(shot / "01_Scan" / "v001")) == 6

    def test_the_ordinary_shots_around_it_are_untouched(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")
        _make_plate(drive, "ReelA", "SH020", "SH020")

        mapping = apply_groups([], survey_source(drive))
        worker, _ = _run(drive, target, mock_db, stitch_mapping=mapping)

        reel = target / "PRJ" / "05_Reels" / "ReelA"
        assert (reel / "SH020" / "01_Scan" / "v001" / "EXR" /
                "SH020.0001.exr").exists()
        assert worker.shots_count == 2


class TestRejectedStitch:
    """The coordinator said no: nothing changes."""

    def test_without_a_mapping_the_parts_stay_separate_shots(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")

        worker, _ = _run(drive, target, mock_db, stitch_mapping={})

        reel = target / "PRJ" / "05_Reels" / "ReelA"
        assert (reel / "SH010_A").exists()
        assert (reel / "SH010_B").exists()
        assert not (reel / "SH010").exists()
        assert worker.shots_count == 2


class TestReelsStayApart:
    """Accepting a stitch in one reel says nothing about another reel."""

    def test_only_the_accepted_reel_is_merged(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        for reel in ("ReelA", "ReelB"):
            _make_plate(drive, reel, "SH010_A", f"{reel}_SH010_A")
            _make_plate(drive, reel, "SH010_B", f"{reel}_SH010_B")

        groups = survey_source(drive)
        accepted = [g for g in groups if g.reel == "ReelA"]
        worker, _ = _run(drive, target, mock_db,
                         stitch_mapping=apply_groups([], accepted))

        reels = target / "PRJ" / "05_Reels"
        assert (reels / "ReelA" / "SH010").exists()
        assert not (reels / "ReelA" / "SH010_A").exists()
        # ReelB was rejected, so its folders stay as two shots.
        assert (reels / "ReelB" / "SH010_A").exists()
        assert (reels / "ReelB" / "SH010_B").exists()


class TestOneFolderStitchStillWorks:
    """The shape that already worked must not regress."""

    def test_two_sequences_in_one_folder_are_one_shot(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        folder = _make_plate(drive, "ReelA", "SH010", "SH010_partA")
        for frame in (1, 2):
            (folder / f"SH010_partB.{frame:04d}.exr").write_bytes(b"y" * 8)

        worker, _ = _run(drive, target, mock_db, stitch_mapping={})

        scan = target / "PRJ" / "05_Reels" / "ReelA" / "SH010" / "01_Scan"
        assert worker.shots_count == 1
        assert sorted(p.name for p in scan.iterdir() if p.is_dir()) == ["v001"]
        assert len(_files(scan / "v001")) == 4


class TestPartsWithClashingFileNames:
    """
    Some clients name both parts' frames identically.

    Merged into one scan version that is a filename-for-filename collision, and
    the ingest's collision rule would skip the second part - half the shot lost.
    """

    def _clashing_drive(self, temp_vfx_root):
        drive = temp_vfx_root / "Drive"
        for part in ("A", "B"):
            _make_plate(drive, "ReelA", f"SH010_{part}", "plate")
        return drive

    def test_no_frame_is_dropped(self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = self._clashing_drive(temp_vfx_root)

        mapping = apply_groups([], survey_source(drive))
        worker, _ = _run(drive, target, mock_db, stitch_mapping=mapping)

        version = (target / "PRJ" / "05_Reels" / "ReelA" / "SH010"
                   / "01_Scan" / "v001")
        assert worker.files_skipped == 0
        assert len(_files(version)) == 4

    def test_the_second_part_is_kept_in_its_own_folder(
            self, temp_vfx_root, mock_db):
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = self._clashing_drive(temp_vfx_root)

        mapping = apply_groups([], survey_source(drive))
        _run(drive, target, mock_db, stitch_mapping=mapping)

        version = (target / "PRJ" / "05_Reels" / "ReelA" / "SH010"
                   / "01_Scan" / "v001")
        assert "EXR/plate.0001.exr" in _files(version)
        assert "B/EXR/plate.0001.exr" in _files(version)

    def test_parts_with_different_names_still_sit_flat_together(
            self, temp_vfx_root, mock_db):
        """The common case must not gain a folder it does not need."""
        target = temp_vfx_root / "Projects"
        target.mkdir()
        drive = temp_vfx_root / "Drive"
        _make_plate(drive, "ReelA", "SH010_A", "SH010_A")
        _make_plate(drive, "ReelA", "SH010_B", "SH010_B")

        mapping = apply_groups([], survey_source(drive))
        _run(drive, target, mock_db, stitch_mapping=mapping)

        version = (target / "PRJ" / "05_Reels" / "ReelA" / "SH010"
                   / "01_Scan" / "v001")
        assert _files(version) == {
            "EXR/SH010_A.0001.exr", "EXR/SH010_A.0002.exr",
            "EXR/SH010_B.0001.exr", "EXR/SH010_B.0002.exr",
        }
