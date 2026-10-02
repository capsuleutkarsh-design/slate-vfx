"""
The shared app pieces the tabs build on: tables, pickers, feedback, the close
guard, notifications and the window itself.

Each test is a fault the audit found on more than one screen.
"""
import time
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QVBoxLayout, QWidget


# ------------------------------------------------------------------ tables

def _table(qtbot, rows):
    from slate.gui.components.table_tools import make_item, setup_table
    table = QTableWidget(0, 3)
    qtbot.addWidget(table)
    setup_table(table)
    table.setRowCount(len(rows))
    for r, (key, name, days) in enumerate(rows):
        table.setItem(r, 0, make_item(name, key=key))
        table.setItem(r, 1, make_item(f"{days} d", sort_value=days))
        table.setItem(r, 2, make_item(str(key)))
    return table


def test_tables_are_read_only_by_default(qtbot):
    """Typing into a cell looked saved and was not."""
    from PySide6.QtWidgets import QAbstractItemView
    table = _table(qtbot, [(1, "a", 1)])
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert not (table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)


def test_numbers_sort_by_value_not_text(qtbot):
    table = _table(qtbot, [(1, "a", 10), (2, "b", 9), (3, "c", 100)])
    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert [table.item(r, 1).text() for r in range(3)] == ["9 d", "10 d", "100 d"]


def test_turning_sorting_on_keeps_the_screens_order(qtbot):
    """Qt sorts by the first column, descending, the moment sorting is on."""
    table = _table(qtbot, [(1, "b", 1), (2, "c", 2), (3, "a", 3)])
    assert [table.item(r, 0).text() for r in range(3)] == ["b", "c", "a"]


def test_selection_follows_the_record_across_a_refill(qtbot):
    """After a reload the same row numbers held other records."""
    from slate.gui.components.table_tools import KeepSelection, make_item, selected_keys
    table = _table(qtbot, [(1, "a", 1), (2, "b", 2), (3, "c", 3)])
    table.selectRow(1)
    assert selected_keys(table) == [2]
    with KeepSelection(table):
        table.setRowCount(0)
        rows = [(9, "new", 0), (1, "a", 1), (2, "b", 2)]
        table.setRowCount(len(rows))
        for r, (key, name, days) in enumerate(rows):
            table.setItem(r, 0, make_item(name, key=key))
    assert selected_keys(table) == [2]
    assert table.currentRow() == 2


def test_a_record_that_went_away_is_not_replaced_by_another(qtbot):
    from slate.gui.components.table_tools import KeepSelection, make_item, selected_keys
    table = _table(qtbot, [(1, "a", 1), (2, "b", 2)])
    table.selectRow(0)
    with KeepSelection(table):
        table.setRowCount(1)
        table.setItem(0, 0, make_item("b", key=2))
    assert selected_keys(table) == []


def test_search_and_combo_filters_hide_rows(qtbot):
    from slate.gui.components.table_tools import TableToolbar
    table = _table(qtbot, [(1, "Nuke render", 1), (2, "Maya", 2), (3, "Nuke comp", 3)])
    bar = TableToolbar(table, columns=(0,))
    qtbot.addWidget(bar)
    combo = bar.add_filter("Key", [("All", ""), ("Two", "2")], column=2)
    bar.search.setText("nuke")
    bar.filter.apply()
    assert [table.isRowHidden(r) for r in range(3)] == [False, True, False]
    bar.search.clear()
    combo.setCurrentIndex(1)
    assert [table.isRowHidden(r) for r in range(3)] == [True, False, True]
    assert bar.count_label.text() == "1 of 3"


def test_a_hidden_row_is_not_left_selected(qtbot):
    """Search hid the selected row; Delete would still have acted on it."""
    from slate.gui.components.table_tools import TableToolbar, selected_keys
    table = _table(qtbot, [(1, "alpha", 1), (2, "beta", 2), (3, "gamma", 3)])
    bar = TableToolbar(table, columns=(0,))
    qtbot.addWidget(bar)
    table.selectRow(0)
    assert selected_keys(table) == [1]
    bar.search.setText("gamma")
    bar.filter.apply()
    assert selected_keys(table) == []


def test_empty_cells_sort_last_both_ways(qtbot):
    from slate.gui.components.table_tools import make_item
    table = _table(qtbot, [(1, "a", 5), (2, "b", 1), (3, "c", 3)])
    table.setItem(1, 1, make_item(""))                    # no value
    for order, expected in ((Qt.SortOrder.AscendingOrder, ["3 d", "5 d", ""]),
                            (Qt.SortOrder.DescendingOrder, ["5 d", "3 d", ""])):
        table.horizontalHeader().setSortIndicator(1, order)
        table.sortItems(1, order)
        assert [table.item(r, 1).text() for r in range(3)] == expected


