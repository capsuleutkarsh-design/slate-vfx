"""
What a workstation is told when it cannot reach the database.

The advice was the same five lines whatever went wrong, and it opened with
"verify the database server is running" and "ping the host". For the failure a
studio actually hit - the server running, reachable, and refusing the address -
every one of those five checks passes. The person doing them concludes the
software is broken and goes looking in the wrong place, which is what happened.
"""

from slate.core.infra.postgres_manager import what_to_do


ARGS = ("10.100.104.82", 5440, "ut_vfx", "ut_vfx_app")


def test_a_refused_address_does_not_send_anybody_to_ping_the_server():
    advice = what_to_do(
        Exception('FATAL:  no pg_hba.conf entry for host "10.100.104.82", '
                  'user "ut_vfx_app", database "ut_vfx", no encryption'), *ARGS)

    assert "running and reachable" in advice
    assert "ping" not in advice.lower(), \
        "the host pings; saying so wastes the first thing somebody tries"
    assert "Slate Server" in advice and "Settings" in advice


def test_a_refused_password_says_the_two_ends_disagree():
    advice = what_to_do(
        Exception('FATAL:  password authentication failed for user "ut_vfx_app"'),
        *ARGS)

    assert "refused the password" in advice
    assert "Slate Server" in advice
    assert "Reinstalling only this workstation" in advice


def test_a_missing_account_points_at_the_server_that_never_made_it():
    advice = what_to_do(Exception('FATAL:  role "ut_vfx_app" does not exist'), *ARGS)
    assert "ut_vfx_app" in advice
    assert "creates this account on start" in advice


def test_a_missing_database_warns_about_the_empty_one_next_door():
    advice = what_to_do(
        Exception('FATAL:  database "ut_vfx" does not exist'), *ARGS)
    assert "Database Name" in advice
    assert "creates an empty database" in advice


def test_an_unreachable_server_still_gets_the_network_checks():
    advice = what_to_do(
        Exception("connection to server at \"10.100.104.82\", port 5440 failed: "
                  "Connection refused"), *ARGS)

    assert "ping 10.100.104.82" in advice
    assert "firewall allows port 5440" in advice
