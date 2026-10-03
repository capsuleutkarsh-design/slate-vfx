"""The Help window: safe search over text, groups that match the sidebar."""
from slate.gui.help_dialog import GROUPS, HelpDialog


def _help(qtbot, tab="leave", mode=None):
    dialog = HelpDialog(None, initial_tab=tab, mode=mode)
    qtbot.addWidget(dialog)
    return dialog


def test_a_search_is_shown_as_text(qtbot):
    dialog = _help(qtbot)
    dialog._filter("<b>zzz</b>")
    page = dialog.nothing.toHtml()
    assert "&lt;b&gt;" in page
    assert dialog.crumb.text() == "Search"


def test_search_reads_words_not_markup(qtbot):
    dialog = _help(qtbot)
    for _item, _sid, haystack in dialog._items:
        assert "<td" not in haystack and "<h1" not in haystack
    dialog._filter("td")
    assert dialog.tally.text().startswith("0 of")


def test_clearing_a_search_returns_to_the_page(qtbot):
    dialog = _help(qtbot, "leave")
    assert dialog.current_section() == "leave"
    dialog._filter("zzzqqq")
    assert dialog.current_section() is None
    dialog._filter("")
    assert dialog.current_section() == "leave"


def test_groups_match_the_sidebar():
    names = [g for g, _ids in GROUPS]
    for heading in ("Production", "People", "IT & Infra", "Administration", "System"):
        assert heading in names
    admin = dict(GROUPS)["Administration"]
    assert "users_roles" in admin and "admin_panel" in admin
