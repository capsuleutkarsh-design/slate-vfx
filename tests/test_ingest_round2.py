"""
Build & Ingest, round 2 (ING2-0xx): one rule for what a shot is, what is not a
shot and where the client folder is; stopped runs that can be finished; and
the report, retry and lock behaviour around them. Scratch drives only.
"""

import json
import os
from pathlib import Path

import pytest

from slate.core.domain import ingest_survey as isv
from slate.core.domain.delivery_report import build_report, write_report
from slate.core.domain.ingest_retry import manifests_with_failures, retry_failures
from slate.core.domain.naming import normalise_shot_name, shot_name_problem
from slate.core.workers import structure
from slate.core.workers.structure import COPY, MOVE, FolderCreationWorker

TEMPLATE = (["01_Frm Client", "05_Reels"], [], [], ["01_Scan", "07_Comp", "08_Output"])


@pytest.fixture(scope="module", autouse=True)
def _keep_the_shared_database_manager():
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    saved = (db_module._manager_instance, SQLiteManager._instance)
    yield
    with db_module._manager_lock:
        db_module._manager_instance = saved[0]
    SQLiteManager._instance = saved[1]


@pytest.fixture
def db(mock_db, monkeypatch):
    monkeypatch.setattr(structure, "database_manager", mock_db)
    return mock_db


def _plates(root: Path, rel: str, frames=range(1001, 1006), name=None, ext="exr"):
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    base = name or "plate"
    for f in frames:
        (folder / f"{base}.{f:04d}.{ext}").write_bytes(b"exr")
    return folder


def _files(root: Path):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} if root.exists() else set()


def _run(source, target, operation=COPY, **kw):
    kw.setdefault("template_data", TEMPLATE)
    worker = FolderCreationWorker(target_dir=target, source_scan_path=source, project_name="PRJ",
                                  fast_mode=True, format_mapping={}, operation=operation, **kw)
    logs = []
    worker.log_signal.connect(logs.append)
    worker.run()
    return worker, logs


# ------------------------------------------------------------------ what a shot is
def test_format_subfolders_are_formats_of_one_shot(tmp_path):
    """ING2-001: SH_010/EXR + SH_010/MOV is one shot SH_010; SH_020/EXR is another."""
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_010/EXR")
    (drive / "REEL_01/SH_010/MOV").mkdir(parents=True)
    (drive / "REEL_01/SH_010/MOV/SH_010.mov").write_bytes(b"m")
    _plates(drive, "REEL_01/SH_020/EXR")
    survey = isv.survey_drive(drive)
    assert sorted((s.reel, s.name, s.file_count) for s in survey.shots) == [
        ("REEL_01", "SH_010", 6), ("REEL_01", "SH_020", 5)]
    assert survey.problems() == []


def test_two_folders_with_one_name_clash_instead_of_merging(tmp_path):
    """ING2-001: a clash is keyed on where the folder is, so seqA/SH_010 and seqB/SH_010 are not one shot."""
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/seqA/SH_010")
    _plates(drive, "REEL_01/seqB/SH_010")
    survey = isv.survey_drive(drive)
    clashes = survey.name_clashes()
    assert list(clashes) == ["REEL_01/SH_010"] and len(clashes["REEL_01/SH_010"]) == 2
    assert "Two different folders would both become REEL_01/SH_010" in survey.problems()[0]


def test_rescans_of_a_shot_still_share_it(tmp_path):
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_050_ScanA")
    _plates(drive, "REEL_01/SH_050_ScanB")
    assert isv.survey_drive(drive).problems() == []


def test_same_file_names_in_two_size_folders_keep_their_folders(tmp_path, db):
    """Same names in SH_010/EXR/2K and SH_010/EXR/4K: nothing skipped as 'already there'."""
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_010/EXR/2K")
    _plates(drive, "REEL_01/SH_010/EXR/4K")
    worker, _ = _run(drive, tmp_path / "P")
    landed = _files(tmp_path / "P" / "PRJ" / "05_Reels")
    assert worker.files_moved == 10 and worker.files_skipped == 0
    assert "REEL_01/SH_010/01_Scan/v001/EXR/2K/plate.1001.exr" in landed
    assert "REEL_01/SH_010/01_Scan/v001/EXR/4K/plate.1001.exr" in landed


def test_plates_deep_in_the_drive_are_found(tmp_path):
    """ING2-002: no depth cap, and an unvisited folder is never called empty."""
    drive = tmp_path / "d"
    _plates(drive, "Delivery/a/b/c/d/e/REEL_01/SH_030")
    _plates(drive, "REEL_02/SH_040")
    survey = isv.survey_drive(drive)
    assert sorted(s.name for s in survey.shots) == ["SH_030", "SH_040"]
    assert survey.empty_folders == []


