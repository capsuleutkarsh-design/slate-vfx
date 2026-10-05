"""The Recovery page, the standalone Recover Slate window and the Recovery Key dialog."""

import types
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QPushButton, QStackedWidget  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def layout(tmp_path, monkeypatch):
    from slate_server.core.recovery import fs
    from slate_server.core.recovery.layout import ServerLayout
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    return ServerLayout(data_dir=tmp_path / "LocalDatabase")


def test_the_page_runs_the_health_check_off_the_screen_thread(app, qtbot, layout):
    from slate_server.gui.views.recovery_view import RecoveryView
    view = RecoveryView(lambda: layout)
    qtbot.addWidget(view)
    view.run_health()
    qtbot.waitUntil(lambda: "This PC" in view.health_text.toPlainText(), timeout=30000)
    assert "FAIL" in view.health_text.toPlainText()


def test_unlocking_on_another_pc_is_refused_on_the_page(app, qtbot, layout):
    from slate_server.gui.views.recovery_view import RecoveryView
    view = RecoveryView(lambda: layout)
    qtbot.addWidget(view)
    view.key_field.setText("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
    view.unlock()
    qtbot.waitUntil(lambda: "Not done" in view.output.toPlainText(), timeout=30000)
    assert view.session is not None and not view.session.unlocked


def test_switches_can_be_set_to_log_only_or_on_one_at_a_time(app, qtbot, layout, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from slate.core.security import switches
    from slate_server.gui.views.recovery_view import RecoveryView
    view = RecoveryView(lambda: layout)
    qtbot.addWidget(view)
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: told.append(a[2]) or QMessageBox.StandardButton.Yes)

    # "Every security switch" is for turning off only.
    view._switch_on("on")
    assert told and "one at a time" in told[-1]

    # Each switch shows its mode and its description from the catalogue.
    view._modes = {"strict_pg_hba": "log_only"}
    view.switch_pick.setCurrentIndex(view.switch_pick.findData("strict_pg_hba"))
    about = view.switch_about.text()
    assert "now: log_only" in about and switches.known()["strict_pg_hba"] in about

    calls = []

    class Session:
        unlocked = True
        say = None

        def turn_on(self, name, mode):
            calls.append((name, mode))
            return ["Applied."]

    session = Session()
    view._session = lambda: session
    monkeypatch.setattr(view, "refresh_switches", lambda: None)
    view._switch_on("log_only")
    qtbot.waitUntil(lambda: calls == [("strict_pg_hba", "log_only")], timeout=10000)

    # signed_* switches: Log only first is recommended before Turn on.
    signed = [n for n in switches.known() if n.startswith("signed_")]
    assert signed
    qtbot.waitUntil(lambda: view.btn_switch_on.isEnabled(), timeout=10000)
    view.switch_pick.setCurrentIndex(view.switch_pick.findData(signed[0]))
    assert "Log only first" in view.switch_about.text()
    view._switch_on("on")
    assert "Log only first" in told[-1]


def test_a_refused_turn_on_says_why_in_plain_words(app, qtbot, layout, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from slate_server.core.recovery import hardening
    from slate_server.core.recovery.session import RecoverySession
    from slate_server.gui.views.recovery_view import RecoveryView
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(RecoverySession, "_need_unlocked", lambda self: None)
    monkeypatch.setattr(RecoverySession, "unlocked", property(lambda self: True))
    # The refusal itself is hardening's (tests/test_security_*); this is what the
    # page shows. Never reaches a real database (the studio's is on 5440).
    reason = "Not turned on: this build has no release key, so every update would be refused."
    monkeypatch.setattr(hardening, "turn_on",
                        lambda layout_, name, mode="on", by="": hardening.StepResult(name, False, reason))
    view = RecoveryView(lambda: layout)
    qtbot.addWidget(view)
    view.switch_pick.setCurrentIndex(view.switch_pick.findData("signed_updates"))
    view._switch_on("on")
    qtbot.waitUntil(lambda: "Not done" in view.output.toPlainText(), timeout=10000)
    assert "no release key" in view.output.toPlainText()


def test_the_key_dialog_cannot_be_closed_until_the_key_is_stored(app, qtbot):
    from slate_server.gui.views.recovery_view import RecoveryKeyDialog
    dialog = RecoveryKeyDialog("ABCDE-FGHJK-MNPQR-STVWX-YZ012")
    qtbot.addWidget(dialog)
    assert dialog.shown.text() == "ABCDE-FGHJK-MNPQR-STVWX-YZ012"
    assert not dialog.close_button.isEnabled()
    dialog.show()
    dialog.reject()
    assert dialog.isVisible(), "escape does not throw the key away"
    dialog.stored.setChecked(True)
    assert dialog.close_button.isEnabled()
    dialog.reject()
    assert not dialog.isVisible()


def test_the_standalone_window_keeps_the_credit_line(app, qtbot, tmp_path):
    from slate import licence
    from slate_server.gui.recovery_window import build_window
    window = build_window(data_dir=str(tmp_path / "LocalDatabase"))
    qtbot.addWidget(window)
    assert licence.window_problems(window) == []


def test_recovery_is_highlighted_when_open(app, qtbot):
    from slate_server.gui import app_window as module
    fake = types.SimpleNamespace(
        stacked_widget=QStackedWidget(), _refresh_operations=lambda: None,
        btn_nav_dash=QPushButton(), btn_nav_settings=QPushButton(),
        btn_nav_analytics=QPushButton(), btn_nav_operations=QPushButton(),
        btn_nav_recovery=QPushButton())
    for _ in range(5):
        fake.stacked_widget.addWidget(QPushButton())
    module.UTServerWindow.switch_view(fake, 4)
    assert "border-left" in fake.btn_nav_recovery.styleSheet()
    assert "border-left" not in fake.btn_nav_operations.styleSheet()


def test_the_server_window_makes_the_key_once_there_is_a_database(app, tmp_path, monkeypatch):
    from slate_server.core.recovery import fs, key, snapshots
    from slate_server.gui import app_window as module
    monkeypatch.setattr(fs, "RESTRICT_PERMISSIONS", False)
    monkeypatch.setattr(snapshots, "before_security_change", lambda *a, **k: None)
    data = tmp_path / "LocalDatabase"
    fake = types.SimpleNamespace(_db_path=str(data), _db_port=5999, _db_pooler_port=6999,
                                 config_path=str(tmp_path / "slate_server_config.json"),
                                 _log=lambda m: None)
    fake._recovery_layout = lambda: module.UTServerWindow._recovery_layout(fake)
    assert module.UTServerWindow._prepare_recovery(fake, show=False) is None, \
        "no database here yet, so no key yet"
    data.mkdir()
    (data / "PG_VERSION").write_text("17", encoding="utf-8")
    made = module.UTServerWindow._prepare_recovery(fake, show=False)
    assert made and key.has_key(fake._recovery_layout())
    assert module.UTServerWindow._prepare_recovery(fake, show=False) is None, "only once"


def test_the_installed_server_offers_the_recovery_tool():
    main = (ROOT / "slate_server" / "main.py").read_text(encoding="utf-8")
    assert '"--recover" in sys.argv' in main
    iss = (ROOT / "deployment" / "setup_slate_server.iss").read_text(encoding="utf-8")
    assert "Recover Slate" in iss and '"--recover"' in iss
    assert (ROOT / "Recover Slate.bat").exists() and (ROOT / "slate_recover.py").exists()
