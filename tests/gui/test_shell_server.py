"""
Slate Server console fixes from the shell audit (SHL-158 .. SHL-171).

The window itself starts a database, so these drive its methods on a stand-in
and build the real views.
"""
import types
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window_module():
    from slate_server.gui import app_window
    return app_window


def test_the_stat_cards_ask_for_real_columns(pg_db):
    module = _window_module()
    pg_db.execute_query(module.STAT_QUERIES["projects"])
    assert pg_db.last_error() in (None, "")
    pg_db.execute_query(module.STAT_QUERIES["assets"])
    assert pg_db.last_error() in (None, "")


def test_operations_is_highlighted_when_open(app, qtbot):
    from PySide6.QtWidgets import QPushButton, QStackedWidget
    module = _window_module()
    fake = types.SimpleNamespace(
        stacked_widget=QStackedWidget(), _refresh_operations=lambda: None,
        btn_nav_dash=QPushButton(), btn_nav_settings=QPushButton(),
        btn_nav_analytics=QPushButton(), btn_nav_operations=QPushButton())
    for _ in range(4):
        fake.stacked_widget.addWidget(QPushButton())
    module.UTServerWindow.switch_view(fake, 3)
    assert "border-left" in fake.btn_nav_operations.styleSheet()
    assert "border-left" not in fake.btn_nav_dash.styleSheet()


def test_analytics_fits_the_default_window(app, qtbot):
    from slate_server.gui.views.analytics_view import AnalyticsView
    view = AnalyticsView()
    qtbot.addWidget(view)
    assert view.minimumSizeHint().width() <= 980
    from PySide6.QtWidgets import QLabel
    for card in view.cards:
        assert not any(ord(ch) > 0x2600 for label in card.findChildren(QLabel) for ch in label.text())


def test_the_log_is_bounded_and_selectable(app, qtbot):
    from slate_server.gui.views.dashboard_view import DashboardView
    view = DashboardView()
    qtbot.addWidget(view)
    for i in range(2000):
        view.append_log(f"line {i}")
    assert view.lbl_logs.blockCount() <= view.LOG_LINES
    assert view.lbl_logs.isReadOnly()
    assert not hasattr(view, "btn_force_kill"), "force kill belongs in the danger zone"


def test_force_kill_is_in_the_danger_zone(app, qtbot):
    from slate_server.gui.views.operations_view import OperationsView
    view = OperationsView()
    qtbot.addWidget(view)
    fired = []
    view.force_kill_requested.connect(lambda: fired.append(1))
    view.btn_force_kill.click()
    assert fired == [1]


def test_the_firewall_is_only_called_allowed_when_it_is(app):
    module = _window_module()
    notes, logs = [], []

    class Result:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    def refused(cmd, **_kw):
        return Result(1, "No rules match the specified criteria.")

    fake = types.SimpleNamespace(
        settings_view=types.SimpleNamespace(
            input_port=types.SimpleNamespace(text=lambda: "5440"),
            input_pooler_port=types.SimpleNamespace(text=lambda: "6432")),
        _log=logs.append, _settings_saved_note=notes.append,
        FIREWALL_RULE=module.UTServerWindow.FIREWALL_RULE,
        firewall_rule_exists=module.UTServerWindow.firewall_rule_exists)
    assert module.UTServerWindow._on_allow_firewall(fake, run=refused) is False
    assert notes[-1] == "Firewall not changed"

    def allowed(cmd, **_kw):
        return Result(0, "Rule Name: %s" % module.UTServerWindow.FIREWALL_RULE)

    assert module.UTServerWindow._on_allow_firewall(fake, run=allowed) is True
    assert notes[-1] == "Firewall allowed"


def test_saving_while_running_keeps_the_engine(app, qtbot, tmp_path, monkeypatch):
    """The engine is not swapped under a running server (SHL-165)."""
    import inspect
    module = _window_module()
    source = inspect.getsource(module.UTServerWindow._on_save_settings)
    assert "server_running()" in source and '"Settings Saved"' not in source


def test_closing_a_running_server_asks(app, monkeypatch):
    module = _window_module()
    calls = []
    fake = types.SimpleNamespace(
        server_running=lambda: True, ask_before_closing=lambda: "cancel",
        _save_window_geometry=lambda: calls.append("geometry"),
        db_engine=types.SimpleNamespace(stop=lambda: calls.append("stop")))

    class Event:
        def __init__(self):
            self.ignored = False

        def ignore(self):
            self.ignored = True

        def accept(self):
            pass

    event = Event()
    module.UTServerWindow.closeEvent(fake, event)
    assert event.ignored and "stop" not in calls


def test_the_server_no_longer_starts_the_web_api(app):
    """It listened on 0.0.0.0, nothing in Slate used it, and it allowed admin takeover."""
    module = _window_module()
    for gone in ("api_port", "dashboard_url", "_on_open_dashboard"):
        assert not hasattr(module.UTServerWindow, gone)
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "ApiServer" not in source
    from slate_server.gui.views.settings_view import SettingsView
    view = SettingsView()
    assert not hasattr(view, "input_api_port")
    assert view.input_port.width() == view.input_pooler_port.width() \
        or view.input_port.maximumWidth() == view.input_pooler_port.maximumWidth()
