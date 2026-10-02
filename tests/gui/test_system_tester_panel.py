"""Tester Panel (SYS-075..104, SYS-084 gating)."""

import logging
import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QScrollArea

from slate.gui import tester_panel as tp


class FakeUsers:
    roles_config = {
        "Artist": ["Dashboard", "Stock Browser", "can:artist_own_status"],
        "Developer": ["ALL"],
    }


@pytest.fixture
def panel_for(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(tp.TesterPanel, "_offer_open_folder",
                        lambda self, title, text, folder: shown.append(text))

    def build(roles=()):
        panel = tp.TesterPanel(user_manager=FakeUsers(), roles=list(roles))
        panel.shown = shown
        qtbot.addWidget(panel)
        return panel
    return build


# ------------------------------------------------------------ test folder
def test_test_folder_rules(tmp_path):
    assert tp.validate_test_folder("")[0] is None
    assert tp.validate_test_folder(".")[0] is None
    assert tp.validate_test_folder("relative/folder")[0] is None
    assert tp.validate_test_folder("C:\\")[0] is None
    install = Path(tp.__file__).resolve().parents[2]
    assert tp.validate_test_folder(str(install / "x"))[0] is None
    assert tp.validate_test_folder(str(tmp_path / "data"))[0] == tmp_path / "data"


def test_cancelled_browse_keeps_the_folder(panel_for, monkeypatch):
    panel = panel_for()
    before = panel.test_root.text()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    panel.browse_test_folder()
    assert panel.test_root.text() == before


def test_wipe_needs_rights_and_the_marker(panel_for, tmp_path, monkeypatch, qtbot):
    removed = []
    monkeypatch.setattr(tp.shutil, "rmtree", lambda *a, **k: removed.append(a))
    artist = panel_for(["Artist"])
    assert not artist.btn_wipe.isEnabled() and "developers" in artist.btn_wipe.toolTip()
    assert artist.wipe_folder() is None

    dev = panel_for(["Developer"])
    plain = tmp_path / "project"
    plain.mkdir()
    (plain / "shot.exr").write_bytes(b"x")
    dev.test_root.setText(str(plain))
    assert dev.wipe_folder() is None and removed == []

    dev.test_root.setText("")
    assert dev.wipe_folder() is None and removed == []
    monkeypatch.undo()


def test_wipe_deletes_a_marked_folder(panel_for, tmp_path, monkeypatch, qtbot):
    dev = panel_for(["Developer"])
    folder = tmp_path / "TesterData"
    tp.mark_folder(folder)
    (folder / "a.jpg").write_bytes(b"x")
    dev.test_root.setText(str(folder))
    asked = []
    monkeypatch.setattr(tp.TesterPanel, "_confirm_wipe", lambda self, f, n: asked.append(n) or True)
    worker = dev.wipe_folder()
    assert asked == [1]
    qtbot.waitUntil(lambda: dev.folder_job is None, timeout=5000)
    assert not folder.exists()


def test_workflow_never_deletes_an_existing_test_folder(tmp_path):
    (tmp_path / "TEST").mkdir()
    (tmp_path / "TEST" / "keep.txt").write_text("x")
    worker = tp.WorkflowWorker(tmp_path, count=1, size_strategy="Empty", file_types=[".exr"])
    assert worker.base.name.startswith("TEST_")
    messages = []
    worker.finished_signal.connect(messages.append)
    worker.run()
    assert (tmp_path / "TEST" / "keep.txt").exists()
    assert messages[0].startswith("Test environment ready")
    assert (worker.base / "For_move" / "EXCEL_TEMPLATE.xlsx").exists()


def test_long_paths_are_refused_in_plain_words(tmp_path):
    deep = Path("C:\\" + "x" * 200)
    assert tp.max_nesting(deep) < 5
    assert tp.workflow_longest_path(deep / "TEST", 5, 5, True, 200) > tp.WINDOWS_PATH_LIMIT
    assert "longer than Windows allows" in tp.PATH_TOO_LONG


def test_set_file_dates_only_in_marked_folders(panel_for, tmp_path, qtbot):
    dev = panel_for(["Developer"])
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("x")
    before = (plain / "a.txt").stat().st_mtime
    dev.test_root.setText(str(plain))
    assert dev.run_time_travel() is None
    assert (plain / "a.txt").stat().st_mtime == before

    marked = tmp_path / "marked"
    tp.mark_folder(marked)
    (marked / "a.txt").write_text("x")
    dev.test_root.setText(str(marked))
    dev.time_date.setDate(QDate(2025, 9, 30))
    assert dev.time_date.displayFormat() == "yyyy-MM-dd"
    dev.run_time_travel()
    qtbot.waitUntil(lambda: dev.folder_job is None, timeout=5000)
    assert time.localtime((marked / "a.txt").stat().st_mtime).tm_year == 2025


def test_generator_rules(panel_for, tmp_path, monkeypatch, qtbot):
    artist = panel_for(["Artist"])
    for box in (artist.chk_jpg, artist.chk_mov, artist.chk_exr, artist.chk_txt):
        box.setChecked(False)
    assert not artist.btn_gen.isEnabled()
    artist.chk_jpg.setChecked(True)
    assert artist.btn_gen.isEnabled()

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    artist.test_root.setText(str(tmp_path / "gen"))
    artist.spin_count.setValue(10000)
    artist.combo_size.setCurrentText("50MB")
    assert "500" in artist.lbl_gen_total.text() or "488" in artist.lbl_gen_total.text()
    assert artist.start_generation() is None and "developers" in warned[-1]

    class Usage:
        total, free = 200 * 1024 ** 3, 100 * 1024 ** 3
    monkeypatch.setattr(tp.shutil, "disk_usage", lambda p: Usage())
    ok, ask, message = tp.free_space_check(tmp_path, 10000 * 50 * 1024 * 1024)
    assert not ok and "10%" in message
    assert tp.free_space_check(tmp_path, 25 * 1024) == (True, False, "")

    artist.spin_count.setValue(3)
    artist.combo_size.setCurrentText("1KB")
    worker = artist.start_generation()
    assert artist.btn_gen.text() == "Stop"
    qtbot.waitUntil(lambda: artist.generation_worker is None, timeout=5000)
    assert len(list((tmp_path / "gen").glob("*.jpg"))) == 3 and tp.is_marked(tmp_path / "gen")
    assert "Created 3 files" in artist.shown[-1]


def test_stop_ends_a_generation_early(tmp_path, qtbot):
    worker = tp.FileGeneratorWorker(tmp_path / "many", 10000, "Empty", [".jpg"])
    worker.start()
    worker.stop()
    worker.wait(10000)
    assert worker.created < 10000


def test_database_health_on_postgres(pg_db):
    results = []
    for job in ("vacuum", "integrity"):
        worker = tp.DatabaseHealthWorker(job, db=pg_db)
        worker.done.connect(lambda ok, msg: results.append((ok, msg)))
        worker.run()
    assert results[0][0] and "VACUUM" in results[0][1]
    assert results[1][0], results[1][1]


def test_vacuum_is_for_developers(panel_for):
    artist = panel_for(["Artist"])
    assert not artist.btn_vac.isEnabled() and artist.run_db_vacuum() is None


def test_ghosts_compare_normalised_paths(tmp_path):
    (tmp_path / "Show").mkdir()
    (tmp_path / "Show" / "a.EXR").write_bytes(b"x")
    (tmp_path / "Show" / "b.exr").write_bytes(b"x")
    db_paths = [str(tmp_path / "show" / "A.exr").replace("\\", "/")]
    ghosts = tp.find_ghosts(tmp_path / "Show", db_paths)
    assert ghosts == ["b.exr"]


def test_analysis_survives_a_database_outage(monkeypatch, tmp_path):
    class Down:
        def execute_query(self, *a, **k):
            raise tp.DatabaseUnavailableError("down")
    # The module's name, not the shared proxy: patching an attribute on the
    # proxy leaves the old backend's bound method behind after the test.
    monkeypatch.setattr(tp, "database_manager", Down())
    worker = tp.ValidationWorker(tmp_path, tmp_path, mode="smart")
    messages = []
    worker.finished_signal.connect(messages.append)
    worker.run()
    assert messages and "not reachable" in messages[0]


def test_live_log_follows_visibility(panel_for):
    panel = panel_for()
    root = logging.getLogger()

    def handlers():
        return sum(isinstance(h, tp.QTextEditHandler) for h in root.handlers)
    panel.show()
    assert handlers() == 0                       # Data generator is the current tab
    panel.tabs.setCurrentIndex([panel.tabs.tabText(i) for i in range(panel.tabs.count())].index("Diagnostics"))
    QApplication.processEvents()
    assert handlers() == 1
    panel.hide()
    QApplication.processEvents()
    assert handlers() == 0
    panel.show()
    QApplication.processEvents()
    assert handlers() == 1
    panel.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    assert handlers() == 0


def test_permission_matrix_is_readable(panel_for):
    panel = panel_for()
    table = panel.perm_table
    assert table.rowCount() == 2 and table.minimumHeight() >= 200
    texts = [table.item(r, c).text() for r in range(table.rowCount()) for c in range(table.columnCount())
             if table.item(r, c)]
    assert not any(t.startswith("[") or "can:" in t for t in texts)
    rows = tp.TesterPanel.permission_rows(FakeUsers.roles_config)
    artist = dict((r[0], r) for r in rows)["Artist"]
    assert artist[1]["Stock Viewer"] and not artist[1]["Admin Panel"]
    assert artist[2] == "Set own shot status"


def test_config_sandbox_is_in_memory_and_resets(panel_for, tmp_path):
    from slate.core.infra.global_config import GlobalConfig
    panel = panel_for()
    saved = GlobalConfig.get("SERVER_ROOT")
    try:
        panel.conf_path.setText(str(tmp_path / "server"))
        panel.conf_cache.setText(str(tmp_path / "cache"))
        panel.apply_config_sandbox()
        assert GlobalConfig.get("SERVER_ROOT") == str(tmp_path / "server")
        assert GlobalConfig.local_cache_dir() == tmp_path / "cache"
        assert not panel.sandbox_banner.isHidden()
        panel.reset_config_sandbox()
        assert GlobalConfig.get("SERVER_ROOT") == saved and panel.sandbox_banner.isHidden()
    finally:
        GlobalConfig.clear_runtime_overrides()


def test_layout_and_wording(panel_for):
    artist = panel_for(["Artist"])
    for i in range(artist.tabs.count()):
        assert isinstance(artist.tabs.widget(i), QScrollArea)
    names = [artist.tabs.tabText(i) for i in range(artist.tabs.count())]
    assert "Deep folders" in names and "Structure (Chaos)" not in names
    assert artist.spin_count.width() <= 140
    labels = [w.text() for w in artist.findChildren(QLabel)]
    assert "TESTER PANEL" not in labels
    assert artist.crash_group.isHidden()
    dev = panel_for(["Developer"])
    assert not dev.crash_group.isHidden() and dev.btn_crash.text() == "Simulate crash"
    source = Path(tp.__file__).read_text(encoding="utf-8")
    for phrase in ("TEMPLETE", "where moved", "**ANALYSIS", "Warp", "⏳", "Ghost Asset Hunter"):
        assert phrase not in source


def test_results_pane(panel_for):
    panel = panel_for()
    assert panel.splitter.widget(0) is panel.tabs
    panel.log("<b>hello</b>")
    assert panel.log_area.toPlainText() == "hello"
    panel.clear_log()
    assert panel.log_area.toPlainText() == ""


def test_regression_ingests_and_cleans_up(pg_db, panel_for, tmp_path, monkeypatch, qtbot):
    titles = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: titles.append(a[1]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: titles.append(a[1] + ": " + a[2]))
    panel = panel_for(["Developer"])
    panel.test_root.setText(str(tmp_path / "TesterData"))
    panel.run_regression()
    qtbot.waitUntil(lambda: "passed" in panel._regression, timeout=120000)
    assert panel._regression["passed"], titles
    assert titles[-1] == "Regression passed"
    assert panel.count_stock_rows(panel._regression["target"]) == 0


def test_free_space_rule_without_stubs(tmp_path):
    """The real disk: nothing is refused for writing nothing, and a run that cannot fit is."""
    import shutil as _sh
    usage = _sh.disk_usage(tmp_path)
    assert tp.free_space_check(tmp_path, 0) == (True, False, "")
    assert tp.free_space_check(tmp_path, 1024)[0] is (usage.free - 1024 >= usage.total * 0.10
                                                      or usage.free < usage.total * 0.10)
    ok, _ask, message = tp.free_space_check(tmp_path, usage.free + 1)
    assert not ok and message


def test_free_space_rule_on_a_nearly_full_disk(monkeypatch, tmp_path):
    class Usage:
        total, free = 100 * 1024 ** 3, 5 * 1024 ** 3          # already under 10 %
    monkeypatch.setattr(tp.shutil, "disk_usage", lambda p: Usage())
    assert tp.free_space_check(tmp_path, 0)[0]
    assert tp.free_space_check(tmp_path, 1024 ** 2)[0]          # small run: does not cross anything
    assert not tp.free_space_check(tmp_path, 6 * 1024 ** 3)[0]  # does not fit
