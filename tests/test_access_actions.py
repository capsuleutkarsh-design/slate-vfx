"""
One permission table, one answer.

Five widgets used to carry their own literal role lists, and they disagreed.
An account created under the role name "Human Resources" was an approver in
Leave (which knew that spelling), refused by Users & Roles (which only knew
"hr"), and a plain artist in Attendance (which knew neither). These tests pin
every workplace action to access.json so a studio adds a role name in one
file and every screen agrees.
"""

import pytest

from slate.core.domain import access
from slate.core.domain.workplace_access import manages_it, manages_leave


@pytest.fixture(autouse=True)
def fresh_access_cache():
    access.reset_cache()
    yield
    access.reset_cache()


WORKPLACE_ACTIONS = (
    "manage_leave", "manage_it", "manage_users",
    "view_team_attendance", "ingest_stock", "wipe_fleet_caches",
)


@pytest.mark.parametrize("action", WORKPLACE_ACTIONS)
def test_every_workplace_action_is_declared(action):
    """An action nobody declared is an action nobody can be granted."""
    assert access.roles_for(action), "%s has no roles in access.json" % action


@pytest.mark.parametrize("spelling", ["HR", "hr", "Human Resources", "human resources"])
def test_both_spellings_of_hr_are_hr_everywhere(spelling):
    """
    The bug: "Human Resources" passed the leave gate and failed the users
    gate, because each had typed its own list.
    """
    assert manages_leave([spelling])
    assert access.can([spelling], "manage_users")
    assert access.can([spelling], "view_team_attendance")


def test_hr_can_see_the_team_attendance_grid():
    """
    The attendance tab's own list was supervisor, developer, admin. HR keep
    the attendance record and could not open it.
    """
    assert access.can(["HR"], "view_team_attendance")
    assert not access.can(["Artist"], "view_team_attendance")


def test_it_support_works_the_desk_but_not_the_leave_queue():
    assert manages_it(["IT Support"])
    assert not manages_leave(["IT Support"])
    assert not manages_it(["Artist"])


def test_permission_key_still_wins_over_role_names():
    """A studio that grants the HRMS permission to an unusual role keeps it."""
    assert manages_leave(["Producer"], allowed_tabs=["HRMS"])
    assert manages_it(["Producer"], allowed_tabs=["IT"])
    assert manages_leave(["Producer"], allowed_tabs=["ALL"])


def test_stock_ingest_is_a_table_entry_not_a_hostname():
    """
    Ingest used to fall back to "is this machine called CAPINT". A role
    decides now, and an artist is not one of them.
    """
    assert access.can(["Lead"], "ingest_stock")
    assert access.can(["Supervisor"], "ingest_stock")
    assert not access.can(["Artist"], "ingest_stock")
    assert not access.can([], "ingest_stock")


def test_unknown_action_is_refused_not_granted():
    assert not access.can(["admin"], "launch_the_missiles")


def test_can_accepts_a_single_role_string():
    assert access.can("admin", "manage_users")
    assert not access.can("artist", "manage_users")
