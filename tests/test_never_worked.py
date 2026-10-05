"""
Features that looked as if they worked but ended in nothing.

One check per finding of the "never worked" sweep (done_realtools_dead.md).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_module_parses():
    """
    The Slate Server's main.py and the dashboard's Advanced filter dialog did
    not compile (a newline and two quotes lost in an edit), so the server
    could not start from source and the filter dialog could not open. No test
    imported either file.
    """
    broken = []
    for top in ("slate", "slate_server"):
        for path in (ROOT / top).rglob("*.py"):
            try:
                ast.parse(path.read_bytes(), filename=str(path))
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
    for path in (ROOT / "slate_recover.py",):
        ast.parse(path.read_bytes())
    assert not broken, broken


def test_dcc_plugins_find_the_shot_the_launcher_names(monkeypatch, tmp_path):
    """
    The Slate menu inside Nuke/Natron/Blender/Silhouette looked the shot up in
    a "shots" table that no Slate database has, so Load Scan and Save New
    Version always said "Shot context not found".
    """
    import os
    from slate.core import dcc_launcher

    exe = tmp_path / "Nuke.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(dcc_launcher, "resolve_executable", lambda *_: str(exe))
    monkeypatch.setattr(dcc_launcher, "ConfigManager", lambda: None)
    monkeypatch.setattr(dcc_launcher, "get_nuke_mode", lambda *_: "nukex")
    envs = []
    monkeypatch.setattr(dcc_launcher.subprocess, "Popen", lambda cmd, **kw: envs.append(kw["env"]))
    scan = str(tmp_path / "sh010" / "01_Scan")
    assert dcc_launcher.DCCLauncher().launch("nuke", 7, shot_name="sh010", scan_path=scan)

    for rel in ("nuke/menu.py", "natron/menu.py", "silhouette/startup.py",
                "blender/startup/ut_vfx_startup.py"):
        tree = ast.parse((ROOT / "slate/plugins/dcc" / rel).read_bytes())
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_shot_data")
        space = {"os": os}
        exec(compile(ast.Module([func], []), rel, "exec"), space)
        monkeypatch.setattr(os, "environ", envs[0])
        data = space["get_shot_data"]()
        monkeypatch.undo()
        assert data == {"shot_name": "sh010", "scan_path": scan}, rel


def test_settings_project_root_reaches_build_and_ingest(qtbot, monkeypatch, tmp_path):
    """
    Settings' "Project root" (the folder Build & Ingest opens with) was saved
    under a key Build & Ingest reads only until it has been used once; its
    "Excel tracking file" was read by nothing and is gone.
    """
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLineEdit
    from slate.gui.tabs import settings_tab as st
    from slate.gui.tabs.folder_creator_tab import FolderCreatorTab

    class Config:
        settings = {"global_settings": {}, "last_project_dir": "C:/old",
                    "last_project_directory": "C:/old/used_by_build"}
        default_global_settings = {}

        def save_settings(self, settings):
            return True

        def update_global_settings(self, values):
            return True

    monkeypatch.setattr(st.SettingsTab, "_toast", lambda *a, **k: None)
    tab = st.SettingsTab(Config(), roles=["Admin"])
    qtbot.addWidget(tab)
    assert not hasattr(tab, "excel_tracking_input")
    tab.project_root_input.setText(str(tmp_path))
    assert tab.save_all()

    build = SimpleNamespace(project_dir_input=QLineEdit(), scan_source_input=QLineEdit(),
                            project_name_input=QLineEdit(),
                            _settings=lambda: tab.config_manager.settings)
    FolderCreatorTab.restore_last_paths(build)
    assert build.project_dir_input.text() == str(tmp_path)


def test_bid_tracking_export_button_writes_a_file(qtbot, monkeypatch, tmp_path):
    """clicked's checked=False landed in export(path=...), so no Save dialog and no file."""
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog
    from slate.core.domain import bid_export
    from slate.gui.tabs import bid_tracking_view as module

    target = tmp_path / "tracking.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(bid_export, "tracking_rows", lambda bid, tracking: (["Shot"], [["sh010"]]))
    monkeypatch.setattr("slate.gui.components.feedback.toast", lambda *a, **k: None)
    view = module.BidTrackingView()
    qtbot.addWidget(view)
    view.bid = SimpleNamespace(project_code="ABC", revision=1)
    view.tracking = object()
    view.export_button.setEnabled(True)
    qtbot.mouseClick(view.export_button, Qt.MouseButton.LeftButton)
    assert target.exists()


def test_first_run_test_connection_button_uses_the_real_connect(qtbot, monkeypatch):
    """clicked's checked=False became connect=False, so the test called False(...) and always failed."""
    from PySide6.QtCore import Qt
    from slate.gui.dialogs import first_run_dialog as module

    seen = []

    class Worker:
        def __init__(self, values, connect=None):
            seen.append(connect)
            self.done = self

        def connect(self, *_):
            pass

        def isRunning(self):
            return False

        def start(self):
            pass

    monkeypatch.setattr(module, "ConnectionTestWorker", Worker)
    dialog = module.FirstRunSetupDialog()
    qtbot.addWidget(dialog)
    qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    assert seen == [None]


