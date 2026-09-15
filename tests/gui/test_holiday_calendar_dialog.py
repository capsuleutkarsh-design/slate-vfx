"""
The screen HR keeps the holiday calendar on.

It could add and remove, and that was all. Correcting a date or a name meant
removing the holiday and putting it back - two steps of which the destructive
one succeeds alone. It listed every holiday the studio had ever had, with no
way to narrow it to the year somebody came to fix. And the places a holiday
could apply to were three fixed city names belonging to the studio this was
written for, while a holiday only applies to a location whose spelling matches
the user records exactly.
"""

from datetime import date

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeRepo:
    """A calendar in memory, answering what the screen asks of it."""

    def __init__(self, rows=None, locations=None):
        self.rows = list(rows or [])
        self._locations = list(locations or [])
        self.updated = []
        self.removed = []
        self._next_id = max([r["id"] for r in self.rows], default=0) + 1

    def holiday_rows(self, year=None):
        rows = [r for r in self.rows
                if year is None or r["holiday_date"].year == int(year)]
        return sorted(rows, key=lambda r: r["holiday_date"])

    def holiday_years(self):
        return sorted({r["holiday_date"].year for r in self.rows}, reverse=True)

    def locations(self):
        return list(self._locations)

    def add_holiday(self, day, name, location="All"):
        self.rows.append({"id": self._next_id, "holiday_date": day,
                          "name": name, "location": location})
        self._next_id += 1
        return True

    def update_holiday(self, holiday_id, day, name, location="All"):
        self.updated.append((holiday_id, day, name, location))
        for row in self.rows:
            if row["id"] == holiday_id:
                row.update({"holiday_date": day, "name": name,
                            "location": location})
        return True

    def remove_holiday(self, holiday_id):
        self.removed.append(holiday_id)
        self.rows = [r for r in self.rows if r["id"] != holiday_id]
        return True


CALENDAR = [
    {"id": 1, "holiday_date": date(2025, 12, 25), "name": "Christmas",
     "location": "All"},
    {"id": 2, "holiday_date": date(2026, 1, 26), "name": "Republic Day",
     "location": "All"},
    {"id": 3, "holiday_date": date(2026, 11, 8), "name": "Diwali",
     "location": "All"},
]


def _dialog(qtbot, rows=CALENDAR, locations=("Pune", "Hyderabad"), year=2026):
    from slate.gui.tabs.leave_admin import HolidayCalendarDialog

    repo = FakeRepo(rows, locations)
    view = HolidayCalendarDialog(repo=repo, year=year)
    qtbot.addWidget(view)
    return view, repo


# ------------------------------------------------------------ the year filter

def test_it_opens_on_the_year_it_was_asked_for(app, qtbot):
    """
    Opened from the attendance grid, which is where a wrong holiday gets
    noticed, it opens on the year being looked at.
    """
    view, _ = _dialog(qtbot, year=2026)

    assert view.combo_year.currentData() == 2026
    assert view.table.rowCount() == 2, "2026 has two, 2025 has one"


def test_the_whole_calendar_is_still_reachable(app, qtbot):
    view, _ = _dialog(qtbot)

    view.combo_year.setCurrentIndex(view.combo_year.findText(view.ANY_YEAR))
    assert view.table.rowCount() == 3


def test_a_year_with_nothing_in_it_yet_is_offered(app, qtbot):
    """
    January is when the next year's calendar gets entered, and it cannot be
    entered into a year the list does not offer.
    """
    view, _ = _dialog(qtbot)
    this_year = date.today().year

    offered = [view.combo_year.itemData(i) for i in range(view.combo_year.count())]
    assert this_year in offered and this_year + 1 in offered


def test_the_count_says_how_many(app, qtbot):
    view, _ = _dialog(qtbot, year=2025)
    assert "1 holiday" in view.lbl_count.text()


# -------------------------------------------------------------- the locations

