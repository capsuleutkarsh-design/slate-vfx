"""
ffmpeg and ffprobe, checked with the real binaries on media they made.

Each test reads back what Slate produced (ffprobe frame counts, a decodable
JPEG) rather than trusting the return value.
"""

import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate.core.domain import proxy_builder
from slate.core.domain.metadata_engine import SmartMetadataManager
from slate.core.domain.proxy_manager import ProxyManager
from tests.realtools.conftest import ffmpeg, make_movie, make_sequence, needs_ffmpeg, probe

pytestmark = [pytest.mark.realtools, needs_ffmpeg]

LP = ProxyManager.long_path


def _frames(path) -> int:
    return int(probe(LP(path))["nb_read_frames"])


def _leftovers(folder: Path):
    return [p for p in Path(LP(folder)).rglob("*") if ".part" in p.name]


def _is_jpeg(path) -> bool:
    from PySide6.QtGui import QImage
    with open(LP(path), "rb") as handle:
        head = handle.read(3)
    image = QImage()
    with open(LP(path), "rb") as handle:
        image.loadFromData(handle.read())
    return head == b"\xff\xd8\xff" and not image.isNull() and 0 < image.width() <= 320


def _deep(tmp_path) -> Path:
    """A folder well past Windows' 260-character limit."""
    deep = tmp_path.joinpath(*(c * 60 for c in "defg"))
    os.makedirs(LP(deep), exist_ok=True)
    assert len(str(deep)) > 260
    return deep


@pytest.mark.parametrize("kind", ["mov", "mp4", "png", "exr", "dpx", "exr_seq", "short_mov"])
def test_thumbnail_for_every_kind_of_media(isolated_proxy_manager, tmp_path, kind):
    if kind in ("mov", "mp4"):
        source, is_seq = make_movie(tmp_path / f"clip.{kind}", frames=48), False
    elif kind == "short_mov":  # shorter than the 1 s seek: falls back to frame 0
        source, is_seq = make_movie(tmp_path / "short.mov", frames=6), False
    else:
        ext = kind.split("_")[0]
        pattern = make_sequence(tmp_path / "frames", ext=ext, count=6)
        source, is_seq = Path(str(pattern) % 1001), kind.endswith("_seq")
    ok, thumb = isolated_proxy_manager.generate_thumbnail(source, is_seq=is_seq)
    assert ok and _is_jpeg(thumb)
    assert not _leftovers(isolated_proxy_manager.cache_dir)


@pytest.mark.parametrize("ext", ["exr", "dpx"])
def test_sequence_proxy_has_every_frame(isolated_proxy_manager, tmp_path, ext):
    pattern = make_sequence(tmp_path / "plate", ext=ext, count=30)
    ok, proxy = isolated_proxy_manager.generate_proxy(Path(str(pattern) % 1001), is_seq=True,
                                                      proxy_path=tmp_path / "p.mp4")
    assert ok and _frames(proxy) == 30
    stream = probe(proxy)
    assert (stream["codec_name"], stream["pix_fmt"], stream["r_frame_rate"]) == ("h264", "yuv420p", "24/1")


def test_sequence_proxy_with_a_missing_frame_runs_the_whole_shot(isolated_proxy_manager, tmp_path):
    """
    FOUND + FIXED: ffmpeg's image reader stops at a gap, so a render with frame
    1005 missing made a 4-frame proxy, reported as made, which the lineup then
    played instead of the shot. The gap now holds the frame before it.
    """
    folder = tmp_path / "O'Brien plate"  # the frame list quotes paths
    pattern = make_sequence(folder, count=10)
    Path(str(pattern) % 1005).unlink()
    ok, proxy = isolated_proxy_manager.generate_proxy(Path(str(pattern) % 1001), is_seq=True,
                                                      proxy_path=tmp_path / "p.mp4")
    assert ok and _frames(proxy) == 10
    assert not list(folder.parent.rglob("*.txt"))  # the frame list is cleaned up


