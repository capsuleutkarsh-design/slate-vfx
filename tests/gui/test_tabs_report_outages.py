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
    """The notice has to work on tabs that have no EmptyState widget."""

    def test_it_uses_the_table_when_there_is_no_empty_state(self, qtbot):
        from PySide6.QtWidgets import QTableWidget
        from slate.gui.core import offline_notice

        table = QTableWidget(5, 3)
        qtbot.addWidget(table)

        assert offline_notice._say_it_in_the_table(table) is True
        assert table.rowCount() == 1
        assert "not responding" in table.item(0, 0).text()

    def test_a_later_successful_refresh_overwrites_it(self, qtbot):
        """The message needs no clearing - real rows replace it."""
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
        from slate.gui.core import offline_notice

        table = QTableWidget(0, 2)
        qtbot.addWidget(table)
        offline_notice._say_it_in_the_table(table)

        table.setRowCount(2)
        table.setItem(0, 0, QTableWidgetItem("PC-01"))
        assert table.item(0, 0).text() == "PC-01"

    def test_a_widget_with_no_table_at_all_is_not_an_error(self, qtbot):
        from PySide6.QtWidgets import QLabel
        from slate.gui.core import offline_notice

        label = QLabel("nothing here")
        qtbot.addWidget(label)
        assert offline_notice._say_it_in_the_table(label) is False
