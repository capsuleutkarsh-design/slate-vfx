"""
The command palette (Ctrl+K): real screens only, honest commands, sensible matching.
"""
import pytest
from PySide6.QtCore import Qt

from slate.gui.components.quick_search_controller import QuickSearchControllerMixin as Palette

ADMIN = {"username": "admin", "user_id": "admin", "display_name": "System Admin",
         "roles": ["Developer"], "role": "Developer"}
ARTIST = {"username": "artist", "user_id": "artist", "display_name": "Test Artist",
          "roles": ["Artist"], "role": "Artist"}


def _window(qtbot, cls_path, user):
    module, name = cls_path.rsplit(".", 1)
    cls = getattr(__import__(module, fromlist=[name]), name)
    win = cls(dict(user))
    qtbot.addWidget(win)
    return win


@pytest.fixture
def admin_window(qtbot, mock_db):
    return _window(qtbot, "slate.gui.main_window.VFXFolderCreatorApp", ADMIN)


def test_no_headings_in_the_palette(admin_window):
    labels = [row["label"] for row in admin_window.palette_commands()]
    assert not any("__HEADER__" in label for label in labels)
    assert "Go to Home" in labels


def test_folded_groups_are_still_listed(admin_window):
    tc = admin_window.tab_coordinator
    tc.set_group_folded("PRODUCTION", True)
    labels = [row["label"] for row in admin_window.palette_commands()]
    assert "Go to Home" in labels and "Go to Build & Ingest" in labels
    assert admin_window._go_to_tab("Build & Ingest")
    assert not tc.groups[0]["folded"]


def test_artist_is_not_offered_screens_they_lack(qtbot, mock_db):
    import slate.core.domain.user_manager as um
    win = _window(qtbot, "slate.gui.vfx_studio_window.VFXStudioWindow", ARTIST)
    have = set(win._palette_tab_labels())
    labels = [row["label"] for row in win.palette_commands()]
    if "Timeline Viewer" not in have:
        assert "Open Timeline Viewer" not in labels
        assert "Rebuild Timeline from Dashboard" not in labels
    assert "Clear temporary files (maintenance)" not in labels
    assert "Diagnostics (Ctrl+Shift+D)" in labels


def test_matching_prefers_word_starts():
    rows = [{"kind": "action", "label": "Clear temporary files (maintenance)", "keywords": "maintenance cache temp"},
            {"kind": "action", "label": "Open Settings", "keywords": "settings preferences"},
            {"kind": "tab", "label": "Go to Admin Panel", "keywords": "go open Admin Panel"},
            {"kind": "tab", "label": "Go to Hardware", "keywords": "go open Hardware"}]
    hits = [row["label"] for _s, row in Palette.match_rows("cache", rows)]
    assert hits == ["Clear temporary files (maintenance)"]
    hits = [row["label"] for _s, row in Palette.match_rows("hard", rows)]
    assert hits == ["Go to Hardware"]


def test_recent_screens_are_kept_by_name(admin_window):
    clean = Palette._sanitize_omnibar_entry({"kind": "tab", "label": "Go to Leave", "tab_label": "Leave",
                                             "tab_index": 12})
    assert clean == {"kind": "tab", "label": "Go to Leave", "tab_label": "Leave"}
    admin_window.global_settings["omnibar_recent_entries"] = [
        {"kind": "tab", "label": "Go to Tab: X", "tab_index": 3},          # old row-number entry
        {"kind": "tab", "label": "Go to Home", "tab_label": "Home"},
        {"kind": "tab", "label": "Go to Nope", "tab_label": "Not here"}]
    admin_window.settings["global_settings"] = admin_window.global_settings
    admin_window._load_omnibar_state()
    assert [e["tab_label"] for e in admin_window._omnibar_recent_entries] == ["Home"]


def test_the_palette_looks_like_one(admin_window, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog, QLineEdit
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    admin_window.show_quick_search()
    dialog = admin_window._palette_dialog
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    field = dialog.findChild(QLineEdit, "omnibarInput")
    assert field.placeholderText() == "Search commands, screens or shots (e.g. shot 042)…"


def test_enter_never_starts_maintenance(admin_window, monkeypatch):
    """Ctrl+K then Enter used to run 'Clear temporary files' for admins."""
    from PySide6.QtWidgets import QDialog, QListWidget
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    labels = [row["label"] for row in admin_window.palette_commands()]
    assert "Clear temporary files (maintenance)" in labels
    admin_window.show_quick_search()
    results = admin_window._palette_dialog.findChild(QListWidget, "omnibarResults")
    current = results.currentItem().data(Qt.ItemDataRole.UserRole)
    assert not current.get("careful") and current["kind"] == "tab"
    assert Palette.palette_preselect([None, {"careful": True}, {"kind": "tab"}]) == 2
    assert Palette.palette_preselect([{"careful": True}]) is None
