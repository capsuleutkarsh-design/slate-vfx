"""
The header and sidebar of the main window (shell area).
"""
import pytest
from PySide6.QtWidgets import QMenu, QWidget

from slate.gui.components.header_builder import HeaderBuilder, initials


class Parent(QWidget):
    """Just enough of a main window for the header."""

    def __init__(self, mode="vfx", job_title=""):
        super().__init__()
        self.app_mode = mode
        self.config_manager = type("C", (), {"settings": {"global_settings": {}}})()
        self.opened = []
        self.job = job_title

    def show_quick_search(self):
        self.opened.append("palette")

    def show_shortcuts(self):
        self.opened.append("shortcuts")

    def logout_user(self):
        self.opened.append("logout")

    def user_subtitle(self):
        return self.job or "Artist"


def _header(qtbot, mode="vfx", name="Test Artist", job=""):
    parent = Parent(mode, job)
    qtbot.addWidget(parent)
    builder = HeaderBuilder(parent, {"display_name": name, "roles": ["Artist"], "job_title": job})
    header = builder.create_header()
    qtbot.addWidget(header)
    return parent, builder, header


@pytest.mark.parametrize("mode,badge", [("vfx", "VFX"), ("ops", "OPS"), ("all", "")])
def test_the_badge_names_the_application(qtbot, mode, badge):
    _parent, builder, header = _header(qtbot, mode)
    builder.update_responsive_layout(1100)          # the old second rule hid it here
    assert builder.vfx_label.text() == badge
    assert builder.vfx_label.isVisibleTo(header) == bool(badge)


def test_the_name_is_shortened_not_hidden(qtbot):
    long_name = "Maximilian Alexander Konstantin Featherstonehaugh-Worthington III"
    _parent, builder, header = _header(qtbot, name=long_name, job="Senior Compositing Supervisor")
    builder.update_responsive_layout(1024)
    assert builder.profile_text_widget.isVisibleTo(header)
    assert builder.name_label.text() != long_name and builder.name_label.text().endswith("…")
    assert builder.name_label.maximumWidth() <= 220
    assert long_name in builder.profile_widget.toolTip()
    assert builder.role_label.text().startswith("Senior")


def test_name_and_role_have_a_hierarchy(qtbot):
    _parent, builder, _header_w = _header(qtbot)
    assert builder.name_label.styleSheet() != builder.role_label.styleSheet()


def test_initials_keep_whole_characters():
    assert initials("Test Artist") == "TA"
    devanagari = initials("प्रिया शर्मा")
    assert devanagari == "प्रिश"


def test_the_account_menu(qtbot, monkeypatch):
    parent, builder, _header_w = _header(qtbot)
    labels = [entry[0] if entry else None for entry in builder.account_actions()]
    assert labels == ["Change password…", "Keyboard shortcuts", "About Slate", None, "Sign out"]
    builder._show_account_menu(builder.profile_widget)
    menu = builder.account_menu
    actions = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert actions[-1] == "Sign out"
    menu.hide()
    menu.actions()[-1].trigger()
    assert parent.opened == ["logout"]


def test_search_opens_the_palette(qtbot):
    parent, builder, _header_w = _header(qtbot)
    builder.search_button.click()
    assert parent.opened == ["palette"]
    builder.update_responsive_layout(1600)
    assert "Ctrl+K" in builder.search_button.text()
    builder.update_responsive_layout(1000)
    assert "Ctrl+K" in builder.search_button.toolTip()


def test_local_mode_badge_shows_without_runtime_badges(qtbot):
    _parent, builder, header = _header(qtbot)
    assert builder.show_runtime_badges is False
    builder.set_db_runtime_status("sqlite", True)
    assert builder.local_mode_label.isVisibleTo(header)
    builder.set_db_runtime_status("postgres", False)
    assert not builder.local_mode_label.isVisibleTo(header)


def test_the_latency_dot_listens_and_never_queries(qtbot, monkeypatch):
    from slate.widgets import db_speed_indicator_compact as module
    from slate.widgets.db_speed_indicator_compact import DBSpeedIndicatorCompact
    dot = DBSpeedIndicatorCompact()
    qtbot.addWidget(dot)
    assert not hasattr(dot, "timer")
    assert not hasattr(module, "database_manager")
    dot.set_status(True, 0.3)
    assert dot.speed_label.text() == "<1 ms"
    dot.set_status(True, 150)
    slow = dot.current_color.name()
    dot.set_status(True, 80)
    assert dot.current_color.name() != slow            # Fair and Slow differ
    dot.set_status(False, 0)
    assert "Cannot reach" in dot.toolTip()


def test_the_header_forwards_the_monitor_reading(qtbot):
    _parent, builder, _header_w = _header(qtbot)
    builder.update_db_status(True, 12.0)
    assert builder.db_speed_indicator.speed_label.text() == "12 ms"


def test_switch_role_message_has_real_line_breaks():
    import inspect
    source = inspect.getsource(HeaderBuilder._on_dev_switch_role)
    assert "\\\\n" not in source
