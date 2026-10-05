"""
The Stock Viewer on screen, against a real (SQLite) library.

These replace the old tests here, which ended in "or True" and so could not
fail (MED-077). Finding ids are named on each test.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel

ADMIN = ["admin"]
ARTIST = ["artist"]


def _picture(path: Path, w=64, h=36, colour=(30, 120, 200)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(w, h, QImage.Format.Format_RGB888)
    image.fill(QColor(*colour))
    image.save(str(path))
    return path


@pytest.fixture
def library(mock_db):
    from slate.core.domain.library_manager import LibraryManager
    return LibraryManager(mock_db, username="priya")


def _seed(lib, tmp_path, count=5, folder="Stock/Fire", category="Fire", **extra):
    assets = []
    for i in range(count):
        p = tmp_path / folder / f"clip_{i:03d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (i + 1))
        assets.append({"file_path": str(p), "category": category,
                       "metadata": {"width": 1920, "height": 1080, "is_still": True,
                                    "codec": "mjpeg"}, "tags": ["Fire"], **extra})
    lib.add_assets_batch(assets)
    return assets


def _wait(qtbot, tab):
    qtbot.waitUntil(lambda: tab.loader_thread is None and not tab.is_loading, timeout=10000)


def _tab(qtbot, library, roles=ADMIN, user="priya", load=True):
    from slate.gui.tabs.stock_browser_tab import StockBrowserTab
    tab = StockBrowserTab(library, user_roles=roles, user_data={"user_id": user})
    qtbot.addWidget(tab)
    tab.resize(1600, 900)
    if load:
        tab.show()              # the first load starts when the tab is shown
        _wait(qtbot, tab)
    return tab


def _select(tab, rows):
    from PySide6.QtCore import QItemSelectionModel
    sm = tab.gallery.asset_view.selectionModel()
    sm.clearSelection()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows[:-1]:
        sm.select(tab.proxy_model.index(row, 0), flags)
    sm.setCurrentIndex(tab.proxy_model.index(rows[-1], 0), flags)


# ------------------------------------------------------------------ loading

def test_the_count_is_the_library_total(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=320)
    tab = _tab(qtbot, library)
    qtbot.wait(300)                 # a second page may follow when the first does not fill the view
    _wait(qtbot, tab)
    assert tab.db_total == 320
    loaded = tab.model.rowCount()
    assert loaded in (300, 320)
    # The rest loads on scroll; nothing is filtered, so no "300 of 320" (MED2-009).
    assert tab.gallery.lbl_count.text() == "320 assets"                     # MED-006, MED-069


def test_count_text_reads_well():
    from slate.gui.tabs.stock_browser.ui.gallery import count_text
    assert count_text(1, 1) == "1 asset"
    assert count_text(0, 0) == "0 assets"
    assert count_text(120, 1860) == "Showing 120 of 1,860 assets"
    assert count_text(120, 1860, compact=True) == "120 / 1,860"


def test_the_category_highlight_stays(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3, category="Fire")
    _seed(library, tmp_path, count=2, folder="Stock/Smoke", category="Smoke")
    tab = _tab(qtbot, library)
    texts = tab.sidebar.category_texts()
    assert texts[:3] == ["All", "Favorites", "Studio picks"] and "Smoke" in texts   # MED-012
    item = next(tab.sidebar.category_list.item(r) for r in range(tab.sidebar.category_list.count())
                if tab.sidebar.category_list.item(r).data(Qt.ItemDataRole.UserRole) == "Smoke")
    assert "(2)" in item.text()                                                       # MED-068
    tab.sidebar._on_category_clicked(item)
    _wait(qtbot, tab)
    assert tab.model.rowCount() == 2 and tab.db_total == 2                           # MED-007
    assert tab.sidebar.category_list.currentItem().data(Qt.ItemDataRole.UserRole) == "Smoke"  # MED-013


def test_an_empty_library_says_so_to_each_role(qtbot, library):
    tab = _tab(qtbot, library, roles=ARTIST)
    assert tab.gallery.stack.currentWidget() is tab.gallery.empty_state
    assert "Ask a lead" in tab.gallery.empty_state._detail.text()                  # MED-042
    admin = _tab(qtbot, library, roles=ADMIN)
    assert "Drag folders" in admin.gallery.empty_state._detail.text()


def test_no_results_offers_to_clear(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    tab.gallery.search_bar.setText("nothing-like-this")
    tab.apply_filters()
    _wait(qtbot, tab)
    assert tab.gallery.stack.currentWidget() is tab.gallery.no_results
    tab.clear_all_filters()                                                          # MED-063
    _wait(qtbot, tab)
    assert tab.gallery.search_bar.text() == "" and tab.model.rowCount() == 3


def test_esc_clears_the_search(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    tab.gallery.search_bar.setText("smoke")
    qtbot.keyClick(tab.gallery.search_bar, Qt.Key.Key_Escape)
    assert tab.gallery.search_bar.text() == ""
    assert tab.gallery.search_bar.isClearButtonEnabled()


# ---------------------------------------------------------------- selection

def test_the_inspector_follows_the_current_item_and_clears(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=6)
    tab = _tab(qtbot, library)
    _select(tab, [5, 2])
    current = tab.proxy_model.index(2, 0).data(Qt.ItemDataRole.UserRole)
    assert tab.inspector.current_asset["file_path"] == current["file_path"]         # MED-074
    assert not tab.inspector.player._pending_autoplay                               # MED-073
    tab.gallery.asset_view.selectionModel().clearSelection()
    tab.on_selection_changed()
    assert tab.inspector.current_asset is None                                      # MED-019
    assert tab.inspector.lbl_name.text() == "Nothing selected"


def test_copy_puts_one_path_per_line(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    _select(tab, [0, 1])
    paths = tab.gallery.copy_selection()
    text = QApplication.clipboard().text()
    assert text.splitlines() == paths and len(paths) == 2                           # MED-018
    assert "\\n" not in text


def test_delete_needs_a_selection_and_says_how_many(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    assert not tab.sidebar.btn_delete_selected.isEnabled()                          # MED-033
    _select(tab, [0, 1])
    assert tab.sidebar.btn_delete_selected.isEnabled()
    assert tab.sidebar.btn_delete_selected.text() == "Delete (2)"


def test_delete_asks_in_plain_lines_and_can_be_undone(qtbot, library, tmp_path, monkeypatch):
    _seed(library, tmp_path, count=4)
    tab = _tab(qtbot, library)
    asked, toasts = [], []
    from slate.gui.components import feedback
    monkeypatch.setattr(feedback, "confirm", lambda parent, title, text, **kw: asked.append(
        (title, text, kw)) or True)
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", details="", action=None:
                        toasts.append((msg, level, action)))
    _select(tab, [0, 1])
    tab.delete_selected_assets()
    title, text, kw = asked[0]
    assert "\\n" not in text and "\\n" not in kw["informative"]                     # MED-017
    assert kw["destructive"]
    assert tab.model.rowCount() == 2 and tab.db_total == 2
    message, level, action = toasts[-1]
    assert level == "success" and action[0] == "Undo"                               # MED-034
    action[1]()
    _wait(qtbot, tab)
    assert tab.model.rowCount() == 4


def test_only_the_assets_really_deleted_leave_the_list(qtbot, library, tmp_path, monkeypatch):
    _seed(library, tmp_path, count=1, folder="A")
    _seed(library, tmp_path, count=1, folder="B")         # same file name, other folder
    tab = _tab(qtbot, library)
    _select(tab, [0, 1])
    monkeypatch.setattr(tab, "_confirm_delete", lambda *a: True)
    monkeypatch.setattr(tab, "_notify", lambda *a, **k: None)
    first, keep = tab.gallery.selected_assets()
    monkeypatch.setattr(tab.lib_manager, "delete_assets", lambda assets: [int(first["id"])])
    tab.delete_selected_assets()
    assert [a["file_path"] for a in tab.model.assets] == [keep["file_path"]]        # MED-076


def test_clear_library_needs_the_word(qtbot):
    from slate.gui.tabs.stock_browser.ui.dialogs import ClearLibraryDialog
    dialog = ClearLibraryDialog(434)
    qtbot.addWidget(dialog)
    words = " ".join(l.text() for l in dialog.findChildren(QLabel))
    assert "434" in words and "cannot be undone" in words and "everybody" in words  # MED-014
    assert not dialog.btn_clear.isEnabled()
    assert dialog.btn_cancel.isDefault()
    dialog.edit.setText("clea")
    assert not dialog.btn_clear.isEnabled()
    dialog.edit.setText("clear")                     # any case (MED2-034)
    assert dialog.btn_clear.isEnabled()


def test_after_clearing_the_library_is_empty(qtbot, library, tmp_path, monkeypatch):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    _select(tab, [0])
    from slate.gui.tabs.stock_browser.ui import dialogs
    monkeypatch.setattr(dialogs.ClearLibraryDialog, "confirmed", lambda self: True)
    monkeypatch.setattr(tab, "_notify", lambda *a, **k: None)
    tab.clear_entire_library()
    assert tab.model.rowCount() == 0 and tab.db_total == 0
    assert tab.gallery.stack.currentWidget() is tab.gallery.empty_state            # MED-016
    assert tab.inspector.current_asset is None
    assert tab.sidebar.btn_clear.text() == "Clear library…"


# -------------------------------------------------------------- favourites

def test_star_in_the_inspector_fills_favorites(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    _select(tab, [1])
    asset = tab.inspector.current_asset
    tab.inspector.btn_favorite.click()
    assert library.list_favorites() == {int(asset["id"])}                          # MED-009
    tab.on_category_changed("Favorites")
    _wait(qtbot, tab)
    assert [a["file_path"] for a in tab.model.assets] == [asset["file_path"]]


def test_tags_can_be_edited_by_managers_only(qtbot, library, tmp_path, monkeypatch):
    _seed(library, tmp_path, count=1)
    tab = _tab(qtbot, library)
    _select(tab, [0])
    from slate.gui.widgets import tag_edit_dialog
    monkeypatch.setattr(tag_edit_dialog.TagEditDialog, "get_tags", lambda self: ["fire", "smoke"])
    monkeypatch.setattr(tab, "_notify", lambda *a, **k: None)
    assert tab.inspector.btn_tags.isVisibleTo(tab.inspector)
    tab.edit_tags_of()                                                               # MED-031
    row = dict(library.db_manager.execute_query("SELECT tags FROM stock_library", fetch="one"))
    assert row["tags"] == "fire,smoke"
    artist = _tab(qtbot, library, roles=ARTIST)
    assert not artist.inspector.btn_tags.isVisibleTo(artist.inspector)


# ------------------------------------------------------------------ ingest

class _Sig:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


class _HeldWorker:
    """Stands in for IngestWorker: runs until let go."""
    made = []

    def __init__(self, root_paths=None, fast_mode=False, username=""):
        self.root_paths = root_paths
        self.running = False
        self.is_paused = False
        _HeldWorker.made.append(self)
        for name in ("progress_signal", "assets_batch_signal",
                     "assets_update_batch_signal", "summary_ready", "finished_signal",
                     "memory_alarm"):
            setattr(self, name, _Sig())

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def stop(self):
        self.running = False

    def deleteLater(self):
        pass

    def wait(self, *a):
        return True


def test_a_drop_locks_the_actions_and_a_second_is_refused(qtbot, library, tmp_path, monkeypatch):
    from slate.gui.tabs.stock_browser.controllers import ingest_controller
    _HeldWorker.made = []
    monkeypatch.setattr(ingest_controller, "IngestWorker", _HeldWorker)
    tab = _tab(qtbot, library)
    notes = []
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", **k: notes.append((msg, level)))
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    assert tab.on_folders_dropped([str(a), str(b), str(tmp_path / "gone")])
    assert _HeldWorker.made[0].root_paths == [str(a), str(b)]                      # MED-008
    assert any("left out" in m for m, _ in notes)
    sb = tab.sidebar
    for widget in (sb.btn_ingest, sb.btn_rescan, sb.btn_refresh, sb.btn_delete_selected,
                   sb.btn_clear, sb.btn_import, sb.btn_export, sb.toggle_fast):
        assert not widget.isEnabled(), widget                                      # MED-022
    assert sb.btn_pause.isEnabled() and sb.btn_stop.isEnabled()
    assert not tab.on_folders_dropped([str(a)])
    assert len(_HeldWorker.made) == 1 and _HeldWorker.made[0].running
    assert tab.busy_reason()


def test_the_ingest_ends_with_a_summary():
    from slate.gui.tabs.stock_browser.controllers.ingest_controller import summary_sentence
    text, level = summary_sentence({"added": 128, "skipped": 40, "failed": 3})
    assert text == "Added 128, 40 already in the library, 3 could not be read."    # MED-021
    assert level == "warning"
    assert summary_sentence({"added": 0, "skipped": 7})[0] == "7 already in the library."
    assert summary_sentence({"stopped": True, "added": 2})[0].startswith("Ingest stopped.")


def test_pause_reads_the_same_way_both_ways(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    tab.sidebar.set_ingest_running(True)
    assert tab.sidebar.btn_pause.text() == "Pause"
    tab.sidebar.set_pause_btn_text("Resume")
    assert tab.sidebar.btn_pause.text() == "Resume"                                 # MED-036
    tab.sidebar.set_pause_btn_text("Pause")
    assert tab.sidebar.btn_pause.text() == "Pause"
    tab.sidebar.set_ingest_progress(-1, "Scanning… 50 files found")
    assert tab.sidebar.progress_bar_ingest.maximum() == 0                          # MED-037
    assert "proxies" in tab.sidebar.toggle_fast.toolTip()                          # MED-038
    assert not tab.sidebar.toggle_fast.isEnabled()


def test_rescan_without_folders_says_why(qtbot, library, monkeypatch):
    tab = _tab(qtbot, library, load=False)
    notes = []
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", **k: notes.append(msg))
    assert not tab.rescan_library()                                                 # MED-040
    assert "No folders are recorded yet" in notes[0]
    assert "Re-read the library from the database" in tab.sidebar.btn_refresh.toolTip()


def test_artists_are_told_who_can_ingest(qtbot, library, monkeypatch):
    tab = _tab(qtbot, library, roles=ARTIST, load=False)
    notes = []
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", **k: notes.append(msg))
    tab.on_folders_dropped(["C:/x"])
    tab.delete_selected_assets()
    assert len(notes) == 2 and all("Only leads, supervisors and admins" in n for n in notes)  # MED-041
    assert "Developer Mode" not in " ".join(notes)


def test_import_refuses_a_file_that_is_not_an_export(qtbot, library, tmp_path, monkeypatch):
    tab = _tab(qtbot, library, load=False)
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a list"}', encoding="utf-8")
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    notes = []
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", **k: notes.append((msg, level)))
    tab.import_library_file()
    assert notes[0][1] == "error" and "not a Slate library export" in notes[0][0]   # MED-027
    assert "attribute" not in notes[0][0]


def test_export_goes_to_documents_with_a_date(qtbot, library, tmp_path, monkeypatch):
    from datetime import date
    from slate.gui.tabs.stock_browser.controllers.library_action_mixin import default_export_path
    assert default_export_path().endswith(
        f"Slate_Stock_Export_{date.today().isoformat()}.json")                     # MED-035
    _seed(library, tmp_path, count=2)
    tab = _tab(qtbot, library)
    out = tmp_path / "export.json"
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    notes = []
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", details="", action=None:
                        notes.append((msg, action)))
    tab.export_library()
    assert notes[0][0].startswith("Exported 2 assets") and notes[0][1][0] == "Open folder"


# ------------------------------------------------------------------ gallery

def test_list_view_is_a_table_with_sortable_columns(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    g = tab.gallery
    g.btn_list.click()
    assert g.view_mode() == "list" and g.stack.currentWidget() is g.table_view     # MED-049, 052
    assert tab.model.columnCount() == 7
    assert not g.zoom_slider.isEnabled()                                            # MED-050
    g._on_header_clicked(0)
    assert g.sort_key() == "name"
    g._on_header_clicked(0)
    assert g.sort_key() == "name_desc"


def test_the_context_menu_has_no_stray_separators(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=2)
    for roles in (ADMIN, ARTIST):
        tab = _tab(qtbot, library, roles=roles)
        menu = tab.gallery.build_context_menu(tab.proxy_model.index(0, 0))
        kinds = [a.isSeparator() for a in menu.actions()]
        assert not kinds[-1] and not any(a and b for a, b in zip(kinds, kinds[1:]))   # MED-055
        texts = [a.text() for a in menu.actions()]
        assert "Show in Explorer" in texts
        assert any(t.startswith("Delete") for t in texts) == (roles == ADMIN)


def test_cards_have_a_tooltip_and_badges():
    from slate.gui.stock_model import badge_labels, tooltip_text
    asset = {"name": "an_extremely_long_name_FINAL.mov", "file_path": "C:/s/x.mov",
             "metadata": {"width": 3840, "height": 2160, "duration_sec": 4, "fps": 24}}
    assert "an_extremely_long_name_FINAL.mov" in tooltip_text(asset)               # MED-044
    badges = badge_labels(asset)
    assert badges["kind"] == "MOV" and badges["resolution"] == "4K" and badges["length"] == "0:04"  # MED-045
    seq = {"is_sequence": True, "frame_count": 24, "file_path": "C:/s/a.1001.exr"}
    assert badge_labels(seq)["kind"] == "SEQ" and badge_labels(seq)["length"] == "24 f"


def test_cards_fill_the_row(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=12)
    tab = _tab(qtbot, library)
    g = tab.gallery
    g._fit_cards()
    cell = g.asset_view.gridSize().width()
    width = g.asset_view.viewport().width()
    # Whole columns fill the row: what is left over is less than the margin kept back.
    keep = g.asset_view.spacing() * 2 + 4          # the first column's inset and a margin
    assert 0 <= width - (width // cell) * cell <= keep + (width // cell)            # MED-046
    # ...and the view really lays out that many in a row: no empty column-wide
    # band at the right (MED2-008).
    for w in (900, 1115, 1240):
        g.asset_view.resize(w, 600)
        qtbot.wait(20)
        g._fit_cards()
        g.asset_view.doItemsLayout()
        cell = g.asset_view.gridSize().width()
        top = g.asset_view.visualRect(tab.proxy_model.index(0, 0)).top()
        in_row = sum(1 for r in range(tab.proxy_model.rowCount())
                     if g.asset_view.visualRect(tab.proxy_model.index(r, 0)).top() == top)
        assert g.asset_view.viewport().width() - in_row * cell < cell, (w, in_row, cell)


def test_media_pills_keep_their_words(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    tab.gallery.resize(500, 600)
    assert tab.gallery.btn_img.text() == "Images" and tab.gallery.btn_vid.text() == "Videos"  # MED-062
    assert tab.gallery.lbl_count.isVisibleTo(tab.gallery)                          # MED-032


def test_the_sidebar_reads_cleanly(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    tab.sidebar.set_collapsed_visual(True)
    assert tab.sidebar.btn_collapse.text() == "" and not tab.sidebar.btn_collapse.icon().isNull()  # MED-065
    labels = [w.text() for w in tab.sidebar.findChildren(QLabel)]
    assert "Categories" in labels and "Smart Categories:" not in labels          # MED-067


def test_side_panels_follow_the_window(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    sizes = tab.proportional_sizes(1280)
    assert sizes[0] >= 200 and sizes[1] >= 0.55 * 1280                              # MED-061, MED2-035
    assert tab.proportional_sizes(1100)[0] == 0
    wide = tab.proportional_sizes(1920)
    assert wide[0] > 0 and wide[1] > wide[2]


def test_the_first_load_starts_when_shown(qtbot, library, monkeypatch):
    tab = _tab(qtbot, library, load=False)
    started = []
    monkeypatch.setattr(tab, "load_library_from_server", lambda: started.append(1))
    tab.show()
    assert started == [1]                                                            # MED-078


# ---------------------------------------------------------------- inspector

def test_the_inspector_shows_the_facts(qtbot, library, tmp_path):
    from slate.gui.tabs.stock_browser.ui.inspector import ZERO_WIDTH_SPACE
    pic = _picture(tmp_path / "Stock" / "an_extremely_long_stock_asset_name_v001_final.jpg",
                   400, 500)
    library.add_assets_batch([{"file_path": str(pic), "category": "References",
                               "metadata": {"width": 400, "height": 500, "is_still": True,
                                            "codec": "jpeg"}}])
    tab = _tab(qtbot, library)
    _select(tab, [0])
    ins = tab.inspector
    assert ZERO_WIDTH_SPACE in ins.lbl_name.text()                                  # MED-057
    assert ins.values["type"].text() == "Still"
    assert ins.values["resolution"].text() == "400 \u00d7 500"                      # MED-058
    assert ins.values["fps"].text() == "\u2014" and ins.values["length"].text() == "\u2014"  # MED-020
    assert ins.values["category"].text() == "References"
    assert ins.values["added_by"].text() != "\u2014"
    fonts = {ins.values[k].font().pointSizeF() for k in ins.values}
    assert len(fonts) == 1                                                           # MED-071


def test_a_missing_file_and_camera_raw_say_so(qtbot, library, tmp_path):
    raw = tmp_path / "A001_C002.r3d"
    raw.write_bytes(b"raw")
    library.add_assets_batch([{"file_path": str(tmp_path / "gone.mov")},
                              {"file_path": str(raw)}])
    tab = _tab(qtbot, library)
    by_name = {a["file_name"]: a for a in tab.model.assets}
    tab.inspector.update_asset(by_name["gone.mov"])
    assert tab.inspector.player.screen.text().startswith("File not found")          # MED-059
    assert tab.inspector.btn_copy_missing.isVisibleTo(tab.inspector)
    tab.inspector.update_asset(by_name["A001_C002.r3d"])
    assert "camera raw" in tab.inspector.player.screen.text()                        # MED-060
    assert tab.inspector.btn_open_external.isVisibleTo(tab.inspector)


def test_double_click_on_raw_launches_nothing(qtbot, library, tmp_path, monkeypatch):
    raw = tmp_path / "B.ari"
    raw.write_bytes(b"raw")
    library.add_assets_batch([{"file_path": str(raw)}])
    tab = _tab(qtbot, library)
    from PySide6.QtGui import QDesktopServices
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url))
    tab.on_double_click(tab.proxy_model.index(0, 0))
    assert opened == []


def test_empty_values_are_one_dash(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    tab.inspector.clear()
    assert {v.text() for v in tab.inspector.values.values()} == {"\u2014"}          # MED-072


# --------------------------------------------------------------------- model

def test_reingesting_does_not_duplicate_cards(qtbot):
    from slate.gui.stock_model import StockModel
    model = StockModel()
    try:
        assets = [{"id": f"h{i}", "file_path": f"C:/s/{i}.jpg", "status": "ingesting"}
                  for i in range(5)]
        model.upsert_assets([dict(a) for a in assets])
        model.upsert_assets([dict(a, status="ready") for a in assets])
        assert model.rowCount() == 5                                                # MED-023
    finally:
        model.cleanup()


def test_dropping_a_card_back_on_the_grid_is_ignored(qtbot):
    from PySide6.QtCore import QMimeData, QUrl
    from slate.gui.tabs.stock_browser.widgets import DraggableListView

    class Event:
        def __init__(self, source):
            self._source = source
            self.ignored = False

        def source(self):
            return self._source

        def mimeData(self):
            data = QMimeData()
            data.setUrls([QUrl.fromLocalFile("C:/x.jpg")])
            return data

        def ignore(self):
            self.ignored = True

    view = DraggableListView()
    qtbot.addWidget(view)
    dropped = []
    view.files_dropped.connect(dropped.append)
    event = Event(view)
    view.dropEvent(event)
    assert event.ignored and dropped == []                                          # MED-043


# ------------------------------------------------- verification round (merged build)

def test_folder_categories_keep_their_casing():
    """NEW-media-1: 'LibB' became 'Libb'."""
    from slate.core.domain.metadata_engine import SmartMetadataManager as M
    assert M.classify_category(Path("X/LibB/file.dat")) == "LibB"
    assert M.classify_category(Path("X/LibC_bulk/file.dat")) == "LibC bulk"


def test_a_finished_ingest_keeps_only_its_sentence(qtbot, library):
    """NEW-media-6"""
    tab = _tab(qtbot, library, load=False)
    tab.show()
    sb = tab.sidebar
    sb.set_ingest_running(True)
    assert sb.progress_bar_ingest.isVisible() and sb.btn_pause.isVisible()
    sb.set_ingest_state("Added 3.", True)
    sb.set_ingest_running(False)
    assert sb.lbl_ingest_status.isVisible() and sb.lbl_ingest_status.text() == "Added 3."
    assert not sb.progress_bar_ingest.isVisible() and not sb.btn_pause.isVisible()
    assert not sb.btn_stop.isVisible()


def test_a_held_cache_file_is_retried_and_counted_right(tmp_path, monkeypatch):
    """NEW-media-7"""
    from slate.core.domain.library_manager import LibraryManager
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"x")
    real_unlink = Path.unlink
    calls = []

    def flaky(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(32, "being used by another process")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", flaky)
    assert LibraryManager.remove_cached_files([{"thumb_path": str(thumb)}]) == (1, 0)
    assert not thumb.exists()
    import inspect
    from slate.gui.tabs.stock_browser.controllers import library_action_mixin
    assert "cached files could not" not in inspect.getsource(library_action_mixin).replace(
        "cached file{'s' if failed != 1 else ''} could not", "")


def test_a_thumbnail_gone_from_the_cache_is_asked_for_again(qtbot):
    """NEW-media-8"""
    from slate.gui.stock_model import StockModel
    model = StockModel()
    try:
        asked = []
        model.thumbnail_needed.connect(asked.append)
        model.load_data([{"id": "1", "file_path": "C:/s/a.jpg", "thumb_path": "C:/cache/gone.jpg"}])
        model.on_image_missing("C:/cache/gone.jpg")
        model.on_image_missing("C:/cache/gone.jpg")
        assert [a["file_path"] for a in asked] == ["C:/s/a.jpg"]
    finally:
        model.cleanup()


def test_cards_are_fitted_again_when_the_viewport_settles(qtbot, library, tmp_path):
    """MED-046: a 50-65 px band stayed on the right on first open."""
    _seed(library, tmp_path, count=40)
    tab = _tab(qtbot, library)
    g = tab.gallery
    qtbot.waitUntil(lambda: g.asset_view.viewport().width() == getattr(g, "_fitted_width", -1),
                    timeout=3000)
    tab.resize(1500, 900)
    qtbot.waitUntil(lambda: g.asset_view.viewport().width() == g._fitted_width, timeout=3000)
    cell = g.asset_view.gridSize().width()
    width = g.asset_view.viewport().width()
    assert width - (width // cell) * cell <= g.asset_view.spacing() * 2 + 4 + (width // cell)


def test_the_inspector_never_scrolls_sideways(qtbot, library):
    """NEW-media-2"""
    from PySide6.QtWidgets import QScrollArea
    tab = _tab(qtbot, library, load=False)
    area = tab.inspector.findChild(QScrollArea)
    assert area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_the_thumbnail_loader_lets_go_of_the_file(qtbot, tmp_path):
    """
    NEW-media-7: Clear library could not delete the last thumbnail the gallery
    had shown - the loader thread's QImageReader kept it open while waiting
    for the next request. Load it the way the gallery does, then delete it.
    """
    import os
    from slate.gui.stock_model import ThumbnailLoader
    thumb = _picture(tmp_path / "abc_thumb.jpg", 320, 180)
    loader = ThumbnailLoader()
    loaded = []
    loader.image_loaded.connect(lambda path, image: loaded.append(path))
    loader.start()
    try:
        loader.request_image(str(thumb))
        qtbot.waitUntil(lambda: bool(loaded), timeout=5000)
        qtbot.wait(200)                       # the loader is now idle, waiting
        os.remove(str(thumb))                 # WinError 32 while it was held
        assert not thumb.exists()
    finally:
        loader.stop()


# ------------------------------------------------------------------ round 2

def test_the_inspector_stays_in_step_with_the_list(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3, folder="Stock/Fire", category="Fire")
    _seed(library, tmp_path, count=2, folder="Stock/Smoke", category="Smoke")
    tab = _tab(qtbot, library)
    row = next(r for r in range(tab.proxy_model.rowCount())
               if tab.proxy_model.index(r, 0).data(Qt.ItemDataRole.UserRole)["category"] == "Fire")
    _select(tab, [row])
    shown = tab.inspector.current_asset["file_path"]
    tab.load_library_from_server()                          # Reload keeps it selected
    _wait(qtbot, tab)
    assert tab.inspector.current_asset["file_path"] == shown                       # MED2-001
    assert tab.gallery.selected_assets()[0]["file_path"] == shown
    assert tab.sidebar.btn_delete_selected.isEnabled()
    tab.on_category_changed("Smoke")                        # not in the list any more
    _wait(qtbot, tab)
    assert tab.inspector.current_asset is None


def test_removed_assets_can_be_restored(qtbot, library, tmp_path, monkeypatch):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    monkeypatch.setattr(tab, "_confirm_delete", lambda *a: True)
    monkeypatch.setattr(tab, "_notify", lambda *a, **k: None)
    _select(tab, [0])
    tab.delete_selected_assets()
    tab._refresh_categories()
    assert "Removed" in tab.sidebar.category_texts()                               # MED2-028
    tab.on_category_changed("Removed")
    tab.sidebar.current_category = "Removed"
    _wait(qtbot, tab)
    assert tab.model.rowCount() == 1
    _select(tab, [0])
    assert tab.sidebar.btn_delete_selected.text() == "Restore"
    menu = tab.gallery.build_context_menu(tab.proxy_model.index(0, 0))
    assert any("Restore" in a.text() for a in menu.actions())
    assert not any("Delete" in a.text() for a in menu.actions())
    tab.delete_selected_assets()                            # the same button restores
    _wait(qtbot, tab)
    assert library.get_total_count() == 3 and library.get_removed_count() == 0


def test_library_actions_need_something_to_act_on(qtbot, library, tmp_path):
    tab = _tab(qtbot, library)
    assert not tab.sidebar.btn_clear.isEnabled()                                    # MED2-020
    assert not tab.sidebar.btn_export.isEnabled()
    assert not tab.sidebar.btn_rescan.isEnabled()
    assert "empty" in tab.sidebar.btn_clear.toolTip()
    _seed(library, tmp_path, count=2)
    library.remember_root(str(tmp_path / "Stock"))
    tab.load_library_from_server()
    _wait(qtbot, tab)
    assert tab.sidebar.btn_clear.isEnabled() and tab.sidebar.btn_export.isEnabled()
    assert tab.sidebar.btn_rescan.isEnabled()
    assert artist_sees_no_removed(qtbot, library)


def artist_sees_no_removed(qtbot, library):
    tab = _tab(qtbot, library, roles=ARTIST, user="sam")
    return "Removed" not in tab.sidebar.category_texts()


def test_an_unreadable_file_says_so(qtbot, library, tmp_path):
    broken = tmp_path / "Stock" / "broken_download.mp4"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"0" * 1024)
    library.add_assets_batch([{"file_path": str(broken), "metadata": {}}])
    tab = _tab(qtbot, library)
    _select(tab, [0])
    qtbot.waitUntil(lambda: tab.inspector.values["resolution"].text() != "Analysing…",
                    timeout=20000)
    assert tab.inspector.values["resolution"].text() == "Could not read this file"   # MED2-011
    assert tab.model.assets[0]["status"] == "corrupt"


def test_the_sort_box_follows_the_header(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=3)
    tab = _tab(qtbot, library)
    tab.gallery.set_view_mode("list")
    tab.gallery._on_header_clicked(4)                       # Size, smallest first
    assert tab.gallery.sort_combo.currentData() == "size_asc"                       # MED2-010
    assert tab.gallery.sort_combo.currentText() == "Smallest first"
    tip = tab.model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
    assert "cannot be sorted" in tip


def test_rows_without_a_picture_keep_the_icon_box(qtbot):
    from slate.gui.stock_model import StockModel
    model = StockModel([{"file_path": "C:/nowhere/camera_raw_A001.r3d"}])
    icon = model.data(model.index(0, 0), Qt.ItemDataRole.DecorationRole)
    assert icon is not None and not icon.isNull()                                    # MED2-017
    model.cleanup()


def test_clearing_filters_empties_the_search_box_properly(qtbot, library, tmp_path):
    _seed(library, tmp_path, count=2)
    tab = _tab(qtbot, library)
    tab.gallery.search_bar.setText("clip")
    seen = []
    tab.gallery.search_bar.textChanged.connect(seen.append)
    tab.clear_all_filters()
    assert seen == [""] and not tab.gallery.search_timer.isActive()                  # MED2-018


def test_a_laptop_keeps_the_sidebar(qtbot, library):
    tab = _tab(qtbot, library, load=False)
    assert tab.proportional_sizes(1300)[0] >= 200          # a 1366 window   # MED2-035
    assert tab.proportional_sizes(1184)[0] >= 200          # a 1280 window


def test_a_failed_pick_is_not_called_a_success(qtbot, library, tmp_path, monkeypatch):
    _seed(library, tmp_path, count=2)
    tab = _tab(qtbot, library)
    said = []
    monkeypatch.setattr(tab, "_notify", lambda msg, level="info", **k: said.append((msg, level)))
    monkeypatch.setattr(tab.lib_manager, "set_pick", lambda *a, **k: False)
    tab.toggle_pick(list(tab.model.assets), True)
    assert said == [("2 studio picks could not be saved.", "error")]                 # MED2-030


def test_the_tag_editor_names_its_asset_and_takes_delete(qtbot):
    from slate.gui.widgets.tag_edit_dialog import TagEditDialog
    dialog = TagEditDialog(None, current_tags=["warm", "square"], asset_name="warm_square.jpg")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowTitle() == "Edit tags - warm_square.jpg"                    # MED2-039
    dialog.list_tags.setCurrentRow(0)
    dialog.list_tags.setFocus()
    qtbot.keyClick(dialog.list_tags, Qt.Key.Key_Delete)
    assert dialog.get_tags() == ["square"]