# ------------------------------------------------------------------ what is not a shot
def test_luts_references_and_loose_files_are_client_material(tmp_path):
    """ING2-010 / ING2-032."""
    drive = tmp_path / "CLIENT_DELIVERY"
    _plates(drive, "REEL_01/SH_010")
    (drive / "LUTS").mkdir()
    (drive / "LUTS/show.cube").write_text("lut")
    (drive / "REFERENCE").mkdir()
    (drive / "REFERENCE/look.jpg").write_bytes(b"j")
    (drive / "temp.wav").write_bytes(b"w")
    (drive / "ref.jpg").write_bytes(b"j")
    survey = isv.survey_drive(drive)
    assert [s.name for s in survey.shots] == ["SH_010"]
    assert sorted(d.path.name for d in survey.documents) == ["look.jpg", "ref.jpg", "show.cube", "temp.wav"]
    survey.loose_media_as_shot(True)         # the coordinator says the loose jpg is a shot
    assert [s.name for s in survey.shots if s.is_root] == ["CLIENT_DELIVERY"]
    assert sorted(d.path.name for d in survey.documents) == ["look.jpg", "show.cube", "temp.wav"]
    survey.loose_media_as_shot(False)
    assert len(survey.documents) == 4 and not any(s.is_root for s in survey.shots)


def test_one_rule_for_the_client_folder(tmp_path, db):
    """ING2-006: the template's client folder is used by the worker, the report and mark_documents_filed."""
    template = {"base_folders": ["00_Incoming", "01_Edit", "05_Reels"], "client_folder": "00_Incoming"}
    assert isv.client_folder_for(template) == "00_Incoming"
    assert isv.client_folder_for({"base_folders": ["01_Edit"]}) == "01_Frm Client"
    assert isv.client_folder_for({"structure": {"base_folders": ["01_From Client"]}}) == "01_From Client"
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_010")
    (drive / "readme.txt").write_text("hi")
    worker, _ = _run(drive, tmp_path / "P", template_data=(template["base_folders"], [], [], ["01_Scan"]),
                     client_folder="00_Incoming")
    project = tmp_path / "P" / "PRJ"
    assert any(f.endswith("readme.txt") for f in _files(project / "00_Incoming"))
    assert not (project / "01_Frm Client").exists()
    paths = write_report(build_report(worker, "PRJ", drive), project, worker.client_folder)
    assert "00_Incoming" in paths["report"]
    survey = isv.survey_drive(drive)
    assert isv.mark_documents_filed(survey, project / "00_Incoming") == 1


# ------------------------------------------------------------------ names
def test_names_in_other_scripts_stay_apart():
    """ING2-029."""
    assert normalise_shot_name("शॉट 010") != normalise_shot_name("दृश्य 010")
    assert normalise_shot_name("Ünïcode_030") == "Unicode_030"
    assert normalise_shot_name("sh 060 (client)") == "SH_060"


def test_a_bad_new_name_is_told_in_one_sentence():
    """ING2-041."""
    text = shot_name_problem("bad name/..", "SH_0110: the name")
    assert text.startswith("SH_0110: the name 'bad name/..' can only use letters, digits")
    assert "no spaces, / or .." in text


# ------------------------------------------------------------------ the run
def test_an_empty_version_list_means_no_version_folders(tmp_path, db):
    """ING2-007."""
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_010")
    _run(drive, tmp_path / "P", scan_version_folders=[])
    assert not (tmp_path / "P/PRJ/05_Reels/REEL_01/SH_010/01_Scan/v001/Denoise").exists()
    _run(drive, tmp_path / "Q")
    assert (tmp_path / "Q/PRJ/05_Reels/REEL_01/SH_010/01_Scan/v001/Denoise").is_dir()


def test_a_dry_run_writes_no_project_row(tmp_path, db, monkeypatch):
    """ING2-013."""
    calls = []
    monkeypatch.setattr(db, "record_project", lambda *a, **k: calls.append(a) or 1, raising=False)
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_010")
    _run(drive, tmp_path / "P", dry_run=True)
    assert calls == []