# Slots whose first parameter takes clicked's checked=False harmlessly
# (False means the same as the default).
_FALSE_IS_DEFAULT = {"build_proxies", "_on_refresh_clicked", "_on_allow_firewall", "save",
                     "_on_stat_card_clicked"}


def test_no_click_handler_gets_checked_in_a_real_parameter():
    """
    clicked/triggered pass checked=False into a slot's first parameter when it
    has one. The dashboard's "N filters on - Clear" chip got apply=False and
    never re-filtered the grid; bid Export and first-run Test connection broke
    the same way. Wrap such a slot in a lambda.
    """
    def optional_first(fn):
        """An optional first parameter: what clicked's checked fills."""
        params = fn.args.posonlyargs + fn.args.args
        return (len(params) > 1 and len(fn.args.defaults) >= len(params) - 1
                and not fn.decorator_list)

    trees = [ast.parse(p.read_bytes()) for top in ("slate", "slate_server")
             for p in (ROOT / top).rglob("*.py")]
    classes = [c for t in trees for c in ast.walk(t) if isinstance(c, ast.ClassDef)]
    anywhere = {}
    for cls in classes:
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef):
                anywhere.setdefault(fn.name, []).append(optional_first(fn))
    bad = []
    for cls in classes:
        own = {fn.name: optional_first(fn) for fn in cls.body if isinstance(fn, ast.FunctionDef)}
        for call in (n for n in ast.walk(cls) if isinstance(n, ast.Call)):
            slots = []
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == "connect" and call.args
                    and isinstance(func.value, ast.Attribute) and func.value.attr in ("clicked", "triggered")):
                slots.append(call.args[0])
            if isinstance(func, ast.Name) and func.id == "make_button":
                slots += [k.value for k in call.keywords if k.arg == "on_click"]
            for slot in slots:
                if not isinstance(slot, ast.Attribute) or slot.attr in _FALSE_IS_DEFAULT:
                    continue
                mine = isinstance(slot.value, ast.Name) and slot.value.id == "self" and slot.attr in own
                if own[slot.attr] if mine else any(anywhere.get(slot.attr, ())):
                    bad.append(f"{cls.name} line {call.lineno}: {ast.unparse(slot)}")
    # Module-level builders (dashboard_layout_builder) connect widget.<method>.
    for tree in trees:
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == "connect" and call.args
                    and isinstance(func.value, ast.Attribute) and func.value.attr == "clicked"
                    and isinstance(call.args[0], ast.Attribute)
                    and isinstance(call.args[0].value, ast.Name) and call.args[0].value.id == "widget"
                    and any(anywhere.get(call.args[0].attr, ()))):
                bad.append(f"line {call.lineno}: {ast.unparse(call.args[0])}")
    assert not bad, sorted(set(bad))


