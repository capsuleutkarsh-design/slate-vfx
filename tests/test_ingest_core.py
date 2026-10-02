"""
Build & Ingest's engine: the survey, the worker, the report, retry and lock.

Every test runs the real code on a scratch drive under tmp_path (ING-003 ...
ING-079). Nothing here touches a studio path.
"""

import json
import os
import time
from pathlib import Path

import pytest

from slate.core.domain import ingest_survey as isv
from slate.core.domain.delivery_report import (
    _render_html, _report_dir, build_report, dry_run_dir, write_report,
)
from slate.core.domain.ingest_lock import IngestLock, IngestLocked, clear_lock, lock_info
from slate.core.domain.ingest_retry import latest_manifest, pending_failures, retry_failures
from slate.core.domain.stitch_detect import apply_groups, find_stitch_groups
from slate.core.workers import structure
from slate.core.workers.structure import COPY, MOVE, FolderCreationWorker

TEMPLATE = (["01_Frm Client", "05_Reels"], [], [], ["01_Scan", "07_Comp", "08_Output"])


@pytest.fixture
def db(mock_db, monkeypatch):
    monkeypatch.setattr(structure, "database_manager", mock_db)
    return mock_db


def _plates(root: Path, rel: str, frames=range(1001, 1005), name=None, ext="exr", body=b"exr"):
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    base = name or Path(rel).name
    for f in frames:
        (folder / f"{base}.{f:04d}.{ext}").write_bytes(body)
    return folder


def _files(root: Path):
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _run(source, target, operation=COPY, **kw):
    worker = FolderCreationWorker(target_dir=target, source_scan_path=source, project_name="PRJ",
                                  template_data=TEMPLATE, fast_mode=True, format_mapping={},
                                  operation=operation, **kw)
    logs = []
    worker.log_signal.connect(logs.append)
    worker.run()
    return worker, logs


def _drive(tmp_path):
    drive = tmp_path / "drive_A"
    _plates(drive, "REEL_01/SH_010")
    _plates(drive, "REEL_01/SH_020")
    _plates(drive, "REEL_02/SH_030")
    return drive


# ------------------------------------------------------------------ copy / move
def test_copy_is_the_default_and_leaves_the_client_drive_alone(tmp_path, db):
    drive = _drive(tmp_path)
    before = _files(drive)
    worker, _ = _run(drive, tmp_path / "Projects")
    assert worker.operation == COPY and worker.outcome == "completed"
    assert _files(drive) == before
    assert "REEL_01/SH_010/01_Scan/v001/EXR/SH_010.1001.exr" in _files(tmp_path / "Projects/PRJ/05_Reels")


def test_move_empties_the_drive(tmp_path, db):
    drive = _drive(tmp_path)
    worker, _ = _run(drive, tmp_path / "Projects", operation=MOVE)
    assert _files(drive) == set()
    assert worker.files_moved == 12


def test_a_project_file_is_never_deleted_before_its_replacement_is_safe(tmp_path, db, monkeypatch):
    """ING-029: 'Overwrite Existing' deleted the project copy first; it is gone."""
    drive = _drive(tmp_path)
    deleted = []
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: deleted.append(self))
    _run(drive, tmp_path / "Projects", overwrite=True)
    assert not [p for p in deleted if "Projects" in str(p)]


# ------------------------------------------------------------------ stop / pause
def test_a_stopped_ingest_says_so_and_stops_between_frames(tmp_path, db):
    drive = tmp_path / "drive"
    _plates(drive, "REEL_01/SH_010", frames=range(1001, 1501))
    worker = FolderCreationWorker(target_dir=tmp_path / "P", source_scan_path=drive, project_name="PRJ",
                                  template_data=TEMPLATE, fast_mode=True, operation=MOVE)
    real = worker._transfer

    def stop_after_ten(*a, **k):
        status = real(*a, **k)
        if worker.files_moved >= 10:
            worker.stop()
        return status

    worker._transfer = stop_after_ten
    finished = []
    worker.finished_signal.connect(lambda *args: finished.append(args))
    worker.run()

    assert worker.outcome == "stopped" and worker.cancelled
    assert worker.files_moved <= 11                     # ING-012: not the whole sequence
    assert len(_files(drive)) >= 489                    # the rest is still on the drive
    assert finished[0][0] is False                      # never reported as a success
    report = build_report(worker, "PRJ", drive)
    assert report.status == "stopped" and not report.is_clean
    assert "no problems" not in report.headline() and "STOPPED" in report.headline()


