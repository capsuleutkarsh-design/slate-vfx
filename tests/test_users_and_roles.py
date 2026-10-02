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
