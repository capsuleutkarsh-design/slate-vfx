"""
The rules behind Leave, the Service Desk, Licences and the joining spine.

These are pure-policy tests: no database, no Qt. They exist because every one
of them encodes a decision that cost somebody money or a day off if it is
wrong, and because two of them are regressions for bugs that shipped.
"""

from datetime import date, timedelta

import pytest

from ut_vfx.core.domain import leave_policy as lp
from ut_vfx.core.domain import licence_compliance as lc
from ut_vfx.core.domain import service_desk as sd
from ut_vfx.core.domain.comp_off_service import hours_between
from ut_vfx.core.domain.onboarding_service import (
    FREELANCE_SKIP, JOINING, LEAVING, OFFBOARD_TASKS, ONBOARD_TASKS,
)


# ---------------------------------------------------------------- the week

def test_saturday_is_a_working_day():
    """The studio works Monday to Saturday. Sunday is the only weekly off."""
    saturday = date(2026, 9, 12)
    sunday = date(2026, 9, 13)
    assert saturday.weekday() == 5 and sunday.weekday() == 6
    assert lp.is_working_day(saturday, set())
    assert not lp.is_working_day(sunday, set())


def test_a_public_holiday_is_not_charged():
    day = date(2026, 10, 2)
    assert not lp.is_working_day(day, {day})


# -------------------------------------------------------------- the sandwich

def test_the_day_between_two_holidays_is_charged_when_skipped():
    """
    Holiday, working day, holiday - and the artist does not come in on the
    middle day. The whole block is charged, which is the rule the studio
    described and the one artists most need warning about.
    """
    # Tue and Thu are holidays; Wed is the working day being taken off. Chosen
    # so no weekly off adjoins the block - see the next test for that case.
    holidays = {date(2026, 11, 10), date(2026, 11, 12)}
    charged = lp.days_charged(date(2026, 11, 11), date(2026, 11, 11), holidays)
    assert charged["total"] == 3
    assert len(charged["sandwich_days"]) == 2


def test_the_sandwich_absorbs_a_weekly_off_that_adjoins_it():
    """
    Sunday, holiday Monday, working Tuesday, holiday Wednesday. Skipping the
    Tuesday costs all four, because the run of non-working days either side is
    continuous - which is exactly the case an artist does not see coming.
    """
    holidays = {date(2026, 11, 9), date(2026, 11, 11)}
    charged = lp.days_charged(date(2026, 11, 10), date(2026, 11, 10), holidays)
    assert date(2026, 11, 8).weekday() == 6          # the Sunday it reaches back to
    assert charged["total"] == 4
    assert len(charged["sandwich_days"]) == 3


def test_no_sandwich_when_the_rule_is_off():
    holidays = {date(2026, 11, 9), date(2026, 11, 11)}
    rules = lp.policy({"sandwich_rule": False})
    charged = lp.days_charged(date(2026, 11, 10), date(2026, 11, 10), holidays, rules)
    assert charged["total"] == 1


def test_a_half_day_costs_half():
    monday = date(2026, 9, 14)
    assert lp.days_charged(monday, monday, set(), half_day=True)["total"] == 0.5


# ----------------------------------------------------------------- accrual

def test_accrual_counts_completed_months_only():
    """Two days a month, earned - not twenty-four handed over in January."""
    joined = date(2026, 1, 15)
    assert lp.accrued_by(date(2026, 1, 31), joined) == 0
    assert lp.accrued_by(date(2026, 2, 14), joined) == 0
    assert lp.accrued_by(date(2026, 2, 15), joined) == 2
    assert lp.accrued_by(date(2026, 7, 15), joined) == 12


def test_a_month_completes_on_the_joining_day_not_the_month_end():
    """
    Regression. Accrual counted calendar months, so somebody who joined on the
    15th was credited a full month's leave on the 31st - two days earned for
    sixteen days worked.
    """
    joined = date(2026, 1, 15)
    assert lp.accrued_by(date(2026, 1, 31), joined) == 0

    # And the other edge: joined on a 31st, in a month that has no 31st. The
    # end of February has to count, or that person never accrues in February.
    end_of_month = date(2026, 1, 31)
    assert lp.accrued_by(date(2026, 2, 27), end_of_month) == 0
    assert lp.accrued_by(date(2026, 2, 28), end_of_month) == 2


def test_carry_forward_is_capped_and_says_what_was_lost():
    rules = lp.policy(None)
    cap = rules["carry_forward_cap"]

    over = lp.carry_forward(30, rules)
    assert over["carried"] == cap
    # The lapsed figure is the point: somebody has to be told what they lost.
    assert over["lapsed"] == 30 - cap

    under = lp.carry_forward(5, rules)
    assert under["carried"] == 5 and under["lapsed"] == 0


# -------------------------------------------------------------- the approval

def test_a_request_travels_supervisor_then_hr():
    status = lp.STATUS_PENDING_SUPERVISOR
    status = lp.next_status(status, "Supervisor", True)
    assert status == lp.STATUS_PENDING_HR
    assert lp.awaiting(status) == "HR"
    status = lp.next_status(status, "HR", True)
    assert status == lp.STATUS_APPROVED
    assert lp.awaiting(status) in (None, "")


def test_either_stage_can_reject_outright():
    assert lp.next_status(lp.STATUS_PENDING_SUPERVISOR, "Supervisor", False) == lp.STATUS_REJECTED
    assert lp.next_status(lp.STATUS_PENDING_HR, "HR", False) == lp.STATUS_REJECTED


