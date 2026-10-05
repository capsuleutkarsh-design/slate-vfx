"""
The merged sidebar builder hands every screen the arguments its area added
(roles=, user_data=, ...). Integration checks after the area merges.
"""
import pytest
from PySide6.QtWidgets import QWidget

OPS = {"username": "admin", "user_id": "admin", "display_name": "System Admin",
       "roles": ["Developer"], "role": "Developer"}
ARTIST = {"username": "artist", "user_id": "artist", "display_name": "Test Artist",
          "roles": ["Artist"], "role": "Artist"}


def _recorder(calls, name):
    class Fake(QWidget):
        templates_refresh_requested = None

        def __init__(self, *args, **kwargs):
            super().__init__()
            calls[name] = (args, kwargs)
    return Fake


@pytest.fixture
def fakes(monkeypatch):
    """Every screen class replaced by a recorder of its constructor arguments."""
    import importlib
    from PySide6.QtCore import Signal

    calls = {}

    class FakeSettings(QWidget):
        templates_refresh_requested = Signal()
        global_settings_updated = Signal(dict)

        def __init__(self, *args, **kwargs):
            super().__init__()
            calls["SettingsTab"] = (args, kwargs)

    targets = {
        ("slate.gui.tabs.settings_tab", "SettingsTab"): FakeSettings,
        ("slate.gui.admin_panel", "AdminPanelTab"): _recorder(calls, "AdminPanelTab"),
    }
    for (module, name), fake in targets.items():
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, fake)
    return calls


@pytest.mark.parametrize("user, expect_roles", [(OPS, ["Developer"]), (ARTIST, ["Artist"])])
def test_settings_and_admin_panel_get_roles(qtbot, mock_db, fakes, user, expect_roles):
    from slate.gui.main_window import VFXFolderCreatorApp
    win = VFXFolderCreatorApp(dict(user), app_mode="all")
    qtbot.addWidget(win)
    win._is_sqlite_fallback_mode = lambda: False
    assert win._screen_roles() and [r.lower() for r in win._screen_roles()] == [r.lower() for r in expect_roles]
    win._get_tab_instance("Settings", create=True)
    assert fakes["SettingsTab"][1].get("roles") == win._screen_roles()
    if "Admin Panel" in win.tab_coordinator.tab_labels and win.tab_coordinator.nav_items:
        permitted = [e for e in win.tab_coordinator.nav_items
                     if e.get("label") == "Admin Panel" and e.get("permitted", True)]
        if permitted:
            win._get_tab_instance("Admin Panel", create=True)
            assert fakes["AdminPanelTab"][1].get("roles") == win._screen_roles()
            assert fakes["AdminPanelTab"][1].get("current_username") == user["user_id"]


def test_the_admin_gets_the_admin_panel_with_roles(qtbot, mock_db, fakes):
    from slate.gui.main_window import VFXFolderCreatorApp
    win = VFXFolderCreatorApp(dict(OPS), app_mode="all")
    qtbot.addWidget(win)
    win._is_sqlite_fallback_mode = lambda: False
    win._get_tab_instance("Admin Panel", create=True)
    assert fakes["AdminPanelTab"][1]["roles"] == ["Developer"]
