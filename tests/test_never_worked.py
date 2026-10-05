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
