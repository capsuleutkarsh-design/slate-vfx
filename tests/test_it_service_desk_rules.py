"""
The service desk's clocks and transitions (IT area).

The SLA used to run on wall-clock hours while the requester was promised
"business days"; At risk used the response window for both clocks; a parked
ticket was counted as breached; setting Waiting twice wiped the banked wait;
reopening kept the old resolved_at; and a requester's reply left the ticket
parked. These pin the rules that replaced all of that.
"""

from datetime import date, datetime, time

import pytest

from slate.core.domain import service_desk as sd

CAL = sd.BusinessCalendar()               # Mon-Sat 10:00-19:00
SAT = datetime(2026, 9, 19)               # a Saturday
MON = datetime(2026, 9, 21)               # the Monday after


def _t(**fields):
    base = {"status": "Open", "priority": "P3", "created_at": MON.replace(hour=10),
            "first_response_at": None}
    base.update(fields)
    return base


# ----------------------------------------------------------------- calendar

def test_working_hours_skip_sunday_and_nights():
    assert CAL.working_hours_between(SAT.replace(hour=18), MON.replace(hour=10)) == pytest.approx(1.0)
    assert CAL.working_hours_between(MON.replace(hour=8), MON.replace(hour=21)) == pytest.approx(9.0)


def test_a_studio_holiday_is_not_a_working_day():
    cal = sd.calendar_from({"start": "10:00", "end": "19:00", "days": [0, 1, 2, 3, 4, 5]},
                           [MON.date()])
    assert cal.working_hours_between(SAT.replace(hour=18), MON.replace(hour=19)) == pytest.approx(1.0)


def test_adding_working_hours_rolls_over_to_the_next_working_day():
    due = CAL.add_working_hours(SAT.replace(hour=18), 2)
    assert due == MON.replace(hour=11)


def test_the_studio_hours_are_read_from_studio_settings_shape():
    cal = sd.calendar_from({"start": "09:30", "end": "18:30", "days": [0, 1, 2, 3, 4]})
    assert cal.start == time(9, 30) and cal.day_hours == 9.0
    assert not cal.is_working_day(SAT.date())
    assert cal.describe() == "Mon–Fri, 09:30–18:30"


# ---------------------------------------------------------------- the clock

def test_a_p3_raised_saturday_evening_is_not_breached_on_monday_morning():
    """IT-082: nights and Sundays are not time IT had."""
    ticket = _t(created_at=SAT.replace(hour=18))
    state = sd.sla_state(ticket, MON.replace(hour=10, minute=30), CAL)
    assert state["state"] != "breached"
    assert state["hours_left"] == pytest.approx(0.5)


def test_a_p1_runs_around_the_clock():
    """A studio-wide outage at 23:00 is still an outage."""
    ticket = _t(priority="P1", created_at=SAT.replace(hour=23))
    state = sd.sla_state(ticket, datetime(2026, 9, 20, 0, 0), CAL)    # Sunday midnight
    assert state["state"] == "breached"        # 15 minutes, 60 have passed
    assert state["around_the_clock"]


def test_the_promise_is_worded_from_the_same_calendar():
    p3 = sd.describe_promise("P3", CAL)
    assert "3 working days" in p3 and "Mon–Sat, 10:00–19:00" in p3
    assert "day or night" in sd.describe_promise("P1", CAL)
    # 3 working days is what the clock measures: 27 working hours.
    assert sd.budget_hours("P3", "resolution", CAL) == pytest.approx(27.0)


def test_at_risk_uses_the_clock_being_measured():
    """IT-083: a P3 fix with 4 of 27 working hours left is at risk."""
    ticket = _t(first_response_at=MON.replace(hour=10, minute=5))
    now = CAL.add_working_hours(MON.replace(hour=10), 23)
    assert sd.sla_state(ticket, now, CAL)["state"] == "at risk"


def test_a_parked_ticket_is_paused_not_breached():
    """IT-087."""
    ticket = _t(status="Waiting on You", created_at=datetime(2026, 8, 1, 10),
                waiting_since=datetime(2026, 8, 1, 11), first_response_at=datetime(2026, 8, 1, 10, 30))
    state = sd.sla_state(ticket, MON, CAL)
    assert state["state"] == "paused"
    assert sd.sla_text(state) == "Paused - waiting on requester"


def test_sla_text_reads_like_a_sentence():
    """IT-114: not '699.5h over (response)'."""
    assert sd.sla_text({"state": "breached", "hours_left": -270.0, "against": "response",
                        "around_the_clock": False}) == "30 d overdue - no reply yet"
    assert sd.sla_text({"state": "met", "hours_left": 3.2, "against": "resolution",
                        "around_the_clock": False}) == "3 h left to fix"
    assert sd.sla_text({"state": "at risk", "hours_left": 0.25, "against": "response",
                        "around_the_clock": True}) == "15 min left to reply"


