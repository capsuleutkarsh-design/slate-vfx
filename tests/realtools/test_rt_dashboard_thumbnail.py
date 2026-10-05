"""
The dashboard's shot thumbnail, made by the real ffmpeg from a plate laid out
the way the ingest lays it out (01_Scan/<version>/EXR).
"""

import threading
from pathlib import Path

import pytest

from tests.realtools.conftest import make_movie, make_sequence, needs_ffmpeg

pytestmark = [pytest.mark.realtools, needs_ffmpeg]


def _thumbnail(tmp_path, monkeypatch, project):
    from slate.core.infra.global_config import GlobalConfig
    from slate.gui.tabs.vfx_dashboard_pro.utils.thumbnail import ThumbnailGenerator
    monkeypatch.setattr(GlobalConfig, "central_thumbnails_dir",
                        classmethod(lambda cls: tmp_path / "central"))
    gen = ThumbnailGenerator(local_cache_dir=str(tmp_path / "local"))
    out = {}
    # Off the GUI thread, as the dashboard's loader calls it.
    worker = threading.Thread(target=lambda: out.update(path=gen.get_or_create_thumbnail(
        "PRJ", "01", "SH010", str(project))))
    worker.start()
    worker.join(60)
    return gen, Path(out["path"])


def _size(path):
    from PySide6.QtGui import QImage
    image = QImage(str(path))
    return image.width(), image.height()


@pytest.mark.parametrize("plate", ["versioned_exr", "versioned_mov"])
def test_thumbnail_is_made_from_the_newest_scan_version(tmp_path, monkeypatch, plate):
    """
    FOUND + FIXED: the plate was looked for in 01_Scan and one folder below,
    but the ingest puts it two below (01_Scan/v001/EXR), so every shot showed
    the red "no source" placeholder.
    """
    scan = tmp_path / "proj" / "05_Reels" / "REEL_01" / "PRJ_RL_01_SH010" / "01_Scan"
    if plate == "versioned_exr":
        make_sequence(scan / "v001" / "EXR", count=3, size="320x240")
        make_sequence(scan / "v002" / "EXR", count=3, size="1920x1080")
    else:
        make_movie(scan / "v001" / "MOV" / "SH010_v001.mov", frames=6, size="1920x1080")
    gen, thumb = _thumbnail(tmp_path, monkeypatch, tmp_path / "proj")
    assert thumb != Path(gen.red_path), "red placeholder: no plate found"
    assert thumb.read_bytes()[:3] == b"\xff\xd8\xff"
    assert _size(thumb) == (300, 169)  # from the 1920x1080 plate, the newest
