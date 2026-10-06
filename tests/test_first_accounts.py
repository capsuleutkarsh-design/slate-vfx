"""
The first accounts on a new studio (handbook fixes C1, C3).

Never lock out: a new studio must always have a way in, so each test also
signs in.
"""
from slate.core.domain.user_manager import UserManager


def test_the_seeded_admin_must_choose_a_new_password(mock_db):
    """C1: admin/admin123 was a permanent known password."""
    users = UserManager(db=mock_db)
    signed_in = users.authenticate("admin", "admin123")
    assert signed_in and signed_in["must_change_password"] is True    # the forced-change window
    assert users.change_own_password("admin", "admin123", "Studio-own-1")[0]
    assert users.authenticate("admin", "Studio-own-1")["must_change_password"] is False

    # Brought back after being removed: the same.
    mock_db.execute_update("DELETE FROM ut_users WHERE username='admin'")
    UserManager(db=mock_db)
    assert users.authenticate("admin", "admin123")["must_change_password"] is True


def test_starting_roles_when_recover_slate_made_the_first_admin(pg_db):
    """C3: an administrator made in Recover Slate before any workstation started."""
    from slate_server.core.recovery import actions
    from tests.conftest import POSTGRES_TEST_DB, _pg_connect
    # pg_db is emptied: nothing seeded yet. A role the studio already has:
    pg_db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES ('Tester', '[\"Settings\"]')")
    conn = _pg_connect(POSTGRES_TEST_DB)
    try:
        actions.restore_admin(conn, "boss", "Recovered-pass1")
    finally:
        conn.close()

    users = UserManager(db=pg_db)                                   # the first workstation start
    roles = {r.lower() for r in users.get_available_roles()}
    for role in ("developer", "supervisor", "coordinator", "lead", "artist", "tester", "compositor"):
        assert role in roles, role
    assert users.role_permissions("Tester") == ["Settings"], "an existing role is never overwritten"
    assert users.authenticate("boss", "Recovered-pass1")["must_change_password"] is True
