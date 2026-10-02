"""
The Timeline Viewer: the lineup table, its player, and the Olive bridge.
Finding ids are named on each test. Olive itself is never started here.
"""

import datetime
import inspect

import pytest
from PySide6.QtCore import Qt

from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


def _frames(folder, basename, frames):
    folder.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (folder / f"{basename}.{frame:04d}.exr").write_bytes(b"x")


def _shot(tmp_path, name, reel="R1", scan=True):
    if scan:
        _frames(tmp_path / "05_Reels" / reel / name / "01_Scan" / "v001" / "EXR", name,
                range(1001, 1011))
    return Shot(shot_name=name, reel_episode=reel,
                folder_paths={"scan": f"05_Reels/{reel}/{name}/01_Scan"})


@pytest.fixture
def editor(qtbot, monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    monkeypatch.setattr(mod.LineupEditorMode, "_find_olive_executable", lambda self: None)
    e = mod.LineupEditorMode()
    qtbot.addWidget(e)
    e.resize(1400, 800)
    return e


def _load(qtbot, editor, tmp_path, shots):
    editor.set_project_context("PRJ")
    editor.set_project_source(tmp_path)
    editor.set_shots(shots)
    qtbot.waitUntil(lambda: editor._scan_job is None, timeout=10000)


def test_it_starts_empty_and_says_what_to_do(editor):
    assert "No shots loaded" in editor.status_label.text()                         # MED-086
    assert not editor.btn_sync.isEnabled()
    assert not editor.btn_launch.isEnabled()                                        # MED-094, 095
    assert "setup.bat" in editor.btn_launch.toolTip()


def test_shots_without_scans_are_listed_and_counted(qtbot, editor, tmp_path):
    shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020", scan=False),
             _shot(tmp_path, "SH030", scan=False)]
    _load(qtbot, editor, tmp_path, shots)
    assert editor.table.rowCount() == 3                                             # MED-087
    assert "2 without scans" in editor.status_label.text()
    assert editor.table.item(1, 3).text() == "no scan yet"
    assert editor.btn_sync.isEnabled()


def test_the_table_filters_and_leaves_out_unticked_shots(qtbot, editor, tmp_path):
    shots = [_shot(tmp_path, "SH010", "R1"), _shot(tmp_path, "SH020", "R1"),
             _shot(tmp_path, "SH030", "R2")]
    _load(qtbot, editor, tmp_path, shots)
    assert [e.name for e in editor.included_lineup()] == ["SH010", "SH020", "SH030"]
    editor.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)                 # MED-088
    assert [e.name for e in editor.included_lineup()] == ["SH010", "SH030"]
    editor.combo_reel.setCurrentIndex(editor.combo_reel.findData("R2"))
    assert [editor.table.isRowHidden(i) for i in range(3)] == [True, True, False]
    assert len(editor.preview.entries) == 2


