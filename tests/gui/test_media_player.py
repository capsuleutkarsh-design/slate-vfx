"""
The preview player (AdvancedPlayer), its engines and Quick Look, on
generated media.

    MED-028 aspect kept      MED-109 paused means paused   MED-110 no probe on the UI thread
    MED-111 readable time    MED-112 colour combos only for EXR   MED-113 full-screen icon
    MED-114/117 icons, keys  MED-115 stills have no transport     MED-116 full-res snapshot
    MED-118 sound            MED-119 messages wrap        MED-120/121 Quick Look
    MED-123 slider range     MED-124 EXR colourspace
"""

import os
import subprocess
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QImage, QKeyEvent

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = ROOT / "slate" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG.exists(), reason="bundled ffmpeg not present")


def _picture(path: Path, w=64, h=36):
    image = QImage(w, h, QImage.Format.Format_RGB888)
    image.fill(QColor(40, 90, 160))
    image.save(str(path))
    return path


def _movie(path: Path, w, h, seconds=1, audio=False):
    cmd = [str(FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
           "-i", f"testsrc=size={w}x{h}:rate=24:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac",
                "-shortest"]
    cmd += ["-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, timeout=60)
    return path


@pytest.fixture
def player(qtbot):
    from slate.gui.widgets.advanced_player import AdvancedPlayer
    p = AdvancedPlayer()
    qtbot.addWidget(p)
    p.resize(640, 480)
    p.show()
    yield p
    p.stop_media()


def _load(qtbot, player, path, **kw):
    player.load(str(path), **kw)
    qtbot.waitUntil(lambda: player.media_kind != "", timeout=5000)


def test_stills_have_no_transport(qtbot, player, tmp_path):
    _load(qtbot, player, _picture(tmp_path / "still.jpg"))
    assert player.media_kind == "image"
    assert not player.btn_play.isVisible() and not player.slider.isVisible()        # MED-115
    assert not player.combo_view.isVisible()                                         # MED-112
    assert player.btn_snap.isEnabled() and player.btn_next.isVisible()


def test_the_slider_ends_on_the_last_frame(player):
    player.set_duration(48)
    assert player.slider.maximum() == 47                                             # MED-123
    player.set_duration(1100)
    player.update_time_label(1099)
    assert player.lbl_time.text() == "1100 / 1100"
    width = player.lbl_time.fontMetrics().horizontalAdvance(player.lbl_time.text())
    assert player.lbl_time.minimumWidth() >= width                                    # MED-111


def test_icons_and_keys_in_tooltips(player):
    for button in (player.btn_prev, player.btn_step_back, player.btn_play,
                   player.btn_step_forward, player.btn_next, player.btn_fullscreen):
        assert not button.icon().isNull() and button.text() == ""                   # MED-114
    assert "←" in player.btn_step_back.toolTip() and "(F)" in player.btn_fullscreen.toolTip()  # MED-117


def test_long_messages_wrap(player):
    player.screen.set_text("This file could not be played. " * 6)
    assert player.screen.message_rect().width() < player.screen.width()              # MED-119


@needs_ffmpeg
def test_a_clip_loads_paused_at_its_own_shape(qtbot, player, tmp_path):
    clip = _movie(tmp_path / "phone.mp4", 360, 640)
    _load(qtbot, player, clip)
    engine = player.active_engine
    qtbot.waitUntil(lambda: engine._ready, timeout=10000)
    assert abs(engine.render_w / engine.render_h - 360 / 640) < 0.02                # MED-028
    assert not engine.is_playing() and not engine.playback_timer.isActive()         # MED-109
    assert player.btn_play.property("playing") is False
    player.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    assert engine.is_playing() and player.btn_play.property("playing") is True       # MED-117
    player.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier))
    assert not engine.is_playing()