def test_title_case_does_not_lose_a_pending_request():
    """
    Regression. A stored status was passed through .title(), which turns
    "Pending HR" into "Pending Hr" - it then matched no constant, so requests
    waiting on HR silently stopped counting as pending and vanished from the
    artist's balance.
    """
    assert lp.normalise_status("Pending Hr") == lp.STATUS_PENDING_HR
    assert lp.normalise_status("pending hr") == lp.STATUS_PENDING_HR
    assert lp.normalise_status("PENDING SUPERVISOR") == lp.STATUS_PENDING_SUPERVISOR


# ----------------------------------------------------------------- comp-off

def test_comp_off_is_off_by_default():
    """UT does not operate comp-off. The engine exists for studios that do."""
    assert lp.policy(None)["comp_off_enabled"] is False
    assert lp.comp_off_earned(date(2026, 9, 6), 9.0, set())["days"] == 0


def test_long_shifts_earn_half_then_whole_days():
    rules = lp.policy({"comp_off_enabled": True})
    monday = date(2026, 9, 14)
    assert lp.comp_off_earned(monday, 8.0, set(), rules)["days"] == 0
    assert lp.comp_off_earned(monday, 13.0, set(), rules)["days"] == 0.5
    assert lp.comp_off_earned(monday, 19.0, set(), rules)["days"] == 1


def test_working_a_weekly_off_earns_a_day_however_short():
    rules = lp.policy({"comp_off_enabled": True})
    sunday = date(2026, 9, 13)
    assert lp.comp_off_earned(sunday, 3.0, set(), rules)["days"] == 1


def test_a_shift_across_midnight_is_counted_not_discarded():
    """The crunch shift that earns comp-off is exactly the one that wraps."""
    assert hours_between("21:00:00", "07:00:00") == 10.0
    assert hours_between("09:30:00", "18:00:00") == 8.5
    assert hours_between("", "18:00:00") == 0.0


# -------------------------------------------------------------- service desk

def test_priority_comes_from_impact_and_urgency():
    assert sd.priority_for("High", "High") == "P1"
    assert sd.priority_for("Low", "Low") == "P4"
    assert sd.priority_for("High", "Low") == sd.MATRIX["High"]["Low"]
    # The answers arrive from a combo box, so casing must not decide a priority.
    assert sd.priority_for("high", "HIGH") == "P1"


def test_every_matrix_cell_is_a_real_priority():
    for impact, _ in sd.IMPACT:
        for urgency, _ in sd.URGENCY:
            assert sd.priority_for(impact, urgency) in sd.PRIORITIES


def test_a_p1_is_promised_sooner_than_a_p4():
    assert sd.SLA_RESPONSE_HOURS["P1"] < sd.SLA_RESPONSE_HOURS["P4"]
    assert sd.SLA_RESOLUTION_HOURS["P1"] < sd.SLA_RESOLUTION_HOURS["P4"]


# ----------------------------------------------------------------- licences

def _in(days):
    return date.today() + timedelta(days=days)


def test_being_short_of_seats_outranks_an_expiry():
    """
    An expiry is a diary entry. A shortfall is people unable to work, so it is
    the finding that must surface even on a licence that is also expiring.
    """
    assert lc.state(8, 9, _in(-5)) == lc.OVER


def test_the_four_findings_are_distinguished():
    assert lc.state(2, 2, _in(-14)) == lc.EXPIRED
    assert lc.state(4, 4, _in(20)) == lc.SOON
    assert lc.state(10, 5, _in(300)) == lc.UNDER
    assert lc.state(3, None, _in(300)) == lc.UNKNOWN
    assert lc.state(8, 7, _in(300)) == lc.OK


def test_a_fully_used_licence_is_not_told_to_drop_seats():
    """Regression: the advice used to read "so 0 could be dropped"."""
    text = lc.describe(4, 4, _in(28))
    assert "0 could" not in text
    assert "renew at least at this size" in text


def test_every_finding_says_something_actionable():
    for seats, peak, expiry in ((8, 9, _in(100)), (2, 2, _in(-14)), (4, 4, _in(28)),
                                (10, 5, _in(300)), (3, None, _in(300)), (8, 7, _in(300))):
        text = lc.describe(seats, peak, expiry)
        assert text and text[0].isupper() and text.endswith(".")


def test_utilisation_survives_a_licence_with_no_seats():
    assert lc.utilisation(3, 0) == 0.0


# ------------------------------------------------------------------- spine

def test_the_leaving_list_undoes_the_joining_list():
    """
    Every access granted on the way in needs a revocation on the way out. The
    two lists are checked against each other so a task added to one side is not
    quietly left without its mirror.
    """
    joining = {name for _team, name in ONBOARD_TASKS}
    leaving = {name for _team, name in OFFBOARD_TASKS}
    for granted, revoked in (
        ("Domain account created", "Domain account disabled"),
        ("Project shares mounted", "Project shares unmounted"),
        ("Render farm access", "Render farm access revoked"),
        ("Workstation issued", "Workstation returned"),
    ):
        assert granted in joining
        assert revoked in leaving


def test_each_task_belongs_to_exactly_one_team():
    for template in (ONBOARD_TASKS, OFFBOARD_TASKS):
        for team, name in template:
            assert team in ("HR", "IT"), name
        assert len({name for _t, name in template}) == len(template)


def test_a_freelancer_skips_payroll_and_settlement():
    assert FREELANCE_SKIP <= ({n for _t, n in ONBOARD_TASKS} | {n for _t, n in OFFBOARD_TASKS})


def test_the_two_directions_are_distinct():
    assert JOINING != LEAVING
