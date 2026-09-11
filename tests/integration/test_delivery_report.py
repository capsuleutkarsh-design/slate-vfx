"""
Catching a short delivery, and being able to prove what arrived.

The check for holes in a sequence existed and was never called, so a client
delivering 1001-1100 with 1043-1055 absent ingested as a clean success. The gap
surfaced when an artist opened the shot, by which point the conversation with
the client is much harder.
"""

import json

import pytest

from ut_vfx.core.domain.delivery_report import (
    build_report, frame_summary, write_report,
)
from ut_vfx.core.domain.workers.structure import FolderCreationWorker


TEMPLATE = (
    ["01_Frm Client", "01_Scan", "05_Reels"], [], [],
    ["01_Scan", "07_Comp/Script", "07_Comp/Output"],
)


def _run(source, target, mock_db, project="PRJ", dry_run=False):
    import ut_vfx.core.domain.workers.structure as structure_module
    structure_module.database_manager = mock_db

    worker = FolderCreationWorker(
        target_dir=target, source_scan_path=source, project_name=project,
        template_data=TEMPLATE, fast_mode=True, format_mapping={},
        dry_run=dry_run,
    )
    logs = []
    worker.log_signal.connect(logs.append)
    worker.run()
    return worker, logs


def _plate(root, reel, shot, frames):
    d = root / reel / shot
    d.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (d / f"{shot}.{frame:04d}.exr").write_bytes(b"x" * 8)
    return d


class TestFrameSummary:
    """Missing frames are read by people, so they are rendered as ranges."""

    def test_contiguous_frames_become_a_range(self):
        assert frame_summary([1043, 1044, 1045]) == "1043-1045"

    def test_separate_gaps_are_listed(self):
        assert frame_summary([1043, 1044, 1060]) == "1043-1044, 1060"

    def test_a_single_frame_stands_alone(self):
        assert frame_summary([1050]) == "1050"

    def test_nothing_missing_is_empty(self):
        assert frame_summary([]) == ""

    def test_a_very_broken_sequence_is_truncated(self):
        every_other = list(range(1000, 1100, 2))
        rendered = frame_summary(every_other, limit=5)
        assert "more)" in rendered


class TestMissingFrameDetection:

    def test_a_complete_sequence_reports_nothing_missing(self, temp_vfx_root, mock_db):
        source = temp_vfx_root / "Drive"
        _plate(source, "ReelA", "SH010", range(1001, 1011))
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run(source, target, mock_db)

        assert worker.incomplete_sequences == []
        assert worker.sequences_found, "no sequence was recorded at all"

    def test_a_hole_in_the_middle_is_caught(self, temp_vfx_root, mock_db):
        """1001-1010 delivered with 1004, 1005 and 1006 absent."""
        source = temp_vfx_root / "Drive"
        frames = [f for f in range(1001, 1011) if f not in (1004, 1005, 1006)]
        _plate(source, "ReelA", "SH010", frames)
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, logs = _run(source, target, mock_db)

        assert len(worker.incomplete_sequences) == 1
        entry = worker.incomplete_sequences[0]
        assert entry["missing"] == [1004, 1005, 1006]
        assert entry["shot"] == "SH010"

        # And it is visible in the log while the run is happening.
        assert any("MISSING 3" in line and "1004-1006" in line for line in logs), logs

    def test_a_single_loose_file_is_not_reported_as_short(
            self, temp_vfx_root, mock_db):
        """
        fileseq calls a standalone file a one-frame sequence. A single file
        cannot have gaps, and this used to raise an error internally.
        """
        source = temp_vfx_root / "Drive"
        d = source / "ReelA" / "SH010"
        d.mkdir(parents=True)
        (d / "reference.mov").write_bytes(b"m" * 8)
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run(source, target, mock_db)
        assert worker.incomplete_sequences == []

    def test_several_short_sequences_are_all_reported(self, temp_vfx_root, mock_db):
        source = temp_vfx_root / "Drive"
        _plate(source, "ReelA", "SH010", [1001, 1002, 1005])
        _plate(source, "ReelA", "SH020", [2001, 2004])
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run(source, target, mock_db)

        shots = {e["shot"] for e in worker.incomplete_sequences}
        assert shots == {"SH010", "SH020"}


class TestTheReport:

    def _report(self, temp_vfx_root, mock_db, frames):
        source = temp_vfx_root / "Drive"
        _plate(source, "ReelA", "SH010", frames)
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run(source, target, mock_db)
        return worker, build_report(worker, "PRJ", source), target

    def test_a_clean_delivery_says_so(self, temp_vfx_root, mock_db):
        _, report, _ = self._report(temp_vfx_root, mock_db, range(1001, 1006))

        assert report.is_clean is True
        assert "no problems found" in report.headline()
        assert report.missing_frame_count == 0

    def test_a_short_delivery_is_not_clean(self, temp_vfx_root, mock_db):
        _, report, _ = self._report(temp_vfx_root, mock_db, [1001, 1002, 1005])

        assert report.is_clean is False
        assert report.missing_frame_count == 2
        assert "short" in report.headline()

    def test_the_headline_can_be_pasted_into_an_email(self, temp_vfx_root, mock_db):
        _, report, _ = self._report(temp_vfx_root, mock_db, range(1001, 1006))

        headline = report.headline()
        assert "1 shot(s)" in headline
        assert "frame(s)" in headline

    def test_files_are_written_into_the_project(self, temp_vfx_root, mock_db):
        _, report, target = self._report(temp_vfx_root, mock_db, [1001, 1002, 1005])

        paths = write_report(report, target / "PRJ")
        assert paths is not None

        from pathlib import Path
        html_file = Path(paths["report"])
        manifest = Path(paths["manifest"])
        assert html_file.exists() and manifest.exists()

        # Filed under the client folder, where a coordinator looks.
        assert "01_Frm Client" in str(html_file)
        assert "_ingest_reports" in str(html_file)

    def test_the_report_names_the_missing_frames(self, temp_vfx_root, mock_db):
        _, report, target = self._report(temp_vfx_root, mock_db, [1001, 1002, 1005])
        paths = write_report(report, target / "PRJ")

        from pathlib import Path
        body = Path(paths["report"]).read_text(encoding="utf-8")
        assert "1003-1004" in body
        assert "Problems found" in body

    def test_the_manifest_is_machine_readable(self, temp_vfx_root, mock_db):
        _, report, target = self._report(temp_vfx_root, mock_db, [1001, 1002, 1005])
        paths = write_report(report, target / "PRJ")

        from pathlib import Path
        data = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
        assert data["project"] == "PRJ"
        assert data["totals"]["missing_frames"] == 2
        assert data["incomplete"][0]["missing"] == [1003, 1004]

    def test_a_dry_run_is_labelled_as_one(self, temp_vfx_root, mock_db):
        source = temp_vfx_root / "Drive"
        _plate(source, "ReelA", "SH010", range(1001, 1004))
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run(source, target, mock_db, dry_run=True)
        report = build_report(worker, "PRJ", source)

        assert report.dry_run is True
        paths = write_report(report, target / "PRJ")
        assert "dryrun_" in paths["report"]

    def test_a_report_that_cannot_be_written_does_not_raise(self, temp_vfx_root,
                                                            mock_db):
        """A failed report must never fail the ingest that produced it."""
        _, report, _ = self._report(temp_vfx_root, mock_db, range(1001, 1004))

        # A path that cannot be created.
        assert write_report(report, "\x00://nowhere") is None