def test_dates_and_times_sort_on_one_scale(qtbot):
    from slate.gui.components.table_tools import make_item
    table = _table(qtbot, [(1, "a", 1), (2, "b", 2)])
    table.setItem(0, 1, make_item("2 Jan 10:00", sort_value=datetime(2026, 1, 2, 10, 0)))
    table.setItem(1, 1, make_item("1 Jan", sort_value=date(2026, 1, 1)))
    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert table.item(0, 1).text() == "1 Jan"


def test_a_refresh_wired_to_a_signal_ignores_the_signals_value(qtbot):
    """currentTextChanged passed its text into load_data(self): the filter broke."""
    from slate.gui.core.offline_notice import on_database_error

    class Screen(QWidget):
        loads = 0

        @on_database_error
        def load_data(self):
            Screen.loads += 1

    screen = Screen()
    qtbot.addWidget(screen)
    screen.load_data("In use")
    assert Screen.loads == 1


def test_a_notice_never_floats_as_its_own_window(qtbot):
    """A load before the table was in a layout made the notice a window."""
    from slate.gui.components.state_notice import show_state

    class Screen(QWidget):
        def __init__(self):
            super().__init__()
            self.grid = QTableWidget(0, 1)          # not in a layout yet

    screen = Screen()
    qtbot.addWidget(screen)
    show_state(screen, "Could not load the list")
    assert not any(w.isWindow() and w.isVisible() and w is not screen
                   for w in screen.findChildren(QWidget))
    from PySide6.QtWidgets import QApplication
    assert not any(type(w).__name__ == "StateNotice" and w.isVisible()
                   for w in QApplication.topLevelWidgets())

def test_inline_edits_are_saved_or_put_back(qtbot, monkeypatch):
    from slate.gui.components import feedback
    from slate.gui.components.table_tools import enable_inline_edits
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: None)
    table = _table(qtbot, [(1, "a", 1)])
    saved = []

    def save(key, column, text):
        if text == "bad":
            return False, "No."
        saved.append((key, column, text))
        return True

    enable_inline_edits(table, save, columns={0})
    table.setCurrentItem(table.item(0, 0))
    table.item(0, 0).setText("good")
    assert saved == [(1, 0, "good")]
    table.item(0, 0).setText("bad")
    assert table.item(0, 0).text() == "good"


# ------------------------------------------------------------ person picker

class _People:
    def get_all_users(self):
        return {
            "priya": {"display_name": "Priya Sharma", "active": True},
            "krishna.chopra": {"display_name": "Krishna Chopra", "active": True},
            "gone": {"display_name": "Gone Person", "active": False},
        }


def test_picker_shows_names_and_hides_leavers(qtbot):
    from slate.gui.components.person_picker import PersonPicker
    picker = PersonPicker(_People())
    qtbot.addWidget(picker)
    assert picker.itemText(0) == "Krishna Chopra (krishna.chopra)"
    assert "gone" not in picker.usernames()


def test_typed_text_that_matches_nobody_is_not_somebody_else(qtbot):
    """Typing 'new.hire.typo' started a checklist for krishna.chopra."""
    from slate.gui.components.person_picker import PersonPicker
    picker = PersonPicker(_People(), allow_empty=False)
    qtbot.addWidget(picker)
    picker.set_username("krishna.chopra")
    picker.setEditText("new.hire.typo")
    assert picker.username() == ""
    assert not picker.is_valid()
    picker.setEditText("priya sharma")
    assert picker.username() == "priya"


def test_picker_suggests_from_a_name(qtbot):
    from slate.gui.components.person_picker import PersonPicker
    picker = PersonPicker(_People())
    qtbot.addWidget(picker)
    assert picker.suggest("Priya") == "priya"
    assert picker.suggest("Nobody Here") == ""


# ----------------------------------------------------------------- feedback

def test_result_reads_the_old_return_shapes():
    from slate.gui.components.feedback import Result
    assert Result.from_value((True, "done")).message == "done"
    assert not Result.from_value((False, "nope"))
    assert not Result.from_value(None), "None is not success - nobody said"
    assert Result.from_value(True)


def test_toasts_sit_above_the_footer_and_carry_an_action(qtbot):
    from PySide6.QtWidgets import QLabel
    from slate.gui.components.feedback import raw_toast
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    layout.addStretch(1)
    footer = QLabel("credit line")
    footer.setObjectName("footer")
    layout.addWidget(footer)
    window.resize(900, 600)
    window.show()
    qtbot.waitExposed(window)
    ran = []
    toast = raw_toast(window, "3 shots archived.", "success", action=("Undo", lambda: ran.append(1)))
    assert toast is not None and toast.isVisible()
    assert toast.geometry().bottom() < footer.geometry().top()
    toast.action_button.click()
    assert ran == [1]


