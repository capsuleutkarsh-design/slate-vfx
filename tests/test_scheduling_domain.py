"""
The schedule's rules, without a database.

Each test names the audit finding it holds. The tab used to keep these rules
inline (or not at all): shifts walked dependents recursively and committed
one row at a time, completed work moved, empty dates crashed the click,
'15/10/2026' compared as earlier than '2026-09-30', and the card called every
unfinished milestone "In Progress".
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from slate.core.domain import scheduling as DS


def ms(mid, name, start, end, status="Scheduled", dep=None, project="AVTR3", owner="", effort=None):
    return DS.Milestone(id=mid, project_code=project, name=name,
                        start=DS.parse_date(start) if start else None,
                        end=DS.parse_date(end) if end else None,
                        status=status, depends_on_id=dep, owner=owner,
                        effort_days=Decimal(str(effort)) if effort is not None else None)


# Sunday is the studio's weekly off by default; tests use Mon-Fri to be clear.
WEEKDAYS = DS.WorkCalendar(weekly_offs=(5, 6))


# ------------------------------------------------------------------ rows

def test_a_row_reads_iso_and_old_day_first_dates():
    m = DS.Milestone.from_row({"id": 3, "project_code": "AVTR3", "milestone": "Legacy",
                               "start_date": "30/09/2026", "end_date": "2026-10-15",
                               "status": "in progress", "project_active": 1})
    assert m.start == date(2026, 9, 30) and m.end == date(2026, 10, 15)
    assert m.status == "In Progress"
    assert not m.archived


def test_an_inactive_project_marks_its_milestones_archived():
    assert DS.Milestone.from_row({"id": 1, "project_active": 0}).archived
    assert not DS.Milestone.from_row({"id": 1, "project_active": None}).archived


def test_PRD_018_legacy_dates_are_not_counted_overdue_by_text():
    legacy = DS.Milestone.from_row({"id": 1, "milestone": "Legacy DD/MM row", "status": "Scheduled",
                                    "start_date": "01/10/2026", "end_date": "15/10/2026"})
    assert not DS.is_overdue(legacy, today=date(2026, 9, 30))
    broken = DS.Milestone.from_row({"id": 2, "milestone": "x", "end_date": "None"})
    assert broken.end is None and not DS.is_overdue(broken, today=date(2030, 1, 1))


# ------------------------------------------------------------------ statuses

def test_PRD_049_every_status_has_a_tone_and_unknown_ones_are_idle():
    assert set(DS.STATUSES) >= {"Scheduled", "In Progress", "On Hold", "Blocked",
                                "Completed", "Cancelled"}
    for status in DS.STATUSES:
        assert DS.status_tone(status) in {"ok", "warn", "bad", "info", "idle", "accent"}
    assert DS.status_tone("Waiting for client") == "idle"


def test_PRD_015_the_cards_count_from_the_status_column():
    today = date(2026, 9, 30)
    rows = []
    mid = 0

    def add(n, status, end):
        nonlocal mid
        for _ in range(n):
            mid += 1
            rows.append(ms(mid, f"m{mid}", "2026-09-01", end, status=status,
                           project="P%d" % (mid % 3)))

    add(13, "In Progress", "2026-12-01")
    add(44, "Scheduled", "2026-12-01")
    add(1, "On Hold", "2026-12-01")
    add(1, "", "2026-12-01")
    add(12, "In Progress", "2026-09-12")          # overdue
    add(13, "Completed", "2026-09-12")             # done, not late
    s = DS.summary(rows, today)
    assert s.in_progress == 13
    assert s.scheduled == 45                         # the blank one has not started
    assert s.paused == 1
    assert s.overdue == 12
    assert s.completed == 13
    assert s.total == len(rows)


def test_PRD_031_projects_counts_only_projects_with_open_work():
    rows = [ms(1, "a", "2026-09-01", "2026-12-01", project="AVTR3"),
            ms(2, "b", "2026-09-01", "2026-12-01", status="Completed", project="DONE1")]
    assert DS.summary(rows, date(2026, 9, 30)).projects == 1


# ------------------------------------------------------------------ calendar

def test_PRD_014_plus_one_working_day_from_friday_is_monday():
    friday = date(2026, 10, 2)
    assert friday.weekday() == 4
    assert WEEKDAYS.add_working_days(friday, 1) == date(2026, 10, 5)
    assert WEEKDAYS.add_working_days(date(2026, 10, 5), -1) == friday


def test_PRD_014_a_holiday_is_stepped_over():
    cal = DS.WorkCalendar((5, 6), {date(2026, 11, 9): "Diwali"})
    assert cal.add_working_days(date(2026, 11, 6), 1) == date(2026, 11, 10)
    assert cal.why_not_working(date(2026, 11, 9)) == "Diwali (holiday)"
    assert cal.why_not_working(date(2026, 11, 7)) == "Saturday"
    assert cal.working_days(date(2026, 11, 2), date(2026, 11, 13)) == 9


# ------------------------------------------------------------------ checks

def test_PRD_002_a_cross_project_dependency_is_refused():
    others = [ms(1, "Comp final", "2026-09-01", "2026-09-30", project="AVTR3")]
    new = ms(None, "Grade", "2026-10-05", "2026-10-10", project="RRR_REDUX", dep=1)
    problems = DS.check_milestone(new, others)
    assert problems and "another project" in str(problems[0])


def test_PRD_002_a_cycle_is_refused():
    a = ms(1, "A", "2026-09-01", "2026-09-10")
    b = ms(2, "B", "2026-09-11", "2026-09-20", dep=1)
    edited_a = ms(1, "A", "2026-09-21", "2026-09-30", dep=2)
    problems = DS.check_milestone(edited_a, [a, b])
    assert any("already waits on" in str(p) for p in problems)


def test_PRD_038_PRD_039_names_must_be_unique_per_project_and_short():
    others = [ms(1, "Grade & finishing", "2026-09-01", "2026-09-10")]
    dup = ms(None, "  grade &  FINISHING ", "2026-09-01", "2026-09-10")
    assert "already exists" in str(DS.check_milestone(dup, others)[0])
    other_project = ms(None, "Grade & finishing", "2026-09-01", "2026-09-10", project="KALKI2")
    assert not DS.check_milestone(other_project, others)
    long = ms(None, "X" * 500, "2026-09-01", "2026-09-10")
    assert "120 characters" in str(DS.check_milestone(long, [])[0])


def test_PRD_017_reversed_dates_and_missing_project_are_refused():
    problems = DS.check_milestone(ms(None, "x", "2026-09-10", "2026-09-01", project=""), [])
    fields = {p.field for p in problems}
    assert {"project", "end"} <= fields


def test_starting_before_the_dependency_ends_is_refused():
    parent = ms(1, "Roto & prep", "2026-09-01", "2026-10-10")
    child = ms(None, "Comp", "2026-10-10", "2026-10-20", dep=1)
    assert "before \"Roto & prep\" ends" in str(DS.check_milestone(child, [parent])[0])


def test_PRD_048_completing_before_the_dependency_warns():
    parent = ms(1, "Roto", "2026-09-01", "2026-09-10", status="In Progress")
    child = ms(2, "Comp", "2026-09-11", "2026-09-20", dep=1)
    assert "not complete" in DS.completion_warning(child, DS.index([parent, child]))
    parent.status = "Completed"
    assert DS.completion_warning(child, DS.index([parent, child])) == ""


def test_PRD_020_PRD_026_dependency_labels_carry_the_end_date_and_flag_broken_links():
    parent = ms(1, "Review", "2026-10-01", "2026-10-10")
    child = ms(2, "Grade", "2026-10-12", "2026-10-20", dep=1)
    gone = ms(3, "Orphan", "2026-10-12", "2026-10-20", dep=99)
    by_id = DS.index([parent, child, gone])
    assert DS.dependency_label(child, by_id) == ("Review (ends 10 Oct 2026)", "")
    assert DS.dependency_label(gone, by_id) == ("Missing (#99)", "warn")
    assert DS.dependency_label(parent, by_id) == ("", "")


# ------------------------------------------------------------------ shifting

def chain(n, start=date(2026, 1, 5)):
    out, cursor = [], start
    for i in range(1, n + 1):
        out.append(DS.Milestone(id=i, project_code="P", name=f"m{i}", start=cursor,
                                end=cursor + timedelta(days=1), status="Scheduled",
                                depends_on_id=i - 1 if i > 1 else None))
        cursor += timedelta(days=2)
    return out


def test_PRD_004_a_milestone_without_dates_is_skipped_not_a_crash():
    plan = DS.plan_shift([ms(1, "No dates", None, None)], 1, 3)
    assert plan.skipped == [(1, "No dates", "no dates")]
    assert not plan.moved


def test_PRD_005_old_day_first_dates_move_and_nothing_moved_says_so():
    legacy = DS.Milestone.from_row({"id": 15, "milestone": "Legacy", "status": "Scheduled",
                                    "start_date": "30/09/2026", "end_date": "15/10/2026"})
    plan = DS.plan_shift([legacy], 15, 3, unit=DS.CALENDAR_DAYS)
    assert plan.moved[0].new_start == date(2026, 10, 3)
    empty = DS.plan_shift([ms(1, "No dates", None, None)], 1, 3)
    assert empty.message().startswith("Nothing moved")


def test_PRD_006_a_5000_long_chain_plans_without_recursion():
    milestones = chain(5000)
    plan = DS.plan_shift(milestones, 1, 2, unit=DS.CALENDAR_DAYS, mode=DS.SAME)
    assert len(plan.moved) == 5000
    assert plan.moved[-1].new_start == milestones[-1].start + timedelta(days=2)


def test_a_cycle_in_old_data_does_not_loop():
    a = ms(1, "A", "2026-09-01", "2026-09-02", dep=2)
    b = ms(2, "B", "2026-09-03", "2026-09-04", dep=1)
    plan = DS.plan_shift([a, b], 1, 1, unit=DS.CALENDAR_DAYS, mode=DS.SAME)
    assert {m.id for m in plan.moved} == {1, 2}


def test_PRD_007_a_negative_shift_that_breaks_a_dependency_reports_it():
    parent = ms(1, "Roto & prep", "2026-09-01", "2026-10-10")
    child = ms(2, "Comp first pass", "2026-10-11", "2026-10-30", dep=1)
    plan = DS.plan_shift([parent, child], 2, -40, unit=DS.CALENDAR_DAYS)
    assert plan.conflicts and plan.conflicts[0][0] == 2
    assert "Roto & prep" in plan.conflicts[0][2]


def test_PRD_008_completed_milestones_stay_put():
    root = ms(1, "Plate turnover", "2026-09-01", "2026-09-05", status="Completed")
    child = ms(2, "Roto", "2026-09-06", "2026-09-10", dep=1)
    plan = DS.plan_shift([root, child], 1, 7, unit=DS.CALENDAR_DAYS, mode=DS.SAME)
    assert [m.id for m in plan.moved] == [2]
    assert plan.skipped[0][0] == 1
    forced = DS.plan_shift([root, child], 1, 7, unit=DS.CALENDAR_DAYS, mode=DS.SAME,
                           include_completed=True)
    assert {m.id for m in forced.moved} == {1, 2}


def test_PRD_052_push_mode_keeps_slack():
    root = ms(1, "Root", "2026-10-05", "2026-10-09")           # Mon-Fri
    tight = ms(2, "Tight", "2026-10-12", "2026-10-16", dep=1)   # starts the next Monday
    slack = ms(3, "Slack", "2026-10-26", "2026-10-30", dep=1)   # two weeks of slack
    plan = DS.plan_shift([root, tight, slack], 1, 3, calendar=WEEKDAYS)
    moved = {m.id: m for m in plan.moved}
    assert moved[1].new_end == date(2026, 10, 14)
    assert moved[2].new_start == date(2026, 10, 15)              # pushed to follow
    assert moved[2].new_end == date(2026, 10, 21)                # same 5 working days
    assert 3 not in moved                                        # slack absorbed it
    same = DS.plan_shift([root, tight, slack], 1, 3, calendar=WEEKDAYS, mode=DS.SAME)
    assert {m.id for m in same.moved} == {1, 2, 3}


def test_PRD_014_working_day_shift_skips_the_weekend():
    m = ms(1, "Weekend delivery", "2026-10-02", "2026-10-02")
    plan = DS.plan_shift([m], 1, 1, calendar=WEEKDAYS)
    assert plan.moved[0].new_start == date(2026, 10, 5)


def test_PRD_050_the_message_names_the_milestone_and_counts_in_the_singular():
    root = ms(1, "Weekend delivery", "2026-10-05", "2026-10-05")
    child = ms(2, "Grade", "2026-10-06", "2026-10-07", dep=1)
    plan = DS.plan_shift([root, child], 1, 1, calendar=WEEKDAYS, mode=DS.SAME)
    assert plan.message() == ('Moved "Weekend delivery" and 1 dependent milestone '
                              '1 working day later.')
    push = DS.plan_shift([root, child], 1, 1, calendar=WEEKDAYS)
    assert push.message() == ('Moved "Weekend delivery" 1 working day later; '
                              '1 dependent milestone moved to follow it.')


def test_PRD_051_changes_are_the_new_dates():
    m = ms(1, "A", "2026-10-05", "2026-10-09")
    plan = DS.plan_shift([m], 1, 2, calendar=WEEKDAYS)
    assert plan.changes() == [(1, date(2026, 10, 7), date(2026, 10, 13))]


# ------------------------------------------------------------------ timeline maths

def test_PRD_011_dates_map_to_x():
    scale = DS.TimeScale(date(2026, 10, 5), 10.0)
    assert scale.x(date(2026, 10, 5)) == 0
    assert scale.x(date(2026, 10, 8)) == 30
    assert scale.x_end(date(2026, 10, 8)) == 40
    assert scale.width(date(2026, 10, 5), date(2026, 10, 9)) == 50


def test_PRD_011_the_range_starts_on_a_monday_and_covers_today():
    first, last = DS.timeline_range([ms(1, "a", "2026-11-03", "2026-11-20")],
                                    today=date(2026, 10, 1), pad_days=7)
    assert first.weekday() == 0 and first <= date(2026, 10, 1)
    assert last >= date(2026, 11, 20)


def test_PRD_011_ruler_ticks_per_zoom():
    first, last = date(2026, 9, 28), date(2026, 11, 1)
    assert len(DS.ruler_ticks(first, last, DS.ZOOM_DAY)) == 35
    weeks = DS.ruler_ticks(first, last, DS.ZOOM_WEEK)
    assert all(t[0].weekday() == 0 for t in weeks) and len(weeks) == 5
    months = DS.ruler_ticks(first, last, DS.ZOOM_MONTH)
    assert [t[1] for t in months] == ["Oct 2026", "Nov 2026"]
    assert DS.month_bands(first, last)[0][2] == "Sep 2026"


def test_PRD_011_overlapping_bars_get_their_own_rows_and_arrows_route_around():
    rows = DS.pack_rows([(date(2026, 1, 1), date(2026, 1, 10)),
                         (date(2026, 1, 5), date(2026, 1, 8)),
                         (date(2026, 1, 11), date(2026, 1, 12))])
    assert rows == [0, 1, 0]
    straight = DS.arrow_route(100, 10, 140, 40)
    assert straight[0] == (100, 10) and straight[-1] == (140, 40) and len(straight) == 4
    back = DS.arrow_route(100, 10, 60, 40)
    assert len(back) == 6 and back[-1] == (60, 40)


# ------------------------------------------------------------------ people

def test_PRD_012_capacity_flags_leave_and_over_allocation():
    cal = WEEKDAYS
    tasks = [
        {"artist": "priya", "status": "WIP", "target_date": "2026-10-09", "bid_days": 5,
         "shot_name": "SH010", "department": "comp", "project_code": "AVTR3"},
        {"artist": "priya", "status": "WIP", "target_date": "2026-10-09", "bid_days": 3,
         "shot_name": "SH020", "department": "roto", "project_code": "AVTR3"},
        {"artist": "priya", "status": "Approved", "target_date": "2026-10-09", "bid_days": 3,
         "shot_name": "SH030", "department": "comp", "project_code": "AVTR3"},     # done
        {"artist": "rahul", "status": "WIP", "target_date": "", "bid_days": 2,
         "shot_name": "SH040", "department": "comp", "project_code": "AVTR3"},     # no date
    ]
    items, skipped = DS.task_items(tasks, cal)
    assert skipped == 1
    assert len(items) == 2
    comp = next(i for i in items if i.label == "SH010 comp")
    assert (comp.start, comp.end) == (date(2026, 10, 5), date(2026, 10, 9))

    owned = DS.milestone_items([ms(9, "Look dev", "2026-10-12", "2026-10-16", owner="priya",
                                   effort=2)])
    away = [DS.Away("priya", date(2026, 10, 8), date(2026, 10, 8), "Casual")]
    plan = DS.people_plan(items + owned, away, cal)
    assert plan.people == ["priya"]
    kinds = {c.kind for c in plan.conflicts}
    assert kinds == {"leave", "overload"}
    overload = next(c for c in plan.conflicts if c.kind == "overload")
    assert (overload.start, overload.end) == (date(2026, 10, 7), date(2026, 10, 9))
    leave = [c for c in plan.conflicts if c.kind == "leave"]
    assert len(leave) == 2 and all(c.start == date(2026, 10, 8) for c in leave)


def test_PRD_012_a_leave_overlap_is_found_for_the_dialog_warning():
    away = [DS.Away("Priya", date(2026, 10, 8), date(2026, 10, 9))]
    assert DS.leave_overlap("priya", date(2026, 10, 1), date(2026, 10, 8), away)
    assert not DS.leave_overlap("priya", date(2026, 10, 10), date(2026, 10, 20), away)


def test_PRD_002_a_parent_cannot_move_to_another_project_under_its_dependents():
    parent = ms(1, "Roto", "2026-09-01", "2026-09-10", project="AVTR3")
    child = ms(2, "Comp", "2026-09-11", "2026-09-20", dep=1, project="AVTR3")
    moved = ms(1, "Roto", "2026-09-01", "2026-09-10", project="RRR_REDUX")
    problems = DS.check_milestone(moved, [parent, child])
    assert any(p.field == "project" and "\"Comp\" in AVTR3 waits on" in str(p) for p in problems)
    assert not DS.check_milestone(ms(1, "Roto 2", "2026-09-01", "2026-09-10"), [parent, child])