# ---------------------------------------------------------- the vocabulary

def test_unknown_statuses_are_read_as_open():
    """IT-088: 'On Hold' was in no filter while its clock kept running."""
    assert sd.normalise_status("On Hold") == "Open"
    assert sd.is_open("On Hold")


@pytest.mark.parametrize("stored, code", [("Medium", "P3"), ("high", "P2"), ("Critical", "P1"),
                                          ("low", "P4"), ("p2", "P2"), (None, "P3")])
def test_legacy_priority_words_map_to_codes(stored, code):
    """IT-089."""
    assert sd.normalise_priority(stored) == code
    assert sd.priority_rank(stored) == sd.PRIORITIES.index(code)


def test_summary_of_an_empty_or_blank_first_line():
    """IT-077."""
    assert sd.summary_of("") == "Untitled ticket"
    assert sd.summary_of("\n\n  Nuke crashes\nmore") == "Nuke crashes"
    assert sd.body_of("\nNuke crashes\nmore\n") == "more"


def test_waiting_reads_from_each_side():
    """IT-095."""
    assert sd.display_status("Waiting on You", "it") == "Waiting on requester"
    assert sd.display_status("waiting on you") == "Waiting on you"


# -------------------------------------------------------------- transitions

def test_setting_waiting_twice_keeps_the_banked_time():
    """IT-080."""
    ticket = _t(status="Waiting on You", waiting_since=SAT.replace(hour=13), waiting_seconds=3600)
    assert sd.plan_status_change(ticket, "Waiting on You", MON, CAL) is None


def test_unparking_banks_the_wait():
    ticket = _t(status="Waiting on You", waiting_since=SAT.replace(hour=17), waiting_seconds=3600)
    changes = sd.plan_status_change(ticket, "In Progress", MON.replace(hour=11), CAL)
    assert changes["waiting_since"] is None
    assert changes["waiting_seconds"] == 3600 + 3 * 3600      # 2 h Saturday + 1 h Monday


def test_reopening_clears_the_old_resolution():
    """IT-085."""
    ticket = _t(status="Resolved", resolved_at=SAT, resolution_met=True)
    changes = sd.plan_status_change(ticket, "Open", MON, CAL)
    assert changes["resolved_at"] is None and changes["resolution_met"] is None


def test_resolving_records_whether_the_promises_were_kept():
    """IT-111."""
    ticket = _t(status="In Progress", first_response_at=MON.replace(hour=10, minute=30))
    changes = sd.plan_status_change(ticket, "Resolved", MON.replace(hour=15), CAL)
    assert changes["resolved_at"] == MON.replace(hour=15)
    assert changes["response_met"] is True
    assert changes["resolution_met"] is True
    assert changes["resolution_hours"] == pytest.approx(5.0)


def test_a_requester_reply_takes_the_ticket_off_waiting():
    """IT-079."""
    ticket = _t(status="Waiting on You", submitted_by="ravi", waiting_since=MON.replace(hour=10))
    changes, event = sd.plan_after_reply(ticket, "Ravi", MON.replace(hour=12), CAL)
    assert changes["status"] == "In Progress"
    assert changes["waiting_seconds"] == 2 * 3600 and changes["waiting_since"] is None


def test_a_requesters_thank_you_does_not_reopen_a_resolved_ticket():
    """IT2-002: replying leaves it resolved; Still broken - reopen is how it goes back."""
    for status in ("Resolved", "Closed"):
        ticket = _t(status=status, submitted_by="ravi", resolved_at=SAT)
        assert sd.plan_after_reply(ticket, "ravi", MON, CAL) == ({}, "")


def test_an_it_reply_on_an_owned_ticket_only_records_the_response():
    ticket = _t(submitted_by="ravi", assigned_to="it.joe", status="In Progress")
    changes, _ = sd.plan_after_reply(ticket, "it.sana", MON.replace(hour=11), CAL)
    assert changes == {"first_response_at": MON.replace(hour=11)}


def test_handling_records_the_response_and_the_owner_once():
    """IT2-014."""
    now = MON.replace(hour=11)
    assert sd.plan_handled(_t(), "it.sana", now) == {"first_response_at": now, "assigned_to": "it.sana"}
    assert sd.plan_handled(_t(first_response_at=MON, assigned_to="it.joe"), "it.sana", now) == {}


