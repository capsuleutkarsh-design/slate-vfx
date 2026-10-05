"""
Punching, on both databases, and the rules every attendance screen shares.

Each test is an audit finding (HR-0xx):

    a second Punch In silently ignored but reported as success     HR-006
    a second Punch Out overwriting the first                       HR-007
    the automatic punch-out closing one day and inventing 23 hours HR-008
    three different late counts for one person                    HR-009
    the export's hours going negative overnight                    HR-010
    corrections nobody could attribute                             HR-019
    the WFH flag that could not be changed after punching in       HR-020
    the personal view reading the whole studio every minute        HR-034
    a changed 'Late after' not reaching the tables                 HR-036
    the message showing the PC's clock, the record the server's    HR-037
"""

from datetime import date, datetime, timedelta

import pytest

from slate.core.domain import attendance_rules as rules
from slate.core.domain import leave_policy as lp
from slate.core.domain.central_attendance import CentralAttendance


@pytest.fixture(params=["sqlite", "postgres"])
def att(request):
    db = request.getfixturevalue("mock_db" if request.param == "sqlite" else "pg_db")
    lp.set_overrides({})
    yield CentralAttendance(db)
    lp.set_overrides({})


def _put(att, user, day, t_in, t_out=None, meta=None):
    att.db.execute_update(
        "INSERT INTO attendance_log (user_id, day_date, punch_in, punch_out, pc_name, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user, day.isoformat(), t_in, t_out, "TEST", __import__("json").dumps(meta or {})))


# ------------------------------------------------------------------ HR-006

def test_punching_in_twice_is_refused_with_the_time(att):
    first = att.log_action("asha", "in")
    with pytest.raises(ValueError, match="Already punched in at"):
        att.log_action("asha", "in")
    assert first["session"] == 1


def test_punching_in_after_punching_out_starts_a_second_session(att):
    att.log_action("asha", "in")
    att.log_action("asha", "out")
    second = att.log_action("asha", "in")
    assert second["session"] == 2
    state = att.today_state("asha")
    assert state["state"] == "working" and state["sessions"] == 2
    att.log_action("asha", "out")
    assert att.today_state("asha")["state"] == "done"
    today = date.today()
    entry = att.get_user_month("asha", today.year, today.month)[f"{today.day:02d}"]
    assert len(entry["sessions"]) == 2 and all(s["out"] for s in entry["sessions"])


def test_signing_in_never_starts_a_session_by_itself(att):
    assert att.log_action("asha", "in", automatic=True)["session"] == 1
    assert att.log_action("asha", "in", automatic=True) is None    # signed in again
    att.log_action("asha", "out")
    assert att.log_action("asha", "in", automatic=True) is None    # after punching out
    assert att.today_state("asha")["state"] == "done"


# ------------------------------------------------------------------ HR-007

def test_a_second_punch_out_does_not_overwrite_the_first(att):
    att.log_action("asha", "in")
    first = att.log_action("asha", "out")
    with pytest.raises(ValueError, match="Already punched out"):
        att.log_action("asha", "out")
    assert att.today_state("asha")["out"] == first["time"][:5]


def test_punching_out_without_punching_in_is_refused(att):
    with pytest.raises(ValueError, match="not punched in"):
        att.log_action("nobody", "out")


# ------------------------------------------------------------------ HR-037

def test_the_stored_time_is_what_is_returned(att):
    stored = att.log_action("asha", "in")
    assert att.today_state("asha")["in"] == stored["time"][:5]
    assert stored["date"] == date.today().isoformat()


# ------------------------------------------------------------------ HR-008

def test_every_forgotten_day_is_closed_and_none_gets_invented_hours(att):
    today = date.today()
    early, late = today - timedelta(days=3), today - timedelta(days=2)
    _put(att, "asha", early, "09:30:00")
    _put(att, "asha", late, "20:15:00")
    lp.set_overrides({"auto_logout_time": "19:30"})
    att.log_action("asha", "in")

    days = att.get_user_days("asha", early, late)
    assert days[early]["out"] == "19:30" and days[early]["auto_logout"]
    assert days[late]["out"] == "" and days[late]["missing_punch_out"]
    assert rules.day_hours(days[late]) == 0
    assert rules.day_state(days[late], late, today) == rules.MISSING_OUT


# ------------------------------------------------------------------ HR-019 / HR-023

def test_a_correction_records_who_why_and_what_it_was(att):
    day = date.today() - timedelta(days=1)
    _put(att, "asha", day, "09:30:00", "18:30:00")
    ok, _ = att.update_record("asha", day.year, day.month, day.day, "10:00", "19:00",
                              editor="hr.meera", reason="Machine was down")
    assert ok
    ok, _ = att.update_record("asha", day.year, day.month, day.day, "10:05", "19:00",
                              editor="hr.kavya", reason="Second look")
    entry = att.get_user_days("asha", day, day)[day]
    assert entry["edited_by"] == "hr.kavya"
    history = entry["edit_history"]
    assert [h["by"] for h in history] == ["hr.meera", "hr.kavya"]
    assert history[0]["was_in"] == "09:30"


def test_a_correction_needs_a_time_and_a_reason(att):
    day = date.today() - timedelta(days=1)
    assert not att.update_record("asha", day.year, day.month, day.day, "", "",
                                 editor="hr", reason="x")[0]
    assert not att.update_record("asha", day.year, day.month, day.day, "10:00", "",
                                 editor="hr", reason="")[0]


def test_clearing_a_day_removes_it(att):
    day = date.today() - timedelta(days=1)
    _put(att, "asha", day, "09:30:00", "18:30:00")
    assert not att.clear_day("asha", day, "hr.meera", "")[0]
    assert att.clear_day("asha", day, "hr.meera", "Punched on the wrong account")[0]
    assert att.get_day("asha", day) is None


# ------------------------------------------------------------------ HR-020

def test_wfh_can_be_changed_after_punching_in(att):
    att.log_action("asha", "in")
    assert att.set_wfh("asha", date.today(), True)
    assert att.today_state("asha")["wfh"] is True
    att.log_action("asha", "out")
    assert att.today_state("asha")["wfh"] is True, "punch-out keeps the WFH flag"


# ------------------------------------------------------------------ HR-034

def test_the_personal_month_reads_one_person(att):
    day = date.today().replace(day=1)
    _put(att, "asha", day, "09:30:00", "18:30:00")
    _put(att, "ravi", day, "09:30:00", "18:30:00")
    mine = att.get_user_month("asha", day.year, day.month)
    assert list(mine) == ["01"]
    assert set(att.get_full_month_data(day.year, day.month)) == {"asha", "ravi"}


# ------------------------------------------------------------------ HR-036

def test_late_after_is_read_when_used(att):
    lp.set_overrides({"late_cutoff": "11:15"})
    assert (att.LATE_CUTOFF_HOUR, att.LATE_CUTOFF_MINUTE) == (11, 15)
    lp.set_overrides({"late_cutoff": "09:00"})
    assert (att.LATE_CUTOFF_HOUR, att.LATE_CUTOFF_MINUTE) == (9, 0)


# --------------------------------------------------------- the shared rules

def test_late_only_counts_on_days_somebody_was_expected():
    lp.set_overrides({})
    sunday, tuesday = date(2026, 9, 13), date(2026, 9, 15)
    holiday = date(2026, 9, 14)
    assert not rules.is_late(sunday, "12:00")
    assert not rules.is_late(holiday, "10:52", {holiday})
    assert rules.is_late(tuesday, "11:00")
    assert not rules.is_late(tuesday, "10:45")


def test_overnight_and_second_sessions_count_their_hours():
    assert rules.day_hours({"in": "20:00", "out": "05:30"}) == pytest.approx(9.5)
    two = {"in": "09:00", "out": "19:00",
           "sessions": [{"in": "09:00", "out": "13:00"}, {"in": "15:00", "out": "19:00"}]}
    assert rules.day_hours(two) == pytest.approx(8.0)


def test_day_states():
    lp.set_overrides({})
    today = date(2026, 9, 30)
    tuesday = date(2026, 9, 15)
    assert rules.day_state({}, tuesday, today) == rules.ABSENT
    assert rules.day_state({}, tuesday, today, leave={"type": "Sick"}) == rules.LEAVE
    assert rules.day_state({"in": "09:30", "out": ""}, tuesday, today) == rules.MISSING_OUT
    assert rules.day_state({"in": "", "out": "18:00"}, tuesday, today) == rules.MISSING_IN
    assert rules.day_state({"in": "09:30", "out": "13:30"}, tuesday, today) == rules.SHORT
    assert rules.day_state({"in": "09:30", "out": "18:45"}, tuesday, today) == rules.PRESENT
    assert rules.day_state({"in": "09:30", "out": ""}, today, today) == rules.WORKING
    assert rules.day_state({}, date(2026, 9, 13), today) == rules.WEEKLY_OFF


def test_the_streak_crosses_months_and_leave_does_not_break_it():
    lp.set_overrides({})
    today = date(2026, 10, 2)                    # Friday
    days = {date(2026, 9, 29): {"in": "10:00"}, date(2026, 9, 30): {"in": "10:00"},
            date(2026, 10, 1): {"in": "10:00"}}
    assert rules.calculate_streak(days, today) == 3
    days.pop(date(2026, 9, 30))
    assert rules.calculate_streak(days, today, leave_days={date(2026, 9, 30)}) == 2


# ---------------------------------------------------------- leave in attendance

def test_approved_leave_reaches_attendance(mock_db):
    from slate.core.infra.leave_repository import LeaveRepository
    from slate.core.domain.user_manager import UserManager
    UserManager(db=mock_db).add_user("hr.meera", "pw", ["HR"], "Meera", "HR")
    repo = LeaveRepository(mock_db)
    day = date(2026, 9, 21)
    sent = repo.submit("aarav", "Sick", day, day, True, "fever", half_day_part="first")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    leave = repo.approved_leave(date(2026, 9, 1), date(2026, 9, 30))
    assert leave["aarav"][day]["type"] == "Sick"
    assert leave["aarav"][day]["days"] == 0.5


# ------------------------------------------------------------ NEW-people-1

def test_a_correction_clears_the_auto_and_missing_flags(att):
    today = date.today()
    auto_day, late_day = today - timedelta(days=3), today - timedelta(days=2)
    _put(att, "asha", auto_day, "09:30:00")
    _put(att, "asha", late_day, "20:15:00")
    lp.set_overrides({"auto_logout_time": "19:30"})
    att.log_action("asha", "in")
    for day in (auto_day, late_day):
        ok, _ = att.update_record("asha", day.year, day.month, day.day, "09:30", "18:30",
                                  editor="hr.meera", reason="checked with the person")
        assert ok
        entry = att.get_user_days("asha", day, day)[day]
        assert not entry["auto_logout"] and not entry["missing_punch_out"]
        assert rules.day_state(entry, day, today) == rules.PRESENT


def test_days_corrected_before_the_fix_are_repaired(att):
    import json
    from slate.core.infra.migrations.people_schema import clear_corrected_flags
    day = date.today() - timedelta(days=4)
    _put(att, "asha", day, "09:30:00", "18:30:00",
         meta={"missing_punch_out": True, "admin_edit": True, "edited_by": "hr"})
    _put(att, "ravi", day, "09:30:00", "19:30:00", meta={"auto_logout": True})
    assert clear_corrected_flags(att.db)
    assert not att.get_user_days("asha", day, day)[day]["missing_punch_out"]
    assert att.get_user_days("ravi", day, day)[day]["auto_logout"], "not hand-edited: left alone"


# ------------------------------------------------------------- end to end

def test_a_second_punch_in_end_to_end_through_home(att, qtbot):
    """Integration: Home's punch panel over the real domain, both databases."""
    from slate.gui.tabs import home_tab
    from slate.gui.tabs.home_tab import HomeLoaderWorker, HomeTab

    class Host:
        def __init__(self):
            self.said = []

        def show_feedback(self, text, level, *a, **k):
            self.said.append((level, text))

    host = Host()
    tab = HomeTab(user_data={"username": "asha", "display_name": "Asha"}, mode="ops")
    qtbot.addWidget(tab)
    tab.attendance = att
    tab._host = lambda: host
    reader = HomeLoaderWorker("asha", None, mode="ops", db=att.db)
    tab._read_todays_punch = reader.todays_punch

    from slate.gui.components import feedback
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(feedback, "confirm", lambda *a, **k: True)      # "Punch out at 18:42?" - yes

    def punch(action):
        tab.do_punch(action)                                  # on a worker now
        qtbot.waitUntil(lambda: not tab.punch_busy(), timeout=5000)

    punch("in")
    assert host.said[-1][0] == "success" and not tab.btn_punch_in.isEnabled()
    punch("in")                                           # refused, with the rule
    assert host.said[-1] == ("warning", host.said[-1][1]) and "Already punched in" in host.said[-1][1]
    punch("out")
    mp.undo()
    assert tab.btn_punch_in.isEnabled() and not tab.btn_punch_out.isEnabled()
    # Make the first session clearly earlier, then come back from lunch.
    row = att._row("asha", att._server_now()[0])
    sessions = att._sessions(row)
    sessions[0]["in"], sessions[0]["out"] = "08:00:00", "08:30:00"
    att.db.execute_update("UPDATE attendance_log SET punch_in = %s, punch_out = %s, metadata = %s WHERE id = %s",
                          ("08:00:00", "08:30:00", __import__("json").dumps({"sessions": sessions}), row["id"]))
    punch("in")
    assert host.said[-1][0] == "success" and "session 2" in host.said[-1][1]
    status = reader.todays_punch()
    assert status["sessions"] == 2 and str(status["first_in"])[:5] == "08:00"
    assert str(status["punch_in"])[:5] != "08:00" and status["punch_out"] is None
    assert "session 2 today" in tab.lbl_punch_status.text()
    assert att.today_state("asha")["state"] == "working"
