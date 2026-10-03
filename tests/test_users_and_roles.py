"""
Users & Roles on both databases (audit 2026-09):

    'Add New User' taking over an existing account          HR-120
    fields that could never be cleared                       HR-124
    Reports to that strands leave (artists, loops)           HR-125
    usernames with spaces, 'a', 120 characters               HR-128
    changes logged as 'System' / 'Updated roles'             HR-139
    no way to rename a role                                   HR-154
"""

import pytest

from slate.core.domain.user_manager import UserManager


@pytest.fixture(params=["sqlite", "postgres"])
def um(request):
    db = request.getfixturevalue("mock_db" if request.param == "sqlite" else "pg_db")
    from slate.core.domain import access
    access.reset_cache()
    manager = UserManager(db=db)
    manager.add_user("hr.kavya", "secret1", ["HR"], "Kavya Reddy", "HR")
    manager.add_user("sup.vikram", "pw1234", ["Supervisor"], "Vikram", "Comp")
    manager.add_user("sup.anjali", "pw1234", ["Supervisor"], "Anjali", "Comp", reports_to="sup.vikram")
    manager.add_user("aarav", "pw1234", ["Artist"], "Aarav", "Comp", reports_to="sup.anjali",
                     employment="Staff", joined_on="2019-04-01", location="Mumbai")
    yield manager
    access.reset_cache()


def test_adding_an_existing_username_is_refused_and_changes_nothing(um):
    before = um._row("hr.kavya")
    ok, message = um.create_user("HR.Kavya", "x", ["Developer"], "Hijacked")
    assert not ok and "already taken" in message
    after = um._row("hr.kavya")
    assert after["password_hash"] == before["password_hash"]
    assert after["display_name"] == "Kavya Reddy" and "HR" in after["roles"]


def test_a_new_user_is_created_with_the_username_rule(um):
    for bad in ("ravi kumar", "a", "x" * 120, "o'brien;--"):
        ok, message = um.create_user(bad, "pw1234", ["Artist"])
        assert not ok, bad
    ok, message = um.create_user("Ravi.Kumar", "pw1234", ["Artist"], "Ravi Kumar", "Comp",
                                 employment="staff")
    assert ok and message == "Added Ravi Kumar (ravi.kumar)"
    assert um._row("ravi.kumar")["employment"] == "Staff"


def test_fields_can_be_cleared_and_untouched_ones_stay(um):
    assert um.update_user("aarav", reports_to=um.CLEAR, employment=um.CLEAR,
                          joined_on=um.CLEAR)
    row = um._row("aarav")
    assert row["reports_to"] is None and row["employment"] is None and row["joined_on"] is None
    assert row["location"] == "Mumbai", "not mentioned, so left alone"


def test_reports_to_cannot_loop_or_point_at_yourself(um):
    assert um.would_create_cycle("sup.vikram", "sup.anjali")
    assert um.reports_to_problem("sup.vikram", "sup.anjali")
    assert um.reports_to_problem("aarav", "aarav")
    assert um.reports_to_problem("aarav", "sup.vikram") == ""
    with pytest.raises(PermissionError):
        um.update_user("sup.vikram", reports_to="aarav")


def test_only_approvers_are_offered_as_reports_to(um):
    approvers = um.approvers()
    assert "sup.vikram" in approvers and "hr.kavya" in approvers
    assert "aarav" not in approvers


def test_an_update_never_changes_the_password(um):
    with pytest.raises(ValueError):
        um.update_user("aarav", password="new-one")


def test_changes_are_audited_with_the_editor_and_the_change(um, monkeypatch):
    seen = []
    monkeypatch.setattr(um.audit, "log_user_change", lambda actor, target, what: seen.append((actor, target, what)))
    um.set_acting_user("hr.kavya")
    um.update_user("aarav", display_name="Aarav Sharma", location="Chennai")
    actor, target, what = seen[-1]
    assert actor == "hr.kavya" and target == "aarav"
    assert "display_name" in what and "Chennai" in what


def test_a_role_can_be_renamed_and_its_holders_follow(um):
    ok, _ = um.create_role("Comp Lead", ["Dashboard", "Attendance"]), None
    um.add_user("lead.farhan", "pw1234", ["Comp Lead"], "Farhan", "Comp")
    ok, message = um.rename_role("Comp Lead", "Compositing Lead")
    assert ok, message
    assert um.role_exists("Compositing Lead") and not um.role_exists("Comp Lead")
    assert "Compositing Lead" in um._row("lead.farhan")["roles"]
    assert um.role_permissions("Compositing Lead") == ["Dashboard", "Attendance"]
    assert not um.rename_role("Developer", "Dev")[0]
    assert not um.rename_role("Compositing Lead", "Supervisor")[0]


# ------------------------------------------------------------ round 2 (HR2-0xx)