def test_pause_says_paused(tmp_path, db, qapp):
    worker = FolderCreationWorker(target_dir=tmp_path, dry_run=True)
    states = []
    worker.state_signal.connect(states.append)
    worker.pause()
    worker.resume()
    assert states == ["paused", "running"]


def test_progress_counts_files(tmp_path, db):
    drive = _drive(tmp_path)
    worker = FolderCreationWorker(target_dir=tmp_path / "P", source_scan_path=drive, project_name="PRJ",
                                  template_data=TEMPLATE, fast_mode=True)
    seen = []
    worker.progress_signal.connect(lambda pct, text: seen.append(text))
    worker.run()
    assert any("of 12 files" in t for t in seen)


# ------------------------------------------------------------------ sequences
def test_mixed_separators_are_not_a_negative_frame_sequence(tmp_path, db):
    """ING-006: plate-1003 was read as frame -1003, a short delivery of 2,006 frames."""
    shot = tmp_path / "drive" / "REEL_02" / "SH_090"
    shot.mkdir(parents=True)
    for name in ("plate_1001.exr", "plate.1002.exr", "plate-1003.exr", "plate1004.exr"):
        (shot / name).write_bytes(b"x")
    worker, _ = _run(tmp_path / "drive", tmp_path / "P")
    assert worker.incomplete_sequences == []
    assert all((s.get("start") or 0) >= 0 for s in worker.sequences_found)
    entry = worker.ingested_shots[0]
    assert entry.get("first_frame", 0) >= 0


def test_one_log_line_per_sequence(tmp_path, db):
    """ING-050 / ING-073: a 2,000-frame plate is one line, named like the report."""
    drive = tmp_path / "drive"
    _plates(drive, "REEL_01/SH_010", frames=range(1001, 1101), name="SH_010_plate")
    worker, logs = _run(drive, tmp_path / "P")
    seq_lines = [l for l in logs if "SH_010_plate" in l]
    assert len(seq_lines) == 1
    assert "[1001-1100]" in seq_lines[0] and ". (" not in seq_lines[0]
    assert len(logs) < 20


def test_report_counts_frames_of_sequences_only(tmp_path, db):
    """ING-075: a MOV is not a frame; started_at is filled."""
    drive = tmp_path / "drive"
    shot = _plates(drive, "REEL_01/SH_010", frames=range(1001, 1101))
    (shot / "ref.mov").write_bytes(b"m")
    worker, _ = _run(drive, tmp_path / "P")
    report = build_report(worker, "PRJ", drive)
    assert report.total_frames == 100 and report.single_files == 1
    assert report.started_at


# ------------------------------------------------------------------ the survey
def test_the_reel_is_the_reel_folder_not_the_parent(tmp_path):
    drive = tmp_path / "drive"
    _plates(drive, "REEL_03/seq_A/sub/SH_100")
    _plates(drive, "ReelA/SH_010")
    survey = isv.survey_drive(drive)
    reels = {s.name: s.reel for s in survey.shots}
    assert reels["SH_100"] == "REEL_03"
    assert reels["SH_010"] == "ReelA"


