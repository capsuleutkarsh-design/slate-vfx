"""
A tab must not answer "nothing here" when it means "I could not ask".

This is the screen-level half of the rule that
tests/test_repositories_fail_honestly.py enforces underneath. The repositories
now raise when the database is unreachable; these tests check that the tabs let
that reach the person instead of catching it and rendering an empty grid.

The failure being prevented, in the words it wore on screen: an IT manager
opening Hardware during an outage and being shown a studio that owns no
computers. Nothing was logged, nothing was displayed, and the tab looked
perfectly healthy - which is why it went unnoticed.

Two things are asserted, and the second matters as much as the first:

    the database is unreachable  ->  the tab says so, and does not crash
    the tab has a real bug       ->  it still raises, and is not disguised
"""

import ast
import io
import os

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Every tab that issues its own SQL. Kept as a list rather than discovered, so
# that adding a tab and forgetting this file shows up as a missing entry in a
# review rather than as silence.
TABS_THAT_QUERY = [
    "slate/gui/tabs/home_tab.py",
    "slate/gui/tabs/prod_scheduling_tab.py",
    "slate/gui/tabs/prod_bidding_tab.py",
    "slate/gui/tabs/it_inventory_tab.py",
    "slate/gui/tabs/service_desk_view.py",
    "slate/gui/tabs/my_tickets_view.py",
    "slate/gui/tabs/it_deployment_tab.py",
    "slate/gui/tester_panel.py",
    "slate/gui/tabs/leave_approvals_view.py",
    "slate/gui/tabs/my_leave_view.py",
    "slate/gui/tabs/joining_leaving_view.py",
    "slate/gui/tabs/licence_view.py",
]


def source(rel):
    return io.open(os.path.join(ROOT, rel.replace("/", os.sep)),
                   encoding="utf-8", errors="ignore").read()


def db_try_blocks(text):
    """Try blocks that talk to the database, with their handlers."""
    markers = ("database_manager", "execute_query", "execute_update",
               "self.db", "self.service", "self.repo")
    lines = text.splitlines()
    out = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Try):
            continue
        body = "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
        if any(m in body for m in markers):
            out.append((node, body))
    return out


@pytest.mark.parametrize("rel", TABS_THAT_QUERY,
                         ids=[os.path.basename(p) for p in TABS_THAT_QUERY])
def test_a_tab_never_turns_an_outage_into_empty_data(rel):
    """
    The regression, checked where it lived.

    A handler that catches everything around a database call and falls back to
    empty is how "no machines" got onto the screen. Such a handler must first
    let DatabaseUnavailableError past.
    """
    text = source(rel)
    offenders = []

    for node, body in db_try_blocks(text):
        if "DatabaseUnavailableError" in body:
            continue
        for handler in node.handlers:
            catches_all = handler.type is None or (
                isinstance(handler.type, ast.Name) and handler.type.id == "Exception")
            re_raises = any(isinstance(n, ast.Raise) for n in ast.walk(handler))
            if catches_all and not re_raises:
                offenders.append(handler.lineno)

    assert not offenders, (
        "%s swallows a database failure at line(s) %s - an outage there shows "
        "as an empty screen" % (rel, offenders))


@pytest.mark.parametrize("rel", TABS_THAT_QUERY,
                         ids=[os.path.basename(p) for p in TABS_THAT_QUERY])
def test_a_tab_that_queries_can_report_the_database_being_down(rel):
    """
    Letting the error out is only half a fix; something has to catch it and put
    it on the screen. Either the tab is decorated, or it hands off to a view
    that is.
    """
    text = source(rel)
    assert ("on_database_error" in text or "show_offline" in text), (
        "%s issues queries but has no way to report the database being "
        "unreachable" % rel)


class TestTheNoticeItself:
    """
    The notice stands in for the table - never squeezed into one of its cells
    - offers Try again, and goes away on the next successful refresh.
    """

    def _screen(self, qtbot):
        from PySide6.QtWidgets import QTableWidget, QVBoxLayout, QWidget
        from slate.gui.core import offline_notice
        from slate.core.infra.postgres_manager import DatabaseUnavailableError

        class Screen(QWidget):
            def __init__(self):
                super().__init__()
                self.table = QTableWidget(5, 3)
                QVBoxLayout(self).addWidget(self.table)
                self.down = True
                self.loads = 0

            @offline_notice.on_database_error
            def refresh(self):
                self.loads += 1
                if self.down:
                    raise DatabaseUnavailableError("down")
                self.table.setRowCount(2)

        screen = Screen()
        qtbot.addWidget(screen)
        screen.show()
        return screen

    def test_it_stands_in_for_the_table(self, qtbot):
        from slate.gui.components.state_notice import StateNotice
        screen = self._screen(qtbot)
        screen.refresh()
        notice = screen.findChild(StateNotice)
        assert notice is not None and notice.isVisible()
        assert not screen.table.isVisible()
        assert screen.table.rowCount() == 5            # the table is not written into
        assert "reach the studio database" in notice.title.text()

    def test_try_again_and_a_later_success_clear_it(self, qtbot):
        from slate.gui.components.state_notice import StateNotice
        screen = self._screen(qtbot)
        screen.refresh()
        notice = screen.findChild(StateNotice)
        screen.down = False
        notice._button_widgets[0].click()               # Try again
        assert screen.loads == 2
        assert not notice.isVisible()
        assert screen.table.isVisible() and screen.table.rowCount() == 2

    def test_a_widget_with_no_table_at_all_is_not_an_error(self, qtbot):
        from PySide6.QtWidgets import QLabel
        from slate.gui.components.state_notice import show_state

        label = QLabel("nothing here")
        qtbot.addWidget(label)
        assert show_state(label, "title") is False
