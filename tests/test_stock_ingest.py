"""
The stock ingest, end to end on real (generated) files.

    MED-001  what the analysis finds is in the database afterwards
    MED-005  visual tags are made and stored
    MED-008  several folders in one ingest
    MED-010  numbered stills are not merged into fake sequences
    MED-020  a still has no frame rate or duration
    MED-021  the run ends with a summary
    MED-024  proxies keep the picture's shape and are never enlarged
    MED-029  stills are measured without starting a process
    MED-037  the scan reports progress before the analysis starts
    MED-039  thumbnails in very deep cache folders are kept
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "slate" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
FFPROBE = ROOT / "slate" / "bin" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG.exists(), reason="bundled ffmpeg not present")


def _picture(path: Path, w=64, h=36, colour=(30, 200, 60)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(w, h, QImage.Format.Format_RGB888)
    image.fill(QColor(*colour))
    assert image.save(str(path))
    return path


def _movie(path: Path, w, h, seconds=1, audio=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
           "-i", f"testsrc=size={w}x{h}:rate=24:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-c:a", "aac", "-shortest"]
    cmd += ["-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, timeout=60)
    return path


def _size(path: Path):
    out = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from slate.core.domain.proxy_manager import proxy_manager
    folder = tmp_path / "cache"
    folder.mkdir()
    monkeypatch.setattr(proxy_manager, "cache_dir", folder)
    return folder


# ------------------------------------------------------------ grouping

def test_numbered_stills_stay_stills(tmp_path):
    from slate.core.domain.asset_ingestor import group_media
    files = []
    for n in (1, 3, 5, 7, 9):
        files.append(tmp_path / f"fire_burst_{n:02d}.png")
    files.append(tmp_path / "IMG_2045.jpg")
    files.append(tmp_path / "sparks_1.png")
    files.append(tmp_path / "sparks_2.png")
    files += [tmp_path / f"plate.{f}.exr" for f in range(1001, 1025)]
    sequences, stills = group_media(files)
    assert len(sequences) == 1
    assert sequences[0].frame_count == 24 and sequences[0].start == 1001
    assert len(stills) == 8


def test_a_short_padded_run_is_a_sequence(tmp_path):
    from slate.core.domain.asset_ingestor import group_media, sequence_display_name
    files = [tmp_path / f"muzzle_A.{f:04d}.png" for f in (1, 2)]
    sequences, stills = group_media(files)
    assert len(sequences) == 1 and not stills
    assert sequence_display_name(sequences[0]) == "muzzle_A.[1-2].png"


# ------------------------------------------------------------ metadata

def test_a_still_is_measured_without_a_process(tmp_path, monkeypatch):
    from slate.core.domain import metadata_engine
    jpg = _picture(tmp_path / "portrait.jpg", 400, 500)

    def no_process(*a, **k):
        raise AssertionError("ffprobe was started for a jpg")
    monkeypatch.setattr(metadata_engine.subprocess, "run", no_process)
    meta = metadata_engine.SmartMetadataManager.extract_tech_metadata(str(jpg))
    assert (meta["width"], meta["height"]) == (400, 500)
    assert meta["is_still"] and meta["fps"] == 0 and meta["duration_sec"] == 0


def test_visual_tags_from_a_green_frame(tmp_path):
    from slate.core.domain.metadata_engine import SmartMetadataManager
    green = _picture(tmp_path / "gs.jpg", colour=(20, 190, 40))
    dark = _picture(tmp_path / "night.jpg", colour=(8, 8, 12))
    assert "Green Screen" in SmartMetadataManager.extract_visual_tags(str(green))
    assert SmartMetadataManager.extract_visual_tags(str(dark)) == ["Dark"]


# ------------------------------------------------------------ proxies

@needs_ffmpeg
def test_proxies_keep_their_shape(tmp_path, cache):
    from slate.core.domain.proxy_manager import proxy_manager
    still = _picture(tmp_path / "portrait.jpg", 400, 500)
    ok, proxy = proxy_manager.generate_proxy(still)
    assert ok
    reader = QImage(str(proxy))
    assert (reader.width(), reader.height()) == (400, 500)

    tall = _movie(tmp_path / "phone.mp4", 1080, 1920)
    ok, proxy = proxy_manager.generate_proxy(tall)
    assert ok and _size(proxy) == (608, 1080)

    scope = _movie(tmp_path / "scope.mp4", 1920, 804)
    ok, proxy = proxy_manager.generate_proxy(scope)
    assert ok and _size(proxy) == (1920, 804)


@pytest.mark.skipif(os.name != "nt", reason="the 260-character limit is a Windows one")
def test_a_thumbnail_in_a_very_deep_folder_is_kept(tmp_path, monkeypatch):
    from slate.core.domain.proxy_manager import proxy_manager
    deep = tmp_path
    while len(str(deep)) < 280:
        deep = deep / "a_rather_long_folder_name"
    os.makedirs(proxy_manager.long_path(deep), exist_ok=True)
    monkeypatch.setattr(proxy_manager, "cache_dir", deep)
    source = _picture(tmp_path / "src.jpg")
    ok, thumb = proxy_manager.generate_thumbnail(source)
    assert ok
    assert os.path.exists(proxy_manager.long_path(thumb))


# ------------------------------------------------------------ the ingest

@needs_ffmpeg
def test_an_ingest_of_two_folders(tmp_path, cache, mock_db, monkeypatch):
    from slate.core.domain import asset_ingestor
    from slate.core.domain.library_manager import LibraryManager

    lib = LibraryManager(mock_db, username="priya")
    monkeypatch.setattr(asset_ingestor, "create_asset_api", lambda **kw: lib)

    a, b = tmp_path / "Footage", tmp_path / "Misc"
    _picture(a / "greenscreen_ref.jpg", colour=(20, 190, 40))
    _picture(a / "rain_heavy.jpg", 320, 180, colour=(90, 90, 120))
    _movie(a / "explosion.mp4", 320, 180)
    for f in range(1001, 1006):
        _picture(b / f"muzzle_flash.{f}.png", colour=(240, 160, 40))
    for n in (1, 3, 5):
        _picture(b / f"fire_burst_{n:02d}.png", colour=(200, 60, 20))

    worker = asset_ingestor.IngestWorker(root_paths=[str(a), str(b)], fast_mode=True,
                                         username="priya")
    progress, summaries = [], []
    worker.progress_signal.connect(lambda pct, text: progress.append((pct, text)))
    worker.summary_ready.connect(summaries.append)
    worker.run()

    assert progress and progress[0][0] == -1          # scanning first
    summary = summaries[-1]
    assert summary["found"] == 7 and summary["added"] == 7 and summary["failed"] == 0

    rows = {dict(r)["file_name"]: dict(r) for r in mock_db.execute_query(
        "SELECT * FROM stock_library")}
    assert len(rows) == 7
    for name, row in rows.items():
        assert row["tags"] and "Pending" not in row["tags"], name
        assert json.loads(row["metadata"]).get("width"), name
        assert row["thumb_path"], name
        assert row["added_by"] == "priya"
    assert "Green Screen" in rows["greenscreen_ref.jpg"]["visual_tags"]
    seq = rows["muzzle_flash.1001.png"]
    assert seq["is_sequence"] and seq["frame_count"] == 5
    assert seq["display_name"] == "muzzle_flash.[1001-1005].png"
    assert set(lib.ingest_roots()) == {str(a), str(b)}

    # Again: nothing new, nothing changed.
    before = {n: (r["tags"], r["metadata"]) for n, r in rows.items()}
    again = asset_ingestor.IngestWorker(root_paths=[str(a), str(b)], fast_mode=True)
    summaries.clear()
    again.summary_ready.connect(summaries.append)
    again.run()
    assert summaries[-1]["skipped"] == 7 and summaries[-1]["added"] == 0
    after = {dict(r)["file_name"]: (dict(r)["tags"], dict(r)["metadata"])
             for r in mock_db.execute_query("SELECT * FROM stock_library")}
    assert after == before