def test_loose_documents_are_documents_not_a_shot(tmp_path, db):
    """ING-016: readme.txt at the root became a shot named after the drive."""
    drive = _drive(tmp_path)
    (drive / "readme.txt").write_text("hi")
    (drive / "client_notes.pdf").write_bytes(b"%PDF")
    worker, _ = _run(drive, tmp_path / "P")
    assert "drive_A" not in {e["shot"] for e in worker.ingested_shots}
    filed = _files(tmp_path / "P" / "PRJ" / "01_Frm Client")
    assert any(f.endswith("_docs/readme.txt") for f in filed)
    assert len(worker.documents_filed) == 2


def test_names_are_proposed_and_client_versions_kept(tmp_path):
    drive = tmp_path / "drive"
    _plates(drive, "REEL_02/sh 060 (client)", name="p")
    _plates(drive, "REEL_02/SH_040_v02", name="p")
    survey = isv.survey_drive(drive)
    by_source = {s.source_name: s for s in survey.shots}
    assert by_source["sh 060 (client)"].name == "SH_060"
    assert by_source["SH_040_v02"].name == "SH_040"
    assert by_source["SH_040_v02"].client_version == "v02"


def test_client_version_reaches_the_shot_record(tmp_path, db):
    drive = tmp_path / "drive"
    _plates(drive, "REEL_02/SH_040_v02", name="p")
    worker, _ = _run(drive, tmp_path / "P")
    assert worker.ingested_shots[0]["client_version"] == "v02"
    report = build_report(worker, "PRJ", drive)
    assert "v02" in _render_html(report)


def test_empty_folders_and_junk_are_reported(tmp_path, db):
    drive = _drive(tmp_path)
    (drive / "REEL_03" / "SH_110_empty").mkdir(parents=True)
    (drive / "REEL_01" / "SH_010" / "Thumbs.db").write_bytes(b"x")
    worker, _ = _run(drive, tmp_path / "P")
    ignored = build_report(worker, "PRJ", drive).to_dict()["ignored"]
    items = {(i["item"].replace("\\", "/"), i["reason"]) for i in ignored}
    assert ("REEL_03/SH_110_empty", "empty folder") in items
    assert ("REEL_01/SH_010/Thumbs.db", "system file") in items


def test_a_target_reel_never_merges_two_shots(tmp_path, db):
    """ING-027: REEL_A/SH_010 and REEL_B/SH_010 with Target Reel R9."""
    drive = tmp_path / "drive"
    _plates(drive, "REEL_A/SH_010", name="a")
    _plates(drive, "REEL_B/SH_010", name="b")
    worker, _ = _run(drive, tmp_path / "P", target_reel_name="R9")
    shots = sorted(e["shot"] for e in worker.ingested_shots)
    assert shots == ["REEL_A_SH_010", "REEL_B_SH_010"]
    assert worker.shots_count == 2


def test_rescans_are_never_offered_as_a_stitch(tmp_path):
    drive = tmp_path / "drive"
    _plates(drive, "REEL_01/SH_050_ScanA", name="p")
    _plates(drive, "REEL_01/SH_050_ScanB", name="p")
    assert isv.survey_drive(drive).stitch_groups == []


def test_stitch_detection_rules():
    assert find_stitch_groups(["EP01_SH010", "EP01_SH020"]) == []
    groups = find_stitch_groups(["SH010_v2", "SH010_FG", "SH010_BG"])
    assert [g.parts for g in groups] == [["SH010_BG", "SH010_FG"]]
    assert find_stitch_groups(["SH_020_final", "SH_020_temp"]) == []
    weak = find_stitch_groups(["SH010_1", "SH010_2"])
    assert weak and weak[0].confident is False


def test_stitch_detection_is_fast():
    names = [f"SH_{i:04d}" for i in range(1500)]
    started = time.perf_counter()
    find_stitch_groups(names)
    assert time.perf_counter() - started < 0.2