def test_reset_password_changes_only_the_password(um, monkeypatch):
    """HR2-060: the picture survived, and the audit says what happened."""
    um.update_user("aarav", profile_pic_path="C:/pics/aarav.png")
    um.set_must_change_password("aarav", True)
    before = um._row("aarav")
    seen = []
    monkeypatch.setattr(um.audit, "log_user_change", lambda a, t, w: seen.append((a, t, w)))
    um.set_acting_user("hr.kavya")
    ok, message = um.set_password("aarav", " newpass9 ")
    assert ok, message
    after = um._row("aarav")
    assert after["profile_pic_path"] == "C:/pics/aarav.png"
    assert after["roles"] == before["roles"] and after["display_name"] == before["display_name"]
    assert after["password_hash"] != before["password_hash"] and not after["must_change_password"]
    assert um.authenticate("aarav", "newpass9")
    assert seen[-1] == ("hr.kavya", "aarav", "Password reset by hr.kavya")
    assert not um.set_password("aarav", "   ")[0]


def test_a_leaver_is_reactivated_only_with_the_last_day_cleared(um):
    """HR2-059: it said 'active again' and left them inactive."""
    um.update_user("aarav", last_day="2026-01-15")
    assert not um.is_active("aarav")
    ok, message = um.reactivate_user("aarav")
    assert not ok and "has passed" in message and not um.is_active("aarav")
    ok, message = um.reactivate_user("aarav", clear_last_day=True)
    assert ok and um.is_active("aarav") and um._row("aarav")["last_day"] is None


def test_reports_to_must_be_able_to_approve_everywhere(um):
    """HR2-062: create, update and import share the rule; HR2-064: no service accounts."""
    assert "cannot approve leave" in um.reports_to_problem("new.one", "aarav")
    ok, message = um.create_user("new.one", "pw1234", ["Artist"], reports_to="aarav")
    assert not ok and "cannot approve" in message
    with pytest.raises(PermissionError):
        um.update_user("sup.anjali", reports_to="aarav")
    assert "admin" not in um.approvers()
    # A manager who can no longer approve does not block saving anything else.
    um.add_user("old.boss", "pw1234", ["Artist"], "Old Boss", "Comp")
    um.update_user("aarav", reports_to=um.CLEAR)
    um._get_db().execute_update("UPDATE ut_users SET reports_to='old.boss' WHERE username='aarav'")
    assert um.update_user("aarav", display_name="Aarav S", reports_to="old.boss")


def test_the_last_developer_cannot_be_removed_and_self_lockout_is_spotted(um):
    """HR2-061."""
    for name in list(um.users_with_role("Developer")):
        if name != "admin":
            um.update_user(name, roles=["Artist"])
    with pytest.raises(PermissionError):
        um.update_user("admin", roles=["HR"])
    um.add_user("dev.two", "pw1234", ["Developer"], "Dev Two", "Dev")
    um.set_acting_user("dev.two")
    assert "lose" in um.lockout_warning("dev.two", ["Artist"])
    assert um.lockout_warning("dev.two", ["Developer", "Artist"]) == ""
    assert um.lockout_warning("aarav", ["Artist"]) == "", "only your own account"


def test_a_supervisors_team_is_listed_and_moved_before_deactivation(um):
    """HR2-063."""
    db = um._get_db()
    db.execute_update("INSERT INTO leave_requests (user_id, type, start_date, end_date, status) "
                      "VALUES ('aarav', 'Casual', '2026-12-01', '2026-12-02', 'Pending Supervisor')")
    items = um.open_items("sup.anjali")
    assert "1 person reports to them" in items
    assert "1 leave request waiting on their decision" in items
    assert not um.deactivate_user("sup.anjali")[0]
    assert um.open_items("sup.anjali", include_reports=False) == []

    moved, problems = um.move_reports("sup.anjali", "sup.vikram")
    assert (moved, problems) == (1, [])
    assert um._row("aarav")["reports_to"] == "sup.vikram"
    assert um.deactivate_user("sup.anjali")[0]

    moved, problems = um.move_reports("sup.vikram", "")
    assert problems == [] and um._row("aarav")["reports_to"] is None
    row = db.execute_query("SELECT status, route_note FROM leave_requests WHERE user_id='aarav'",
                           fetch="one")
    assert row["status"] == "Pending HR" and "left" in row["route_note"]


def test_account_status_says_leaving_left_and_system(um):
    """HR2-067 / HR2-074."""
    from datetime import date, timedelta
    users = um.get_all_users()
    assert um.account_status(dict(users["admin"], username="admin"))[0] == "System account"
    soon = (date.today() + timedelta(days=10)).isoformat()
    um.update_user("aarav", last_day=soon)
    text, tone = um.account_status(um.get_all_users()["aarav"])
    assert text.startswith("Leaving ") and tone == "warn"
