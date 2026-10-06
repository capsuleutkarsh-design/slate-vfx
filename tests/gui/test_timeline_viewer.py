"""
The Timeline Viewer: the lineup table, its player, RV and the EDLs.
Finding ids are named on each test. RV itself is never started here.
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
def editor(qtbot):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
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
    assert not editor.btn_edl.isEnabled() and not editor.btn_rv.isEnabled()


def test_shots_without_scans_are_listed_and_counted(qtbot, editor, tmp_path):
    shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020", scan=False),
             _shot(tmp_path, "SH030", scan=False)]
    _load(qtbot, editor, tmp_path, shots)
    assert editor.table.rowCount() == 3                                             # MED-087
    assert "2 shots without a scan" in editor.status_label.text()                    # MED2-050
    assert editor.table.item(1, 3).text() == "no scan yet"
    assert editor.btn_edl.isEnabled() and editor.btn_rv.isEnabled()


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


def test_make_review_proxies_needs_a_ticked_shot(qtbot, editor, tmp_path):
    """A7: it could be pressed with nothing ticked."""
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010")])
    assert editor.btn_proxy.isEnabled()
    editor.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not editor.btn_proxy.isEnabled()


def test_export_writes_edls_into_the_project(qtbot, editor, tmp_path, monkeypatch):
    from slate.gui.tabs.shot_review import lineup_editor_mode as mod
    shots = [_shot(tmp_path, "SH010")] + [_shot(tmp_path, f"X{i:02d}", scan=False) for i in range(25)]
    _load(qtbot, editor, tmp_path, shots)
    seen = []
    monkeypatch.setattr(mod.ExportResultDialog, "exec", lambda self: seen.append(self) or 1)
    result = editor.sync_lineup()
    assert result.ok and result.combined.parent == tmp_path / "editorial" / "lineups"   # MED-093
    assert result.combined.suffix == ".edl"
    assert "and 5 more" in seen[0].skipped_text                                         # MED-092
    assert editor.sync_time_label.text().startswith("EDL written today")                # MED-105


def test_open_in_rv_plays_the_ticked_shots_in_order(qtbot, editor, tmp_path, monkeypatch):
    from slate.core.domain import rv_review
    from slate.core.domain.proxy_builder import proxy_path_for
    shots = [_shot(tmp_path, "SH020"), _shot(tmp_path, "SH010"), _shot(tmp_path, "SH030")]
    _load(qtbot, editor, tmp_path, shots)
    editor.table.item(2, 0).setCheckState(Qt.CheckState.Unchecked)                 # leave out SH030
    proxy = proxy_path_for(editor.lineup[1].clips["scan"], "SH020")
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"mp4")
    sent = []
    monkeypatch.setattr(rv_review, "launch", lambda paths, launcher=None: sent.append(paths) or True)
    assert editor.open_in_rv()
    assert [p.split("\\")[-1].split("/")[-1] for p in sent[0]] == ["SH010.%04d.exr", "SH020_scan_proxy.mp4"]
    editor.chk_prefer_proxy.setChecked(False)
    editor.open_in_rv()
    assert sent[1][1].endswith("SH020.%04d.exr")


def test_rv_that_will_not_start_says_so(qtbot, editor, tmp_path, monkeypatch):
    from slate.core.domain import rv_review
    from slate.gui.components import feedback
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010")])
    monkeypatch.setattr(rv_review, "launch", lambda paths, launcher=None: False)
    said = []
    monkeypatch.setattr(feedback, "warn", lambda *a, **k: said.append(a))
    assert editor.open_in_rv() is False
    assert "tell IT" in said[0][2]                                                       # MED2-063


def test_buttons_and_header_read_cleanly(qtbot):
    from PySide6.QtWidgets import QLabel, QWidget
    from slate.gui.tabs.vfx_review_dual_mode_tab import VFXReviewDualModeTab
    tab = VFXReviewDualModeTab(None)
    qtbot.addWidget(tab)
    assert tab.lineup_editor.btn_rv.property("kind") == "primary"                        # MED-100
    texts = [w.text() for w in tab.findChildren(QLabel)]
    assert "Timeline" in texts and "TIMELINE VIEWER" not in texts                        # MED-101
    header = tab.findChild(QWidget, "TimelineHeader")
    assert header.styleSheet().startswith("QWidget#TimelineHeader")                      # MED-099


def test_plurals_and_friendly_times():
    from slate.core.domain.lineup import plural
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


# ------------------------------------------------------------------ round 2

def test_another_project_does_not_show_the_last_ones_edl(qtbot, editor, tmp_path):
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010")])
    folder = tmp_path / "editorial" / "lineups"
    folder.mkdir(parents=True)
    (folder / "PRJ_All_Reels_scan.edl").write_text("x")
    editor._read_last_sync()
    assert editor.sync_time_label.text() != "No EDL yet"
    other = tmp_path / "other"
    editor.set_project_context("OTHER")
    editor.set_project_source(other)
    editor.set_shots([_shot(other, "SH900")])
    qtbot.waitUntil(lambda: editor._scan_job is None, timeout=10000)
    assert editor.sync_time_label.text() == "No EDL yet"                                  # MED2-040


def test_a_movie_plate_gives_its_rate_and_length(tmp_path):
    from pathlib import Path
    from slate.core.domain import lineup
    from slate.core.domain.shot_media import MediaClip
    clip = MediaClip(path=Path(tmp_path / "SH010_scan.mov"), department="scan")
    fake = {"fps": 25.0, "duration_sec": 2.0, "width": 1920, "height": 1080}
    from slate.core.domain.metadata_engine import SmartMetadataManager
    import unittest.mock as mock
    with mock.patch.object(SmartMetadataManager, "extract_tech_metadata", return_value=fake):
        assert lineup.plate_facts(clip) == (25.0, 50)                          # MED2-041/042
    shot = Shot(shot_name="SH010")
    assert lineup._frame_range(shot, clip, 50) == (1, 50)
    seq = MediaClip(path=Path("x.%04d.exr"), is_sequence=True, first_frame=1001, last_frame=1008)
    assert lineup.plate_facts(seq) == (24.0, 0)


def test_ticking_keeps_the_shot_being_watched(qtbot, editor, tmp_path):
    shots = [_shot(tmp_path, f"SH0{i}0") for i in range(1, 5)]
    _load(qtbot, editor, tmp_path, shots)
    preview = editor.preview
    preview.show_shot(2)
    editor.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)                 # untick SH010
    assert preview.entries[preview.index].name == "SH030"                           # MED2-043


def test_a_new_layer_keeps_play_lineup_playing(qtbot, editor, tmp_path):
    _load(qtbot, editor, tmp_path, [_shot(tmp_path, "SH010")])
    preview = editor.preview
    asked = []
    preview.show_shot = lambda index, autoplay=False: asked.append(autoplay)
    preview.continuous = True
    preview.combo_layer.addItem("Comp", "comp")
    preview.combo_layer.setCurrentIndex(preview.combo_layer.count() - 1)
    assert asked == [True]                                                            # MED2-044


def test_an_unreadable_share_is_not_an_empty_project(qtbot, editor):
    editor._on_rows(None, OSError("share down"))
    assert "could not be read" in editor.status_label.text()                         # MED2-045
    assert "No shots loaded" not in editor.status_label.text()


def test_the_strip_does_not_inflate_unknown_lengths(qtbot):
    from slate.core.domain.lineup import LineupShot
    from slate.gui.tabs.shot_review.lineup_preview import LineupStrip
    strip = LineupStrip()
    qtbot.addWidget(strip)
    strip.resize(400, 30)
    strip.set_entries([LineupShot("A", frame_range=(1, 8)), LineupShot("B"),
                       LineupShot("C", frame_range=(1, 8))])
    widths = [rect.width() for _i, _e, rect in strip._blocks()]
    assert abs(widths[1] - widths[0]) < 2                                             # MED2-046
    assert LineupShot("B").frames_text() == "length unknown"


def test_renders_show_their_version_and_rv_words(qtbot, tmp_path):
    from pathlib import Path
    from slate.core.domain.rv_review import ReviewOption, ReviewRequest
    from slate.core.domain.shot_media import MediaClip
    from slate.gui.dialogs.rv_review_dialog import RVReviewDialog
    comp = ReviewOption("comp", "Comp", MediaClip(path=Path("C:/s/SEQ010_SH020_comp_v002.mov"),
                                                  department="comp"))
    assert comp.version == "v002"                                                     # MED2-062
    dialog = RVReviewDialog(ReviewRequest("SH020", [comp]), launcher=object())
    qtbot.addWidget(dialog)
    assert (dialog.btn_all.text(), dialog.btn_none.text()) == ("Tick all", "Untick all")  # MED2-052
    import inspect as _inspect
    assert "RV_PATH" not in _inspect.getsource(RVReviewDialog.open_in_rv)            # MED2-063


def test_the_preview_is_clear_of_the_splitter_and_talks_about_shots(qtbot):
    from slate.gui.tabs.shot_review.lineup_preview import LineupPreview
    preview = LineupPreview()
    qtbot.addWidget(preview)
    assert preview.layout().contentsMargins().left() >= 8                            # MED2-051
    assert preview.player.btn_next.toolTip() == "Next shot"                         # MED2-056