def test_the_survey_walks_once_and_feeds_the_run(tmp_path, db, monkeypatch):
    drive = _drive(tmp_path)
    survey = isv.survey_drive(drive)
    calls = []
    real = isv.survey_drive
    monkeypatch.setattr(structure, "survey_drive", lambda *a, **k: calls.append(1) or real(*a, **k))
    worker, _ = _run(drive, tmp_path / "P", survey=survey)
    assert calls == [] and worker.files_moved == 12


def test_a_drive_already_ingested_is_not_copied_again(tmp_path, db):
    drive = _drive(tmp_path)
    target = tmp_path / "P"
    _run(drive, target)
    survey = isv.survey_drive(drive)
    skipped = survey.mark_unchanged(target / "PRJ" / "05_Reels")
    assert skipped == 3 and all(s.skip and s.unchanged_from == "v001" for s in survey.shots)
    worker, _ = _run(drive, target, survey=survey)
    assert worker.files_moved == 0
    assert not (target / "PRJ/05_Reels/REEL_01/SH_010/01_Scan/v002").exists()


def test_a_regrade_with_the_same_sizes_is_not_mistaken_for_the_same_files(tmp_path, db):
    drive = tmp_path / "drive"
    _plates(drive, "REEL_01/SH_010", body=b"OLD")
    target = tmp_path / "P"
    _run(drive, target, operation=MOVE)
    _plates(drive, "REEL_01/SH_010", body=b"NEW")
    for f in (drive / "REEL_01/SH_010").iterdir():
        os.utime(f, (time.time() + 3600, time.time() + 3600))
    survey = isv.survey_drive(drive)
    assert survey.mark_unchanged(target / "PRJ" / "05_Reels") == 0


# ------------------------------------------------------------------ versions
def test_a_rerun_over_a_failing_file_makes_no_empty_version(tmp_path, db, monkeypatch):
    """ING-019: an empty v002 and a 'new scan' flag appeared although nothing arrived."""
    drive = tmp_path / "drive"
    _plates(drive, "REEL_02/SH_095", frames=[1001])
    target = tmp_path / "P"

    def fail(*a, **k):
        return False, "SIMULATED", 0

    monkeypatch.setattr(structure.SafeFileOperations, "safe_copy_with_verification", staticmethod(fail))
    first, _ = _run(drive, target)
    second, _ = _run(drive, target)
    scan = target / "PRJ/05_Reels/REEL_02/SH_095/01_Scan"
    assert not scan.exists() or not any(scan.iterdir())
    assert first.ingested_shots == [] and second.ingested_shots == []


def test_stitch_parts_that_collide_each_get_a_folder(tmp_path, db):
    """ING-049."""
    drive = tmp_path / "drive"
    _plates(drive, "ReelA/SH010_A", name="plate")
    _plates(drive, "ReelA/SH010_B", name="plate")
    survey = isv.survey_drive(drive)
    mapping = apply_groups([], survey.stitch_groups)
    _run(drive, tmp_path / "P", survey=survey, stitch_mapping=mapping)
    version = tmp_path / "P/PRJ/05_Reels/ReelA/SH010/01_Scan/v001"
    assert {f.split("/")[0] for f in _files(version)} == {"A", "B"}


def test_a_dangerous_stitch_name_builds_nothing(tmp_path, db):
    """ING-004: '../../../ESCAPED' built a shot tree outside the project."""
    drive = tmp_path / "drive"
    _plates(drive, "ReelA/SH010_A")
    _plates(drive, "ReelA/SH010_B")
    survey = isv.survey_drive(drive)
    mapping = {("ReelA", "SH010_A"): "../../../ESCAPED", ("ReelA", "SH010_B"): "../../../ESCAPED"}
    worker, _ = _run(drive, tmp_path / "P", survey=survey, stitch_mapping=mapping)
    assert not list(tmp_path.rglob("ESCAPED"))
    assert worker.errors == 8 and worker.files_moved == 0