def test_generic_titles_are_known():
    from slate.gui.components.feedback import GENERIC_TITLES
    assert {"error", "warning", "success"} <= GENERIC_TITLES


# --------------------------------------------------------------- work guard

class _Tab(QWidget):
    def __init__(self, unsaved=False, busy=None):
        super().__init__()
        self._unsaved, self._busy, self.stopped = unsaved, busy, False

    def has_unsaved_changes(self):
        return self._unsaved

    def busy_reason(self):
        return None if self.stopped else self._busy

    def shutdown(self, timeout_ms):
        self.stopped = True
        return True


def test_pending_work_finds_unsaved_and_busy_tabs(qtbot):
    from slate.gui.components.work_guard import pending_work
    holder = QWidget()
    qtbot.addWidget(holder)
    inner = _Tab(unsaved=True)
    inner.setParent(holder)
    busy = _Tab(busy="An ingest is copying files.")
    qtbot.addWidget(busy)
    unsaved, running = pending_work({"Leave": holder, "Build & Ingest": busy, "Home": QWidget()})
    assert [label for label, _w in unsaved] == ["Leave"]
    assert [reason for _l, _w, reason in running] == ["An ingest is copying files."]


def test_nothing_pending_means_no_question(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from slate.gui.components.work_guard import confirm_leave
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: asked.append(1))
    assert confirm_leave(None, {"Home": QWidget()}, "close Slate")
    assert asked == []


def test_the_dashboards_own_question_is_asked(qtbot):
    from slate.gui.components.work_guard import confirm_leave

    class Dashboardish(QWidget):
        def __init__(self):
            super().__init__()
            self.asked = None

        def has_unsaved_changes(self):
            return True

        def confirm_discarding_changes(self, action):
            self.asked = action
            return False

    tab = Dashboardish()
    qtbot.addWidget(tab)
    assert not confirm_leave(None, {"VFX Dashboard": tab}, "sign out")
    assert tab.asked == "sign out"


# ----------------------------------------------------------- notifications

def test_badge_counts_to_nine_then_nine_plus():
    from slate.gui.components.notification_center import badge_text
    assert badge_text(0) == "" and badge_text(9) == "9" and badge_text(10) == "9+"


def test_friendly_times():
    from slate.gui.components.notification_center import friendly_time
    now = datetime(2026, 9, 30, 15, 0)
    assert friendly_time(now.timestamp() - 20, now) == "Just now"
    assert friendly_time((now - timedelta(minutes=5)).timestamp(), now) == "5 min ago"
    assert friendly_time(datetime(2026, 9, 30, 9, 5).timestamp(), now) == "Today 09:05"
    assert friendly_time(datetime(2026, 9, 29, 9, 5).timestamp(), now) == "Yesterday 09:05"
    assert "2026" in friendly_time(datetime(2026, 1, 2).timestamp(), now)



def test_a_clock_a_little_ahead_still_reads_just_now():
    """Another workstation's clock ran ahead, so a fresh note showed a full date."""
    from slate.gui.components.notification_center import friendly_time
    now = datetime(2026, 9, 30, 15, 0)
    assert friendly_time(now.timestamp() + 90, now) == "Just now"
    assert "2026" in friendly_time((now + timedelta(days=2)).timestamp(), now)


def test_the_list_says_when_it_is_empty(qtbot):
    from slate.gui.components.notification_center import NotificationsDialog
    dialog = NotificationsDialog([])
    qtbot.addWidget(dialog)
    assert dialog.stack.currentWidget() is dialog.empty
    assert not dialog.mark_button.isVisible()


