"""
Small shell pieces: the update dialog, start-up and launchers.
"""
import logging
import os
import sys
import threading

import pytest
from PySide6.QtWidgets import QLabel

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def gatekeeper(monkeypatch):
    """slate.gatekeeper_main, with the process-wide hooks it installs put back."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    hooks = (sys.excepthook, threading.excepthook)
    import slate.gatekeeper_main as module
    yield module
    sys.excepthook, threading.excepthook = hooks
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


# ------------------------------------------------------------------ update

def test_release_notes_are_text(qtbot):
    from slate.gui.dialogs.update_available_dialog import UpdateAvailableDialog
    dialog = UpdateAvailableDialog({"version": "2.1.0", "notes": "<b>x</b><img src=x>"})
    qtbot.addWidget(dialog)
    assert "<b>x</b>" in dialog.notes_area.toPlainText()
    assert "<img" not in dialog.notes_area.toHtml().replace("&lt;img", "")
    assert dialog.btn_update.text() == "Download and install"


def test_remind_me_later_waits_a_day(monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    from slate.gui.dialogs import update_available_dialog as module
    store = {}
    monkeypatch.setattr(GlobalConfig, "get", classmethod(lambda cls, key, default=None: store.get(key, default)))
    monkeypatch.setattr(GlobalConfig, "set", classmethod(lambda cls, key, value: store.__setitem__(key, value)))
    module.snooze("2.1.0", now=1000.0)
    assert module.is_snoozed("2.1.0", now=1000.0 + 3600)
    assert not module.is_snoozed("2.1.0", now=1000.0 + module.SNOOZE_SECONDS + 1)
    assert not module.is_snoozed("2.2.0", now=1001.0)


def test_the_startup_check_honours_the_setting(monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    from slate.gui.main_window import VFXFolderCreatorApp
    monkeypatch.setattr(GlobalConfig, "get", classmethod(
        lambda cls, key, default=None: False if key == "check_updates_on_startup" else default))
    assert VFXFolderCreatorApp.update_check_enabled() is False


def test_an_offered_update_is_a_toast_unless_put_off(monkeypatch):
    from slate.gui import main_window
    from slate.gui.dialogs import update_available_dialog as module
    shown = []

    class Fake:
        def show_feedback(self, message, level="info", duration=None, details="", action=None):
            shown.append((message, action[0] if action else None))

        show_update_dialog = lambda self: None

    monkeypatch.setattr(module, "is_snoozed", lambda version: version == "9.9")
    fake = Fake()
    assert main_window.VFXFolderCreatorApp._on_update_available(fake, {"version": "2.1"})
    assert shown == [("Slate 2.1 is available.", "See what's new")]
    assert not main_window.VFXFolderCreatorApp._on_update_available(fake, {"version": "9.9"})


# ------------------------------------------------------------------ start-up

def test_admin_message_window(qtbot, gatekeeper):
    window = gatekeeper.BroadcastWindow("Fire drill at 3", sender="Priya")
    qtbot.addWidget(window)
    texts = [label.text() for label in window.findChildren(QLabel)]
    assert "Message from Priya" in texts and not any("[WARN]" in t or "Running command" in t for t in texts)
    assert window.ok_button.text() == "OK"
    for label in window.findChildren(QLabel):
        assert "border: 2px" not in label.styleSheet()


def test_first_run_test_connection(qtbot, gatekeeper):
    def refuse(**kwargs):
        raise RuntimeError("could not connect to server: Connection refused")

    ok, message = gatekeeper.test_database_connection({"db_host": "nowhere", "db_port": 1}, connect=refuse)
    assert not ok and message.startswith("Not connected") and "refused" not in message.lower() or "reached" in message

    class Conn:
        def close(self):
            pass

    ok, message = gatekeeper.test_database_connection({"db_host": "x", "db_port": 5440},
                                                       connect=lambda **k: Conn())
    assert ok
    dialog = gatekeeper.FirstRunSetupDialog()
    qtbot.addWidget(dialog)
    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Server address" in labels and "DB Host" not in labels and "Server root" in labels
    worker = dialog.test_connection(connect=refuse)
    qtbot.waitUntil(lambda: dialog.test_result.text().startswith("Not connected"), timeout=5000)
    worker.wait(1000)


def test_first_run_find_server_is_a_worker_and_a_missing_folder_is_questioned(qtbot, monkeypatch, tmp_path):
    import slate.core.infra.network_discovery as nd
    from slate.gui.dialogs import first_run_dialog as module
    monkeypatch.setattr(nd, "discover_server_details",
                        lambda timeout=2.0: {"host": "10.0.0.5", "db_port": 5440})
    dialog = module.FirstRunSetupDialog()
    qtbot.addWidget(dialog)
    intro = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Paths & Connections" not in intro and "Server, database and branding" in intro
    assert "credential setup" not in dialog.db_password_input.placeholderText()
    dialog._find_server()
    assert dialog.find_button.text() == "Looking…"        # busy, and the window still paints
    qtbot.waitUntil(lambda: dialog.db_host_input.text() == "10.0.0.5", timeout=3000)
    assert dialog.find_button.isEnabled()

    asked = []
    monkeypatch.setattr(module.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or module.QMessageBox.StandardButton.Cancel))
    dialog.server_root_input.setText(str(tmp_path / "no_such_share"))
    dialog.db_name_input.setText("ut_vfx")
    dialog.db_user_input.setText("postgres")
    dialog._validate_and_accept()
    assert asked and "cannot be reached" in asked[0] and dialog.result() == 0
    dialog.server_root_input.setText(str(tmp_path))
    dialog._validate_and_accept()
    assert dialog.result() == 1


def test_the_first_run_window_has_no_import_side_effects():
    import subprocess
    code = ("import logging, slate.gui.dialogs.first_run_dialog; "
            "import sys; print('gatekeeper' in ' '.join(sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert out.stdout.strip() == "False", out.stderr[-500:]


def test_a_failed_start_exits_non_zero(gatekeeper, monkeypatch):
    codes = []
    monkeypatch.setattr(os, "_exit", lambda code: codes.append(code))
    entry = gatekeeper.ApplicationEntry.__new__(gatekeeper.ApplicationEntry)
    entry.loading_dialog = None
    monkeypatch.setattr(gatekeeper.ApplicationEntry, "_cleanup_background_services", lambda self: None)
    monkeypatch.setattr(gatekeeper.QApplication, "instance", staticmethod(lambda: None))
    entry.cleanup_and_exit(1)
    entry.cleanup_and_exit()
    assert codes == [1, 0]


def test_already_open_names_the_product():
    from slate.utils.single_instance import already_open_message, lock_name_for
    assert already_open_message(lock_name_for("vfx")) == "Slate VFX is already open."
    assert already_open_message(lock_name_for("ops")) == "Slate Operations is already open."
    assert "Slate_Process" not in already_open_message("Slate_Process")


def test_launchers_start_in_their_own_folder():
    for name in ("launch_vfx.bat", "launch_ops.bat", "launch_server.bat", "launch_console.bat"):
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert 'cd /d "%~dp0"' in text, name
    console = open(os.path.join(ROOT, "launch_console.bat"), encoding="utf-8").read()
    assert "poetry" not in console.lower() and "runtime" in console


def test_exr_and_oiio_are_settings(monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    for key in ("SLATE_ENABLE_EXR_LOADING", "SLATE_ENABLE_OIIO"):
        monkeypatch.delenv(key, raising=False)
    assert GlobalConfig.DEFAULTS["enable_exr_loading"] is True
    monkeypatch.setattr(GlobalConfig, "get", classmethod(lambda cls, key, default=None: default))
    assert GlobalConfig.exr_loading_enabled() and GlobalConfig.oiio_enabled()
    monkeypatch.setenv("SLATE_ENABLE_OIIO", "0")
    assert not GlobalConfig.oiio_enabled()
    for name in ("launch_vfx.bat", os.path.join("launchers", "launch_app.bat")):
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert 'set "SLATE_ENABLE_EXR_LOADING=1"' not in text


def test_the_developer_console_speaks_plainly():
    folder = os.path.join(ROOT, "tools", "slate_console", "ui")
    text = "".join(open(os.path.join(folder, f), encoding="utf-8").read()
                   for f in os.listdir(folder) if f.endswith(".py"))
    assert "THE FORGE" not in text and "THE LAB" not in text
    assert not any(ch in text for ch in "\U0001F525\U0001F9EA\U0001F680\U0001F9F9")


def test_the_console_stop_button_waits_for_a_run(qtbot):
    sys.path.insert(0, os.path.join(ROOT, "tools", "slate_console"))
    try:
        from ui.test_tab import TestTab
    except Exception:
        pytest.skip("console UI not importable here")
    tab = TestTab()
    qtbot.addWidget(tab)
    assert not tab.btn_stop.isEnabled()
    assert ":disabled" in tab.btn_stop.styleSheet()