# ------------------------------------------------------------------ dry run
def test_a_dry_run_creates_nothing_and_counts_like_the_real_run(tmp_path, db):
    """ING-010 / ING-034 / ING-036."""
    drive = _drive(tmp_path)
    target = tmp_path / "P"
    target.mkdir()
    dry, logs = _run(drive, target, dry_run=True)
    assert list(target.iterdir()) == []
    assert any(l.startswith("[DRY]") for l in logs)
    paths = write_report(build_report(dry, "PRJ", drive), target / "PRJ")
    assert paths and str(target) not in paths["report"]
    assert Path(paths["report"]).parent == dry_run_dir("PRJ")
    real, _ = _run(drive, target)
    assert dry.folders_created == real.folders_created


def test_a_dry_run_predicts_skips(tmp_path, db):
    drive = tmp_path / "drive"
    _plates(drive, "ReelA/SH010_A", name="plate")
    _plates(drive, "ReelA/SH010_B", name="plate")
    survey = isv.survey_drive(drive)
    # A stitch without split folders would collide: force the old flat layout.
    mapping = apply_groups([], survey.stitch_groups)
    worker = FolderCreationWorker(target_dir=tmp_path / "P", source_scan_path=drive, project_name="PRJ",
                                  template_data=TEMPLATE, dry_run=True, survey=survey,
                                  stitch_mapping=mapping)
    worker._plan_stitch_splits = lambda: None
    worker.run()
    assert worker.files_skipped == 4


# ------------------------------------------------------------------ the report
def test_the_problems_badge_is_readable():
    """ING-028: red text on a red background."""
    from slate.core.domain.delivery_report import DeliveryReport
    html = _render_html(DeliveryReport(project="P", failed=[{"file": "a", "error": "x"}]))
    rule = html[html.index(".status.bad"):html.index("}", html.index(".status.bad"))]
    assert "color: #FFFFFF" in rule


def test_reports_always_go_to_one_place(tmp_path):
    """ING-054."""
    project = tmp_path / "PRJ"
    first = _report_dir(project)
    (project / "01_Frm Client").mkdir(parents=True)
    assert _report_dir(project) == first == project / "01_Frm Client" / "_ingest_reports"


# ------------------------------------------------------------------ files
def test_a_destination_past_260_characters_is_copied(tmp_path):
    """ING-007."""
    from slate.core.infra.file_operations import SafeFileOperations, long_path
    src = tmp_path / "a.exr"
    src.write_bytes(b"abc")
    dest = tmp_path / ("x" * 120) / ("y" * 120) / "SH_095_very_long_client_name.1001.exr"
    assert len(str(dest)) > 260
    ok, message, size = SafeFileOperations.safe_move_with_verification(src, dest)
    assert ok, message
    assert os.path.exists(long_path(dest)) and not src.exists()


def test_long_path_forms():
    from slate.core.infra.file_operations import long_path
    if os.name == "nt":
        assert long_path(r"C:\a\b") == "\\\\?\\C:\\a\\b"
        assert long_path(r"\\server\share\x") == "\\\\?\\UNC\\server\\share\\x"


def test_big_files_are_checksummed_too(tmp_path, monkeypatch):
    """ING-037: no 1 GB cut-off when verification is on."""
    from slate.core.infra import file_operations as fo
    src = tmp_path / "big.mov"
    src.write_bytes(b"x" * 10)
    calls = []
    real = fo.SafeFileOperations._calculate_checksum
    monkeypatch.setattr(fo.SafeFileOperations, "_get_path_size", staticmethod(lambda p: 2 * 1024 ** 3))
    monkeypatch.setattr(fo.SafeFileOperations, "_calculate_checksum",
                        staticmethod(lambda p: calls.append(p) or real(p)))
    monkeypatch.setattr(fo.SafeFileOperations, "_check_disk_space", staticmethod(lambda *a: (True, "")))
    fo.SafeFileOperations.safe_copy_with_verification(src, tmp_path / "out" / "big.mov", True)
    assert len(calls) == 2