def test_durations_never_round_up_to_a_unit_that_is_not_there():
    """IT2-080."""
    assert sd.format_duration(0.999) == "59 min"
    assert sd.format_duration(8.6, around_the_clock=False, calendar=CAL) == "8 h"
    assert sd.format_duration(17.6, around_the_clock=False, calendar=CAL) == "1 d 8 h"
    assert sd.format_duration(23.6) == "23 h"
    assert sd.format_duration(24.0) == "1 d"
    assert sd.format_duration(0.01) == "1 min"


def test_an_escalated_ticket_is_measured_from_the_escalation():
    """IT2-013: sla_from restarts the promise."""
    ticket = _t(priority="P1", created_at=MON.replace(hour=10) - (MON - SAT),
                sla_from=MON.replace(hour=10))
    state = sd.sla_state(ticket, MON.replace(hour=10, minute=5), CAL)
    assert state["state"] != "breached" and state["hours_left"] == pytest.approx(0.25 - 5 / 60)


def test_a_day_ending_before_it_starts_is_logged_not_silent(caplog):
    """IT2-079."""
    with caplog.at_level("WARNING"):
        cal = sd.calendar_from({"start": "22:00", "end": "06:00", "days": [0, 1, 2, 3, 4]})
    assert cal.start == sd.DEFAULT_CALENDAR.start
    assert "end before they start" in caplog.text


def test_the_working_days_are_the_studio_policys_weekly_offs(monkeypatch):
    """The working week is kept once: the studio policy's weekly offs."""
    import slate.core.infra.studio_settings as settings
    import slate.core.infra.studio_policy as policy

    class _NoHolidays:
        def execute_query(self, *_a, **_k):
            return []

    monkeypatch.setattr(settings, "get_setting",
                        lambda key, default=None, db=None: {"start": "09:00", "end": "18:00",
                                                            "days": [0, 1, 2, 3, 4, 5]})
    monkeypatch.setattr(policy, "studio_rules", lambda db=None: {"weekly_offs": [5, 6]})
    cal = sd.studio_calendar(_NoHolidays())
    assert cal.days == frozenset({0, 1, 2, 3, 4})
    assert cal.describe() == "Mon–Fri, 09:00–18:00"


def test_an_it_reply_picks_up_an_unassigned_ticket():
    """IT-097."""
    ticket = _t(submitted_by="ravi")
    changes, _ = sd.plan_after_reply(ticket, "it.sana", MON.replace(hour=11), CAL)
    assert changes == {"first_response_at": MON.replace(hour=11), "assigned_to": "it.sana",
                       "status": "In Progress"}


def test_monthly_report_counts_kept_promises():
    tickets = [
        {"priority": "P2", "resolved_at": datetime(2026, 9, 3), "response_met": 1,
         "resolution_met": 1, "resolution_hours": 4},
        {"priority": "P2", "resolved_at": datetime(2026, 9, 9), "response_met": True,
         "resolution_met": False, "resolution_hours": 12},
        {"priority": "P3", "resolved_at": datetime(2026, 8, 30), "resolution_met": 1},
    ]
    report = sd.sla_report(tickets, 2026, 9)
    assert report == [{"priority": "P2", "count": 2, "response_pct": 100,
                       "resolution_pct": 50, "median_hours": 8.0}]
    # IT2-017: a span of months.
    assert [line["count"] for line in sd.sla_report(tickets, 2026, 9, months=12)] == [2, 1]


def test_a_ticket_resolved_before_outcomes_were_stored_is_measured_not_missed():
    """IT2-016."""
    legacy = {"priority": "P3", "created_at": MON.replace(hour=10),
              "first_response_at": MON.replace(hour=11), "resolved_at": MON.replace(hour=15)}
    [line] = sd.sla_report([legacy], 2026, 9, calendar=CAL)
    assert line["response_pct"] == 100 and line["resolution_pct"] == 100


def test_the_help_pages_describe_what_the_screens_do():
    """Help for the IT screens matches the features (IT-070, IT-073, IT-094, IT-104, IT-139)."""
    import json, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pages = json.load(open(os.path.join(root, "slate", "core", "help_content.json"), encoding="utf-8"))
    support = pages["it_support"]["content"]
    assert "Monday to Saturday" in support and "around the clock" in support
    assert "Mark responded" not in support and "Unresolved" in support
    licences = pages["licences"]["content"]
    assert "Short of seats" not in licences and "Never used at once" not in licences
    assert "Spare seats" in licences and "rlmstat" in licences
    assert "Retired" in pages["hardware"]["content"]
    assert "does not install" in pages["deployment"]["content"]