def test_batch_edit_applies_what_was_ticked(qtbot, monkeypatch):
    """The dialog deleted itself on close, so reading its answer after exec() raised."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QWidget
    from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as module
    from slate.gui.tabs.vfx_dashboard_pro.ui.batch_edit_dialog import BatchEditDialog

    applied = []
    host = QWidget()
    qtbot.addWidget(host)
    host._can_manage_shots = lambda: True
    host._get_user_list = lambda: []
    host._notify = lambda *a, **k: None
    host.on_batch_update = lambda shots, updates: applied.append(updates)

    def answer(dialog):
        # What a real exec() does: the person ticks and applies, the dialog
        # closes, and leaving its event loop runs any pending deletes.
        dialog.status_cb.setChecked(True)
        dialog._on_apply()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return dialog.result()

    monkeypatch.setattr("slate.core.infra.studio_settings.get_setting", lambda *a, **k: None)
    monkeypatch.setattr(BatchEditDialog, "exec", answer)
    module.DashboardWidget.open_batch_edit_dialog(host, ["sh010", "sh020"])
    assert applied and "status" in applied[0]


def test_shot_panel_verdict_moves_the_shot(qtbot, monkeypatch):
    """The panel re-emitted verdict_given, but the dashboard never listened."""
    from types import SimpleNamespace
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as module

    class FakePanel(QWidget):
        close_requested = Signal()
        show_only_requested = Signal(object)
        apply_requested = Signal(object, dict)
        quick_look_requested = Signal(object)
        rv_review_requested = Signal(object)
        history_requested = Signal(object)
        verdict_given = Signal(object, str)

        def __init__(self, shot, *a, **k):
            super().__init__()
            self.shot = shot

    verdicts = []
    container = QWidget()
    qtbot.addWidget(container)
    container.resize(400, 300)
    host = SimpleNamespace(
        detail_widget=None, user_roles=[], project_manager=None, all_shots=[], user_data={},
        current_project=None, inherit_app_theme=False, detail_layout=QVBoxLayout(container),
        detail_container=container, _get_user_list=lambda: [], _department_scope=lambda: None,
        _artist_identity_candidates=lambda: [], _allowed_statuses=lambda: None,
        _load_detail_thumbnail=lambda shot: None, close_detail_dock=lambda: None,
        show_only_shots=lambda s: None, on_detail_apply=lambda *a: None,
        open_quick_look=lambda s: None, review_in_rv=lambda s: None,
        show_history_dialog=lambda s: None,
        on_version_verdict=lambda version, status: verdicts.append(status))
    monkeypatch.setattr(module, "ShotDetailWidget", FakePanel)
    module.DashboardWidget.open_detail_dock(host, SimpleNamespace(shot_name="sh010"))
    host.detail_widget.verdict_given.emit(object(), "Approved")
    assert verdicts == ["Approved"]


def test_timeline_viewer_opens_your_dashboard_project(monkeypatch, tmp_path):
    """It asked for a default_project that was always None, so the first project won."""
    from types import SimpleNamespace
    from PySide6.QtCore import QSettings
    from slate.gui.tabs import vfx_review_dual_mode_tab as tab_module
    from slate.gui.tabs.vfx_dashboard_pro.core import project_manager, sqlite_handler
    from slate.gui.tabs.vfx_dashboard_pro.ui.components import column_layout_manager as clm

    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store.setValue("dashboard/last_project/ana", "BBB")
    monkeypatch.setattr(clm, "settings_factory", lambda: store)
    projects = [SimpleNamespace(code="AAA", folder_base=""), SimpleNamespace(code="BBB", folder_base="")]

    class Manager:
        def get_all_projects(self):
            return projects

        def get_project(self, code):
            return next((p for p in projects if p.code == code), None)

    class Handler:
        def __init__(self, code):
            self.code = code

        def read_shots(self):
            return [self.code]

    monkeypatch.setattr(project_manager, "ProjectManager", Manager)
    monkeypatch.setattr(sqlite_handler, "SQLiteHandler", Handler)
    loaded = []
    tab = SimpleNamespace(user_data={"username": "ana"},
                          lineup_editor=SimpleNamespace(_set_status=lambda m: None),
                          set_shots=lambda shots, **k: loaded.append(k["project_name"]))
    assert tab_module.VFXReviewDualModeTab._refresh_from_database(tab)
    assert loaded == ["BBB"]


def test_refresh_shortcut_refreshes_the_timeline_viewer(monkeypatch):
    """F5 looked for refresh/reload/load_data; the Timeline Viewer had none and said it kept itself up to date."""
    from types import SimpleNamespace
    from slate.gui.main_window import VFXFolderCreatorApp
    from slate.gui.tabs.vfx_review_dual_mode_tab import VFXReviewDualModeTab

    calls = []
    monkeypatch.setattr(VFXReviewDualModeTab, "_on_refresh_clicked", lambda self, quiet=False: calls.append(1))
    tab = VFXReviewDualModeTab.__new__(VFXReviewDualModeTab)
    window = SimpleNamespace(
        tab_coordinator=SimpleNamespace(get_current_tab=lambda: tab, get_current_tab_name=lambda: "Timeline"),
        show_status=lambda *a: calls.append(a[0]))
    VFXFolderCreatorApp.refresh_current_tab(window)
    assert calls == [1, "Refreshed"]


def test_column_filter_row_click_ticks_the_value(qtbot):
    """'A click anywhere on the row ticks it' was a bare pass; only the box worked."""
    from PySide6.QtCore import QPoint, Qt
    from slate.gui.tabs.vfx_dashboard_pro.ui.header_filter_view import _FilterPanel

    panel = _FilterPanel(["Comp", "Roto"], selected=None)
    qtbot.addWidget(panel)
    panel.show()
    item = panel.list.item(0)
    rect = panel.list.visualItemRect(item)
    qtbot.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rect.left() + 120, rect.center().y()))
    assert item.checkState() == Qt.CheckState.Unchecked
    qtbot.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rect.left() + 10, rect.center().y()))          # the box itself
    assert item.checkState() == Qt.CheckState.Checked


def test_saving_an_archived_project_keeps_it_archived(pg_db):
    """The PostgreSQL upsert set active=1 on every update, so a save un-archived it."""
    pg_db.save_tracking_project("ARC", "Archived", "{}")
    pg_db.execute_update("UPDATE tracking_projects SET active = 0 WHERE code = %s", ("ARC",))
    pg_db.save_tracking_project("ARC", "Archived", '{"x": 1}')
    row = pg_db.execute_query("SELECT active FROM tracking_projects WHERE code = %s", ("ARC",), fetch="one")
    assert row["active"] == 0


def test_dashboard_retry_reconnects(monkeypatch):
    """The offline banner's Retry asked for a reconnect() nothing has, so it never retried."""
    from slate.core.domain import access
    from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as module

    calls = []

    class FakeManager:
        def reload_from_config(self):
            calls.append("reload")

    class FakeDashboard:
        def refresh_connection_state(self):
            return False

        def _notify(self, text, level, **_):
            calls.append(level)

        def refresh_data(self):
            calls.append("refresh")

    monkeypatch.setattr(module, "database_manager", FakeManager())
    monkeypatch.setattr(access, "is_offline_fallback", lambda: True)
    module.DashboardWidget.retry_connection(FakeDashboard())
    assert calls == ["reload", "success", "refresh"]