def test_two_thousand_small_frames_are_quick(tmp_path, db, monkeypatch):
    """ING-032: 33 s for 2,000 tiny frames; task rows now go in batches."""
    drive = tmp_path / "drive"
    _plates(drive, "REEL_01/SH_010", frames=range(1, 2001), body=b"0123456789abcdef")
    batches = []
    repo = db.project_repo
    real = repo.record_task_details
    monkeypatch.setattr(repo, "record_task_details", lambda rows: batches.append(len(rows)) or real(rows))
    started = time.perf_counter()
    worker, _ = _run(drive, tmp_path / "P", operation=MOVE)
    elapsed = time.perf_counter() - started
    assert worker.files_moved == 2000
    assert sum(batches) == 2000 and max(batches) <= 200
    assert elapsed < 15, elapsed


# ------------------------------------------------------------------ registration
def test_shots_are_registered_by_the_worker(tmp_path, db):
    """ING-079: off the UI thread."""
    drive = _drive(tmp_path)
    worker, _ = _run(drive, tmp_path / "P", register_shots=True)
    assert worker.registration is not None and worker.registration.ok
    assert sorted(worker.registration.created) == ["SH_010", "SH_020", "SH_030"]


# ------------------------------------------------------------------ retry
def test_retry_honours_copy_and_rewrites_the_manifest(tmp_path, db, monkeypatch):
    drive = _drive(tmp_path)
    target = tmp_path / "P"

    def fail(*a, **k):
        return False, "SIMULATED", 0

    real = structure.SafeFileOperations.safe_copy_with_verification
    monkeypatch.setattr(structure.SafeFileOperations, "safe_copy_with_verification", staticmethod(fail))
    worker, _ = _run(drive, target)
    paths = write_report(build_report(worker, "PRJ", drive), target / "PRJ")
    monkeypatch.setattr(structure.SafeFileOperations, "safe_copy_with_verification", staticmethod(real))

    assert latest_manifest(target / "PRJ") == Path(paths["manifest"])
    assert len(pending_failures(target / "PRJ")) == 12
    result = retry_failures(paths["manifest"], fast_mode=True)
    assert len(result.recovered) == 12
    assert len(_files(drive)) == 12                      # copy: the drive is untouched
    assert pending_failures(target / "PRJ") == []
    assert json.loads(Path(paths["manifest"]).read_text())["retries"]


# ------------------------------------------------------------------ the lock
def test_the_lock_names_the_slate_user_and_can_be_cleared(tmp_path):
    lock = IngestLock(tmp_path, holder="Priya Sharma (priya)").acquire()
    assert lock_info(tmp_path).holder == "Priya Sharma (priya)"
    with pytest.raises(IngestLocked):
        IngestLock(tmp_path, holder="someone").acquire()
    assert clear_lock(tmp_path)
    IngestLock(tmp_path, holder="someone").acquire().release()
    lock.acquired = False


def test_the_worker_keeps_the_lock_alive(tmp_path, db, monkeypatch):
    drive = _drive(tmp_path)
    touched = []

    class Lock:
        def touch(self):
            touched.append(1)

    monkeypatch.setattr(structure, "LOCK_TOUCH_SECONDS", 0)
    _run(drive, tmp_path / "P", lock=Lock())
    assert touched


def test_default_format_folders_are_upper_case():
    """ING-074."""
    from slate.core.infra.config_manager import ConfigManager
    leaves = {v.split("/")[-1] for v in ConfigManager().default_format_mapping.values()}
    assert all(leaf == leaf.upper() for leaf in leaves)


def test_documents_filed_by_an_earlier_run_are_left_out(tmp_path, db):
    drive = _drive(tmp_path)
    (drive / "readme.txt").write_text("notes")
    target = tmp_path / "P"
    _run(drive, target)
    survey = isv.survey_drive(drive)
    assert isv.mark_documents_filed(survey, target / "PRJ" / "01_Frm Client") == 1
    assert survey.documents == [] and len(survey.documents_filed_before) == 1