def test_the_places_offered_are_the_studios_own(app, qtbot):
    view, _ = _dialog(qtbot, locations=("Pune", "Hyderabad"))

    offered = [view.location.itemText(i) for i in range(view.location.count())]
    assert offered == ["All", "Pune", "Hyderabad"]
    assert "Mumbai" not in offered, \
        "the three fixed cities belonged to one studio"


def test_a_studio_with_one_office_is_offered_only_all(app, qtbot):
    view, _ = _dialog(qtbot, locations=())
    offered = [view.location.itemText(i) for i in range(view.location.count())]
    assert offered == ["All"]


# ----------------------------------------------------------------- correcting

def test_a_holiday_can_be_corrected_in_place(app, qtbot, monkeypatch):
    from slate.gui.tabs import leave_admin

    view, repo = _dialog(qtbot, year=2026)
    view.table.selectRow(0)          # Republic Day, 26 January

    class Corrected:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {"holiday_date": date(2026, 1, 27),
                    "name": "Republic Day", "location": "All"}

    monkeypatch.setattr(leave_admin, "HolidayEditDialog", Corrected)
    view.edit()

    assert repo.updated == [(2, date(2026, 1, 27), "Republic Day", "All")]
    assert repo.removed == [], "correcting must never go through a delete"


def test_cancelling_the_edit_changes_nothing(app, qtbot, monkeypatch):
    from slate.gui.tabs import leave_admin

    view, repo = _dialog(qtbot, year=2026)
    view.table.selectRow(0)

    class Cancelled:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def values(self):
            raise AssertionError("a cancelled dialog must not be read")

    monkeypatch.setattr(leave_admin, "HolidayEditDialog", Cancelled)
    view.edit()

    assert repo.updated == []


def test_nothing_can_be_edited_or_removed_until_something_is_picked(app, qtbot):
    view, _ = _dialog(qtbot)

    assert not view.btn_edit.isEnabled()
    assert not view.btn_remove.isEnabled()

    view.table.selectRow(0)
    assert view.btn_edit.isEnabled()
    assert view.btn_remove.isEnabled()


# ------------------------------------------------------------- the edit dialog

def test_the_edit_dialog_opens_on_the_holiday_it_was_given(app, qtbot):
    from slate.gui.tabs.leave_admin import HolidayEditDialog

    view = HolidayEditDialog(["Pune"], CALENDAR[2])
    qtbot.addWidget(view)

    assert view.name.text() == "Diwali"
    assert view.values()["holiday_date"] == date(2026, 11, 8)
    assert view.location.currentText() == "All"


def test_the_edit_dialog_refuses_a_holiday_with_no_name(app, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from slate.gui.tabs.leave_admin import HolidayEditDialog

    view = HolidayEditDialog(["Pune"], CALENDAR[2])
    qtbot.addWidget(view)
    view.name.setText("   ")

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    view._accept()
    assert view.result() != QDialog.DialogCode.Accepted


# ------------------------------------------------- reaching it from attendance

def test_the_attendance_grid_can_reach_the_calendar():
    """
    The grid shades holidays, so it is where a wrong one is noticed - by
    somebody looking at the month. The editor could only be opened from the
    Leave tab, which is not where anybody was when they spotted it.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent.parent
              / "slate" / "gui" / "attendance_tab.py").read_text(encoding="utf-8")

    assert "def edit_holidays" in source
    assert "HolidayCalendarDialog" in source
    assert 'can(self.roles, "manage_leave")' in source, \
        "keeping the studio's calendar is HR's, not everyone who sees the grid"
    assert "self._holiday_cache = {}" in source, \
        "the grid caches holidays, so an edit nobody can see having worked " \
        "gets made twice"


def test_the_attendance_year_picker_does_not_expire():
    """A fixed 2020-2030 is the date this screen stops working on."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent.parent
              / "slate" / "gui" / "attendance_tab.py").read_text(encoding="utf-8")

    assert "setRange(2020, 2030)" not in source
    assert "setRange(this_year - 6, this_year + 2)" in source
