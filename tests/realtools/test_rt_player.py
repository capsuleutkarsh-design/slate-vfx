"""
The preview player decoding real files with the real ffmpeg, offscreen.

Every frame carries its own grey level (frame N is N*8+16), so what the player
shows can be checked frame by frame, not just "a picture appeared".
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QSize

from tests.realtools.conftest import ffmpeg, needs_ffmpeg

pytestmark = [pytest.mark.realtools, needs_ffmpeg]

GREY = "nullsrc=s=64x64:r=24,geq=lum='N*8+16':cb=128:cr=128"


def _level(n):  # what frame n reads as once in full-range RGB
    return round((n * 8) * 255 / 219)


def _collect(engine):
    got = []
    engine.frame_ready.connect(lambda img: got.append(
        (engine.current_frame, img.pixelColor(img.width() // 2, img.height() // 2).red())))
    return got


def _play_to_end(qtbot, engine, got, count):
    engine.set_loop(False)
    engine.play()
    qtbot.waitUntil(lambda: len(got) >= count, timeout=10000)
    qtbot.wait(300)  # nothing more may follow
    engine.stop()


def _close(a, b):
    return abs(a - b) <= 4


def test_movie_plays_every_frame_in_order_and_seeks_exactly(qtbot, tmp_path):
    from slate.gui.widgets.media_engines.stream_engine import StreamEngine
    movie = tmp_path / "grey.mov"
    ffmpeg("-f", "lavfi", "-i", GREY, "-frames:v", 24, "-c:v", "libx264", "-qp", 0,
           "-pix_fmt", "yuv444p", movie)
    engine = StreamEngine()
    engine.set_target_size(QSize(64, 64))
    got = _collect(engine)
    engine.load(str(movie))
    _play_to_end(qtbot, engine, got, 24)
    assert engine.total_frames == 24 and len(got) == 24
    assert all(i == n and _close(level, _level(n)) for n, (i, level) in enumerate(got))

    got.clear()
    engine.load(str(movie))
    qtbot.waitUntil(lambda: engine._ready, timeout=5000)
    got.clear()
    engine.seek(10)
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got[0][0] == 10 and _close(got[0][1], _level(10))
    engine.stop()


@pytest.fixture
def player(qtbot):
    from slate.gui.widgets.advanced_player import AdvancedPlayer
    p = AdvancedPlayer()
    qtbot.addWidget(p)
    p.resize(320, 240)
    p.show()
    yield p
    p.stop_media()


@pytest.mark.parametrize("gap", [False, True])
def test_player_plays_a_sequence_to_its_last_frame(qtbot, player, tmp_path, gap):
    """
    FOUND + FIXED (gap): ffmpeg's image reader stops at a missing frame, so a
    render with 1005 missing played four frames and stopped, without a word.
    The missing frame is now held, as RV holds it.
    """
    folder = tmp_path / "seq"
    folder.mkdir()
    ffmpeg("-f", "lavfi", "-i", GREY, "-frames:v", 12, "-start_number", 1001,
           folder / "grey.%04d.png")
    if gap:
        (folder / "grey.1005.png").unlink()
    player.load(str(folder / "grey.1001.png"))
    qtbot.waitUntil(lambda: player.media_kind != "", timeout=5000)
    engine = player.engines["sequence"]
    assert player.active_engine is engine
    qtbot.waitUntil(lambda: engine._ready, timeout=5000)
    assert engine.total_frames == 12

    engine.seek(0)
    got = _collect(engine)
    _play_to_end(qtbot, engine, got, 12)
    expected = [3 if (gap and n == 4) else n for n in range(12)]
    assert [i for i, _ in got] == list(range(12))
    assert all(_close(level, _level(n)) for (_, level), n in zip(got, expected)), got