def test_a_stopped_run_is_finished_into_the_same_version(tmp_path, db, monkeypatch):
    """ING2-003 / ING2-030 / ING2-004 / ING2-005."""
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_500", frames=range(1001, 1061))
    _plates(drive, "REEL_01/SH_600", frames=range(1001, 1004))
    real = FolderCreationWorker._transfer
    counter = {"n": 0}

    def stop_after_13(worker, *a, **k):
        status = real(worker, *a, **k)
        counter["n"] += 1
        if counter["n"] == 13:
            worker.stop()
        return status

    monkeypatch.setattr(FolderCreationWorker, "_transfer", stop_after_13)
    worker, logs = _run(drive, tmp_path / "P", register_shots=True)
    monkeypatch.setattr(FolderCreationWorker, "_transfer", real)
    assert worker.outcome == "stopped"
    report = build_report(worker, "PRJ", drive)
    seq = next(s for s in report.sequences if s["shot"] == "SH_500")
    assert seq["frames"] == 13 and len(seq["not_landed"]) == 47       # never reported whole
    assert any(line.startswith("[STOP] plate [1001-1060].exr - 13 of 60") for line in logs)
    assert len(report.pending) == 47 + 3
    project = tmp_path / "P" / "PRJ"
    paths = write_report(report, project, worker.client_folder)
    assert manifests_with_failures(project)[0]["pending"] == 50

    registered = []
    import slate.core.domain.shot_registry as registry

    class Result:
        error, created, new_scans = "", [], []

    def fake_register(project_code, shots, **kw):
        registered.extend(s["shot"] for s in shots)
        result = Result()
        result.created = [s["shot"] for s in shots]
        return result

    monkeypatch.setattr(registry, "register_ingested_shots", fake_register)
    result = retry_failures(paths["manifest"], fast_mode=True)
    assert len(result.recovered) == 50
    scan = project / "05_Reels/REEL_01/SH_500/01_Scan"
    assert sorted(p.name for p in scan.iterdir()) == ["v001"]                     # no v002
    assert len(list((scan / "v001/EXR").iterdir())) == 60
    assert (project / "05_Reels/REEL_01/SH_600/01_Scan/v001/Denoise").is_dir()   # the version's own folders
    assert registered == ["SH_600"]           # the shot that got its first file in the retry
    data = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    assert data["status"] == "completed" and data["pending"] == []
    html = Path(paths["report"]).read_text(encoding="utf-8")
    assert "Retried 1 time(s)" in html                                           # the report was rewritten
    assert manifests_with_failures(project) == []


def test_every_run_with_failures_is_offered(tmp_path):
    """ING2-023: a newer run does not hide an older run's failures."""
    folder = tmp_path / "PRJ" / "01_Frm Client" / "_ingest_reports"
    folder.mkdir(parents=True)
    entry = {"file": "a", "source": "x", "destination": "y"}
    (folder / "manifest_20260101_000000.json").write_text(json.dumps({"failed": [entry]}))
    (folder / "manifest_20260102_000000.json").write_text(json.dumps({"failed": []}))
    assert [m["failed"] for m in manifests_with_failures(tmp_path / "PRJ")] == [1]


def test_move_takes_away_the_folders_it_emptied(tmp_path, db):
    """ING2-020."""
    drive = tmp_path / "d"
    _plates(drive, "REEL_01/SH_900")
    (drive / "readme.txt").write_text("r")
    (drive / "REEL_02/SH_910").mkdir(parents=True)               # empty before: not ours to remove
    _run(drive, tmp_path / "P", operation=MOVE)
    assert not (drive / "REEL_01").exists()
    assert (drive / "REEL_02/SH_910").is_dir()


def test_a_copy_that_dies_leaves_no_partial_file(tmp_path, monkeypatch):
    """ING2-019."""
    import shutil
    from slate.core.infra.file_operations import SafeFileOperations
    src = tmp_path / "a.exr"
    src.write_bytes(b"x" * 100)
    dest = tmp_path / "out" / "a.exr"

    def dies(a, b, *args, **kw):
        Path(b).write_bytes(b"x" * 10)
        raise OSError("network name no longer available")

    monkeypatch.setattr(shutil, "copy2", dies)
    ok, _msg, _ = SafeFileOperations.safe_copy_with_verification(src, dest, verify_checksum=False)
    assert not ok and not dest.exists() and list((tmp_path / "out").iterdir()) == []


def test_two_deliveries_register_the_newest(mock_db):
    """ING2-009."""
    from slate.core.domain.shot_registry import register_ingested_shots
    result = register_ingested_shots("R2P", [
        {"reel": "R1", "shot": "SH_050", "scan_version": "v001"},
        {"reel": "R1", "shot": "SH_050", "scan_version": "v002"}], db=mock_db)
    assert result.created == ["SH_050"]
    row = next(r for r in mock_db.get_tracking_shots("R2P") if r.get("shot_name") == "SH_050")
    assert "v002" in json.dumps(row)


def test_a_stale_lock_of_another_machine_is_asked_about(tmp_path):
    """ING2-017: never cleared silently."""
    import time
    from slate.core.domain.ingest_lock import STALE_AFTER, IngestLock, IngestLocked
    lock = tmp_path / ".ingest.lock"
    lock.write_text(json.dumps({"holder": "coord2", "machine": "OTHER-PC", "started_at": "x"}))
    old = time.time() - STALE_AFTER.total_seconds() - 60
    os.utime(lock, (old, old))
    with pytest.raises(IngestLocked) as caught:
        IngestLock(tmp_path, holder="coord1").acquire()
    assert caught.value.info.stale and lock.exists()