def test_sync_writes_into_the_project_and_enables_launch(qtbot, editor, tmp_path, monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    monkeypatch.setattr(mod.LineupEditorMode, "_find_olive_executable", lambda self: tmp_path)
    editor._olive_path = tmp_path / "olive-editor.exe"
    shots = [_shot(tmp_path, "SH010")] + [_shot(tmp_path, f"X{i:02d}", scan=False) for i in range(25)]
    _load(qtbot, editor, tmp_path, shots)
    seen = []
    monkeypatch.setattr(mod.SyncResultDialog, "exec", lambda self: seen.append(self) or 1)
    result = editor.sync_lineup()
    assert result.ok and result.combined.parent == tmp_path / "editorial" / "lineups"   # MED-093
    assert "and 5 more" in seen[0].skipped_text                                         # MED-092
    assert editor.btn_launch.isEnabled()                                                # MED-095
    assert editor.sync_time_label.text().startswith("Synced today")                    # MED-105


def test_resync_with_olive_open_offers_a_reload(qtbot, editor, tmp_path, monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010")])

    class Running:
        pid = 4242
        def poll(self): return None
    editor.olive_process = Running()
    monkeypatch.setattr(mod.SyncResultDialog, "exec", lambda self: 1)
    monkeypatch.setattr(editor, "_ask_reload", lambda: True)
    reloaded = []
    monkeypatch.setattr(editor, "reload_olive", lambda: reloaded.append(1))
    editor.sync_lineup()
    assert reloaded == [1]                                                               # MED-096
    editor.olive_process = None


def test_back_to_lineup_keeps_a_way_back(qtbot, editor, tmp_path):
    class Running:
        pid = 1
        def poll(self): return None
    editor.olive_process = Running()
    editor._show_stage("Olive")
    editor.back_to_lineup()
    assert editor.btn_return.isVisibleTo(editor)                                          # MED-082
    editor.return_to_olive()
    assert editor.olive_container.isVisibleTo(editor)
    editor.olive_process = None


def test_an_olive_that_cannot_be_embedded_says_so(qtbot, editor, monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod

    class Running:
        pid = 99999
        def poll(self): return None
    monkeypatch.setattr(mod, "find_olive_window", lambda target_pid=0, windows=None: None)
    editor.olive_process = Running()
    editor._show_stage("Starting Olive…")
    editor._start_embedding()
    for _ in range(mod.EMBED_ATTEMPTS + 1):
        editor.try_embed_olive()
    assert "could not be brought into Slate" in editor.stage_label.text()              # MED-083
    assert editor.btn_show_window.isVisibleTo(editor) and editor.btn_retry_embed.isVisibleTo(editor)
    assert editor.compact_status.text() == "Could not embed Olive"
    editor.embed_timer.stop()
    editor.olive_process = None


def test_only_our_olive_window_is_taken(monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    monkeypatch.setattr(mod, "_olive_pids", lambda pid: {pid})
    windows = [(11, 500), (12, 600)]          # 500: somebody's "Olive oil order" tab
    assert mod.find_olive_window(600, windows=windows) == 12                            # MED-081
    assert mod.find_olive_window(700, windows=windows) is None
    assert mod.find_olive_window(0, windows=windows) is None


def test_nothing_is_ever_force_killed():
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    source = inspect.getsource(mod)
    assert "taskkill" not in source                                                       # MED-080
    assert "user32 = ctypes.windll" not in source                                         # MED-107


def test_an_open_olive_is_asked_about_not_closed(qtbot, editor, tmp_path, monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    from slate.gui.components import feedback
    editor._olive_path = tmp_path / "olive-editor.exe"
    editor.output_path = tmp_path / "x.ovexml"
    editor.output_path.write_text("x")
    monkeypatch.setattr(mod, "other_olive_running", lambda own_pid=0: True)
    asked = []
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: asked.append(a) or False)
    started = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: started.append(a))
    editor.launch_olive()
    assert asked and started == []


def test_buttons_and_header_read_cleanly(qtbot):
    from PySide6.QtWidgets import QLabel, QWidget
    from slate.gui.tabs.vfx_review_dual_mode_tab import VFXReviewDualModeTab
    tab = VFXReviewDualModeTab(None)
    qtbot.addWidget(tab)
    assert tab.lineup_editor.btn_sync.property("kind") == "primary"                      # MED-100
    assert tab.lineup_editor.btn_close_olive.property("kind") == "danger"
    texts = [w.text() for w in tab.findChildren(QLabel)]
    assert "Timeline" in texts and "TIMELINE VIEWER" not in texts                        # MED-101
    header = tab.findChild(QWidget, "TimelineHeader")
    assert header.styleSheet().startswith("QWidget#TimelineHeader")                      # MED-099


def test_plurals_and_friendly_times():
    from slate.core.domain.olive_lineup import plural
    from slate.gui.tabs.shot_review.lineup_editor_mode import friendly_time
    assert plural(1, "shot") == "1 shot" and plural(3, "proxy", "proxies") == "3 proxies"  # MED-101
    now = datetime.datetime(2026, 10, 2, 20, 0)
    assert friendly_time(datetime.datetime(2026, 10, 2, 19, 24), now) == "today 19:24"
    assert friendly_time(datetime.datetime(2026, 10, 1, 9, 5), now) == "yesterday 09:05"


# ----------------------------------------------------------------- the player

def test_the_player_shows_the_lineup(qtbot, editor, tmp_path):
    shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020")]
    _load(qtbot, editor, tmp_path, shots)
    preview = editor.preview
    assert [e.name for e in preview.strip.entries] == ["SH010", "SH020"]
    assert preview.index == 0 and "SH010" in preview.lbl_shot.text()
    assert preview.btn_make_proxy.isVisibleTo(preview)            # EXR frames: offer a proxy
    preview.strip.shot_clicked.emit(1)
    assert preview.index == 1
    assert editor.table.currentRow() == 1


def test_a_proxy_is_preferred_when_there_is_one(qtbot, editor, tmp_path):
    from slate.gui.tabs.shot_review.lineup_preview import media_for
    from slate.core.domain.proxy_builder import proxy_path_for
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010")])
    entry = editor.lineup[0]
    path, kind, _clip = media_for(entry, "comp")
    assert kind == "frames" and path.name == "SH010.1001.exr"
    proxy = proxy_path_for(entry.clips["scan"], "SH010")
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"mp4")
    path, kind, _clip = media_for(entry, "scan")
    assert kind == "proxy" and path == proxy


def test_play_lineup_moves_on_when_a_shot_ends(qtbot, editor, tmp_path):
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020")])
    preview = editor.preview
    preview.continuous = True
    preview._seen_playing = True
    preview._on_finished()
    assert preview.index == 1
    preview._seen_playing = True
    preview._on_finished()                          # the last one: stops
    assert not preview.btn_play_all.isChecked()


def test_the_rv_picker_reads_the_same_on_every_row(qtbot, tmp_path):
    """MED-122: printf patterns and mixed details; Select all / none."""
    from pathlib import Path
    from slate.core.domain.rv_review import ReviewOption, ReviewRequest
    from slate.core.domain.shot_media import MediaClip
    from slate.gui.dialogs.rv_review_dialog import RVReviewDialog
    plate = ReviewOption("scan", "Scan", MediaClip(path=Path("C:/s/SH010.%04d.exr"), department="scan",
                                                   is_sequence=True, first_frame=1001, last_frame=1008,
                                                   scan_version="v001"))
    comp = ReviewOption("comp", "Comp", MediaClip(path=Path("C:/s/SH010_comp.mov"), department="comp"))
    assert plate.detail == "v001 \u00b7 1001\u20131008 (8 f)" and "%" not in plate.detail
    assert comp.detail == "SH010_comp.mov"
    dialog = RVReviewDialog(ReviewRequest("SH010", [plate, comp]), launcher=object())
    qtbot.addWidget(dialog)
    assert dialog.selected_keys() == ["scan"]
    dialog.btn_all.click()
    assert dialog.selected_keys() == ["scan", "comp"] and dialog.open_btn.text() == "Open 2 in RV"
    dialog.btn_none.click()
    assert dialog.selected_keys() == [] and not dialog.open_btn.isEnabled()


def test_shot_names_stay_readable_at_1280(qtbot, editor, tmp_path):
    """NEW-media-5: the Shot column was 55 px; the strip read 'SEQ010_S...'."""
    editor.resize(1280, 640)
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, f"SEQ0{i}0_SH010") for i in range(1, 4)])
    editor.show()
    qtbot.wait(50)
    shot_col = editor.table.columnWidth(2)
    needed = editor.table.fontMetrics().horizontalAdvance("SEQ010_SH010")
    assert shot_col >= needed
    from slate.gui.tabs.shot_review.lineup_preview import strip_label
    metrics = editor.preview.strip.fontMetrics()
    assert strip_label(metrics, "SEQ010_SH010", 1000) == "SEQ010_SH010"
    assert strip_label(metrics, "SEQ010_SH010", metrics.horizontalAdvance("SH010") + 2) == "SH010"