@needs_ffmpeg
def test_sound_is_found_or_said_missing(qtbot, player, tmp_path):
    loud = _movie(tmp_path / "boom.mp4", 160, 90, audio=True)
    _load(qtbot, player, loud)
    qtbot.waitUntil(lambda: player.has_audio(), timeout=10000)                       # MED-118
    assert player.btn_mute.isVisible() and not player.lbl_no_audio.isVisible()
    player.set_muted(True)
    assert player.audio.is_muted()
    quiet = _movie(tmp_path / "quiet.mp4", 160, 90)
    _load(qtbot, player, quiet)
    qtbot.wait(800)
    assert not player.has_audio() and player.lbl_no_audio.isVisible()


def test_a_sequence_load_does_not_wait_for_the_probe(qtbot, tmp_path, monkeypatch):
    from slate.core.domain import metadata_engine
    from slate.gui.widgets.media_engines.sequence_engine import SequenceEngine
    for f in range(1001, 1004):
        _picture(tmp_path / f"plate.{f}.png")
    monkeypatch.setattr(metadata_engine.SmartMetadataManager, "extract_tech_metadata",
                        staticmethod(lambda p: time.sleep(2) or {"width": 64, "height": 36}))
    engine = SequenceEngine()
    engine.set_sequence_details(str(tmp_path / "plate.%04d.png"), 1001, 3)
    started = time.monotonic()
    engine.load(str(tmp_path / "plate.1001.png"))
    assert time.monotonic() - started < 0.5                                          # MED-110
    engine.stop()


def test_a_snapshot_is_full_size_and_said(qtbot, player, tmp_path, monkeypatch):
    big = _picture(tmp_path / "plate.png", 1920, 1080)
    _load(qtbot, player, big)
    from slate.gui.components import feedback
    told = []
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: told.append((a, k)))
    out = tmp_path / "snap.png"
    saved = []
    player.snapshot_saved.connect(saved.append)
    player.take_snapshot(str(out))
    qtbot.waitUntil(lambda: bool(saved), timeout=5000)
    image = QImage(saved[0])
    assert (image.width(), image.height()) == (1920, 1080)                          # MED-116
    assert told and "Open folder" in str(told[0][1].get("action"))


def test_full_screen_is_one_icon(qtbot, player):
    tip = player.btn_fullscreen.toolTip()
    player.toggle_fullscreen()
    assert player.btn_fullscreen.toolTip() != tip and player.btn_fullscreen.text() == ""  # MED-113
    player.toggle_fullscreen()
    assert player.btn_fullscreen.toolTip() == tip


def test_quick_look_fits_and_walks_the_list(qtbot, tmp_path, monkeypatch):
    from slate.gui.widgets.quick_look import QuickLookDialog
    from slate.gui.components import screen_fit
    from PySide6.QtCore import QSize
    monkeypatch.setattr(screen_fit, "available_size", lambda w=None: QSize(1366, 728))
    a, b = _picture(tmp_path / "a.png"), _picture(tmp_path / "b.png")
    steps = []
    dialog = QuickLookDialog(None, "a", str(a), navigator=lambda s: steps.append(s) or ("b", str(b)))
    qtbot.addWidget(dialog)
    assert dialog.height() <= 728 * 0.8 + 1 and dialog.width() <= 1366 * 0.8 + 1   # MED-120
    dialog.player.btn_next.click()
    assert steps == [1] and dialog.player.current_path == str(b)                    # MED-121
    plain = QuickLookDialog(None, "a", str(a))
    qtbot.addWidget(plain)
    assert not plain.player.btn_next.isVisibleTo(plain)


def test_exr_colourspace_comes_from_the_file():
    from slate.gui.widgets.media_engines.image_engine import detect_input_space
    rec709 = (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.329)
    assert detect_input_space({"chromaticities": rec709}) == "Linear Rec.709 (sRGB)"   # MED-124
    assert detect_input_space({"oiio:ColorSpace": "ACEScg"}) == "ACEScg"
    assert detect_input_space({}, default="ACEScg") == "ACEScg"