def test_notifications_find_a_display_name_recipient(tmp_path, monkeypatch):
    """Stored as 'Priya Sharma', looked up as 'priya' - it was never found."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.domain.user_manager import UserManager
    from slate.core.domain.notification_manager import NotificationManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    db = db_module.DatabaseManager(db_path=str(tmp_path / "notes.db"))
    try:
        UserManager(db=db).add_user("priya", "pw", ["Artist"], "Priya Sharma", "Comp")
        notes = NotificationManager(db=db)
        notes.add_notification("Priya Sharma", "KLC_R01_2110 was assigned to you.", "assignment")
        # A row written the old way, by display name, is readdressed.
        db.execute_update(
            "INSERT INTO notifications (id, user_id, message, type, timestamp, read) "
            "VALUES (%s, %s, %s, %s, %s, %s)", ("old", "priya sharma", "Old", "info", time.time(), 0))
        notes.repair_recipients()
        unread = notes.get_unread("PRIYA")
        assert {n["message"] for n in unread} == {"KLC_R01_2110 was assigned to you.", "Old"}
        assert notes.unread_count("priya") == 2
        notes.mark_all_read("priya")
        assert notes.unread_count("priya") == 0
    finally:
        SQLiteManager._instance = None


# ------------------------------------------------------------------ window

def test_fit_to_screen_never_exceeds_the_screen(qtbot):
    from PySide6.QtWidgets import QDialog
    from slate.gui.components.screen_fit import available_size, fit_to_screen
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.setMinimumSize(5000, 5000)
    size = fit_to_screen(dialog, 9000, 9000)
    screen = available_size(dialog)
    assert size.width() <= screen.width() and size.height() <= screen.height()
    assert dialog.minimumHeight() <= screen.height()


def test_page_scroll_hands_back_the_page(qtbot):
    from PySide6.QtWidgets import QListWidget, QStackedWidget
    from slate.gui.components.tab_coordinator import PageScroll, TabCoordinator, page_of
    host = QWidget()
    qtbot.addWidget(host)
    nav, stack = QListWidget(), QStackedWidget()
    coordinator = TabCoordinator(host, nav, stack)
    page = QWidget()
    coordinator.register_tab_factory("Home", lambda: page)
    nav.setCurrentRow(0)
    assert isinstance(stack.currentWidget(), PageScroll)
    assert coordinator.get_current_tab() is page
    assert page_of(stack.currentWidget()) is page
    assert coordinator.set_badge("Home", 3) and coordinator.badge("Home") == 3


def test_startup_does_not_undo_the_first_click():
    """perform_async_login switched to Home whatever the person had opened."""
    from slate.gui.main_window import VFXFolderCreatorApp

    class Nav:
        def __init__(self, row):
            self.row = row

        def currentRow(self):
            return self.row

    fake = type("W", (), {})()
    fake.sidebar_nav, fake._startup_row = Nav(1), 1
    assert not VFXFolderCreatorApp.user_has_navigated(fake)
    fake.sidebar_nav.row = 5
    assert VFXFolderCreatorApp.user_has_navigated(fake)


def test_the_minimum_window_fits_a_small_laptop():
    from slate.gui.main_window import MIN_WINDOW_SIZE
    assert MIN_WINDOW_SIZE[0] <= 1280 - 40 and MIN_WINDOW_SIZE[1] <= 720 - 80


def test_the_studio_logo_does_not_replace_the_wordmark(qtbot, tmp_path):
    from PySide6.QtGui import QColor, QPixmap
    from slate.gui.components.header_builder import HeaderBuilder
    logo = tmp_path / "logo.png"
    pix = QPixmap(64, 64)
    pix.fill(QColor("orange"))
    pix.save(str(logo))

    class Parent(QWidget):
        def __init__(self):
            super().__init__()
            self.config_manager = type("C", (), {"settings": {"global_settings": {}}})()

    parent = Parent()
    qtbot.addWidget(parent)
    builder = HeaderBuilder(parent, {"display_name": "A", "roles": ["Artist"]})
    header = builder.create_header()
    qtbot.addWidget(header)
    assert builder.mark_label is not None and not builder.studio_logo_label.isVisibleTo(header)
    parent.config_manager.settings["global_settings"]["branding_logo_path"] = str(logo)
    builder.reload_branding()
    assert builder.mark_label.text() == "SLATE"
    assert builder.studio_logo_label.isVisibleTo(header)
    assert builder.studio_logo_label.height() <= HeaderBuilder.STUDIO_LOGO_MAX[1]


def test_picker_reads_the_people_directory_by_default(qtbot, monkeypatch):
    """Service accounts and leavers are the directory's call (people_for_picker)."""
    from slate.core.domain import people as directory
    from slate.gui.components.person_picker import PersonPicker
    asked = {}

    def fake(*, include_leavers=False, include_inactive=False, **_):
        asked.update(leavers=include_leavers, inactive=include_inactive)
        return [directory.Person("priya", "Priya Sharma"), directory.Person("rahul.s", "Rahul S")]

    monkeypatch.setattr(directory, "people_for_picker", fake)
    picker = PersonPicker()
    qtbot.addWidget(picker)
    assert picker.usernames() == ["priya", "rahul.s"]
    assert asked == {"leavers": False, "inactive": False}


def test_the_bell_is_the_shared_line_icon(qtbot):
    from slate.gui.components.notification_center import bell_icon
    assert not bell_icon("#ffffff", 20).isNull()