def test_long_paths_thumbnail_and_sequence_proxy(isolated_proxy_manager, tmp_path):
    deep = _deep(tmp_path)
    isolated_proxy_manager.cache_dir = deep / "cache"
    for frame in make_sequence(tmp_path / "s", count=5).parent.iterdir():
        shutil.copy(frame, LP(deep / frame.name))
    movie = make_movie(tmp_path / "m.mov", frames=36)
    shutil.copy(movie, LP(deep / "SH010_comp_v001.mov"))

    ok, thumb = isolated_proxy_manager.generate_thumbnail(deep / "SH010_comp_v001.mov")
    assert ok and _is_jpeg(thumb)
    ok, proxy = isolated_proxy_manager.generate_proxy(deep / "SH010_plate.1001.exr", is_seq=True,
                                                      overwrite=True)
    assert ok and len(str(proxy)) > 260 and _frames(proxy) == 5
    meta = SmartMetadataManager.extract_tech_metadata(str(deep / "SH010_comp_v001.mov"))
    assert (meta["width"], meta["height"], meta["fps"]) == (320, 240, 24.0)
    assert not _leftovers(deep)


@pytest.mark.parametrize("broken", ["junk", "truncated"])
def test_a_failed_encode_leaves_nothing_behind(isolated_proxy_manager, tmp_path, broken):
    source = tmp_path / "bad.mov"
    if broken == "junk":
        source.write_bytes(b"not a movie at all")
    else:
        data = make_movie(tmp_path / "good.mov", frames=48).read_bytes()
        source.write_bytes(data[: len(data) // 3])
    target = tmp_path / "out" / "bad_proxy.mp4"
    target.parent.mkdir()
    assert isolated_proxy_manager.generate_proxy(source, proxy_path=target) == (False, None)
    assert isolated_proxy_manager.generate_thumbnail(source) == (False, None)
    assert not list(target.parent.iterdir())
    assert not _leftovers(isolated_proxy_manager.cache_dir)


def test_rebuild_all_proxies_shows_the_new_render(isolated_proxy_manager, tmp_path):
    """Make review proxies, re-render the plate, Rebuild all: the proxy is the new one."""
    project = tmp_path / "proj"
    plate = project / "SH010" / "01_Scan" / "v001" / "EXR"
    make_sequence(plate, count=12)
    shot = SimpleNamespace(shot_name="SH010", folder_paths={"scan": "SH010/01_Scan"})

    jobs = proxy_builder.plan([shot], project)
    result = proxy_builder.build(jobs, manager=isolated_proxy_manager)
    assert result.built and not result.failed
    target = jobs[0].target
    assert _frames(target) == 12
    assert proxy_builder.plan([shot], project) == []  # current: not offered again

    time.sleep(1.1)  # a re-render is newer than the proxy
    shutil.rmtree(plate)
    make_sequence(plate, count=20)
    rebuild = proxy_builder.plan([shot], project, rebuild=True)
    result = proxy_builder.build(rebuild, manager=isolated_proxy_manager, overwrite=True)
    assert result.built and _frames(target) == 20


@pytest.mark.parametrize("name,rate,frames,fps,duration", [
    ("m2398.mov", "24000/1001", 48, 23.976, 2.002),
    ("m25.mp4", "25", 50, 25.0, 2.0),
    ("m24.mov", "24", 36, 24.0, 1.5),
])
def test_movie_metadata(tmp_path, name, rate, frames, fps, duration):
    make_movie(tmp_path / name, frames=frames, rate=rate, size="640x360")
    meta = SmartMetadataManager.extract_tech_metadata(str(tmp_path / name))
    assert (meta["width"], meta["height"], meta["codec"]) == (640, 360, "h264")
    assert meta["fps"] == pytest.approx(fps, abs=0.001)
    assert meta["duration_sec"] == pytest.approx(duration, abs=0.05)


@pytest.mark.parametrize("ext", ["exr", "dpx", "png", "tif"])
def test_still_metadata(tmp_path, ext):
    path = tmp_path / f"still.{ext}"
    extra = ["-pix_fmt", "gbrpf32le"] if ext == "exr" else []
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", 1, *extra, path)
    meta = SmartMetadataManager.extract_tech_metadata(str(path))
    assert (meta["width"], meta["height"]) == (640, 360)
    assert meta["is_still"] and meta["fps"] == 0 and meta["duration_sec"] == 0
