"""
A write says what happened, and says it the same way on both databases.

Before: execute_query(..., fetch=False) returned None on PostgreSQL whether the
statement worked or not, so the success branch after "Save" never ran (no
message, no refresh, people saved again and made duplicates). A refused
statement was logged and turned into None / False on PostgreSQL but raised on
SQLite. An UPDATE that matched nothing reported success, so "Punch Out"
without a punch-in said "Logged OUT" and saved nothing.

Every test here runs on SQLite and on a real PostgreSQL (skipped when none is
reachable), because the whole point is that they agree.
"""

import pytest

from slate.core.infra.db_results import (
    DatabaseUnavailableError, DatabaseWriteError, NoRowsError, SqlResult, WriteResult,
)
from slate.core.infra.transaction import atomic


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    if request.param == "sqlite":
        yield request.getfixturevalue("mock_db")
    else:
        yield request.getfixturevalue("pg_db")


def _deployment(db, name="Nuke 15"):
    return db.write(
        "INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
        "VALUES (%s, %s, %s, %s) RETURNING id", (name, "WS-01", "it.sana", "Pending"))


# ------------------------------------------------------------ WriteResult


def test_a_write_result_is_truthy_exactly_when_accepted():
    assert WriteResult(True, rows=0)
    assert not WriteResult(False, error="nope")
    assert WriteResult(True, rows=0) == True  # noqa: E712 - the old boolean contract
    assert WriteResult(False) == False  # noqa: E712


def test_changed_means_accepted_and_touched_a_row():
    assert WriteResult(True, rows=2).changed
    assert not WriteResult(True, rows=0).changed
    assert not WriteResult(False, rows=0).changed


def test_raise_for_error_carries_the_reason():
    with pytest.raises(DatabaseWriteError, match="too long"):
        WriteResult(False, error="value too long", kind="too_long").raise_for_error()


# ------------------------------------------------------------ both backends


def test_an_insert_reports_its_row_and_new_id(db):
    result = _deployment(db)
    assert result.ok and result.rows == 1
    assert int(result.last_id) >= 1


def test_the_old_fetch_false_call_now_reports_success(db):
    """The PRD-003 / PRD-077 / IT-135 shape: 'if execute_query(..., fetch=False):'."""
    result = db.execute_query(
        "INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
        "VALUES (%s, %s, %s, %s)", ("Houdini", "WS-02", "it.ravi", "Pending"), fetch=False)
    assert isinstance(result, WriteResult)
    assert result
    assert result.rows == 1
    assert db.execute_query("SELECT 1 FROM it_deployments", fetch="none")


def test_an_update_that_matches_nothing_is_accepted_but_changed_nothing(db):
    """HR-005: punch-out with no punch-in must be tellable from a real update."""
    result = db.execute_update(
        "UPDATE it_deployments SET status = %s WHERE id = %s", ("Done", 999999))
    assert result.ok
    assert result.rows == 0
    assert not result.changed


def test_a_duplicate_is_refused_and_says_so(db):
    db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                      ("Compositor", "[]"))
    again = db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                              ("Compositor", "[]"))
    assert not again
    assert again.kind == "duplicate"
    assert again.error


def test_on_conflict_do_nothing_reports_zero_rows(db):
    """HR-070: a holiday that already exists must not look added."""
    sql = ("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s) "
           "ON CONFLICT (role_name) DO NOTHING")
    assert db.execute_update(sql, ("Roto", "[]")).rows == 1
    second = db.execute_update(sql, ("Roto", "[]"))
    assert second.ok and second.rows == 0


def test_a_refused_statement_is_a_failed_result_not_an_exception(db):
    result = db.execute_update("UPDATE no_such_table SET x = 1")
    assert not result
    assert "no_such_table" in result.error
    assert db.last_error() == result.error


def test_strict_raises_on_refusal(db):
    with pytest.raises(DatabaseWriteError):
        db.write("UPDATE no_such_table SET x = 1", strict=True)


def test_a_refused_read_is_none_on_both(db):
    assert db.execute_query("SELECT nope FROM it_deployments", fetch="all") is None
    assert "nope" in db.last_error()


def test_a_successful_read_clears_the_last_error(db):
    db.execute_query("SELECT nope FROM it_deployments", fetch="all")
    assert db.execute_query("SELECT id FROM it_deployments", fetch="all") == []
    assert db.last_error() == ""


def test_an_update_run_through_the_default_fetch_mode_is_not_an_error(db):
    """It used to raise 'no results to fetch' after committing on PostgreSQL."""
    _deployment(db)
    assert db.execute_query("UPDATE it_deployments SET status = 'Done'") == []
    assert db.last_error() == ""
    row = db.execute_query("SELECT status FROM it_deployments", fetch="one")
    assert dict(row)["status"] == "Done"


# ------------------------------------------------------------ execute_sql


def test_execute_sql_returns_columns_rows_and_truncation(db):
    for n in range(3):
        _deployment(db, "pkg%d" % n)
    result = db.execute_sql("SELECT id, package_name FROM it_deployments ORDER BY id", max_rows=2)
    assert isinstance(result, SqlResult) and result.ok
    assert result.columns == ["id", "package_name"]
    assert len(result.rows) == 2 and result.truncated
    assert result.is_query


def test_execute_sql_is_read_only(db):
    """SYS-002/041-044: the console cannot write, not even behind a SELECT."""
    for n in range(3):
        _deployment(db, "pkg%d" % n)
    for sql in ("UPDATE it_deployments SET status = 'Done'",
                "SELECT 1; UPDATE it_deployments SET status = 'Done'",
                "SELECT 1; COMMIT; UPDATE it_deployments SET status = 'Done'",
                "WITH x AS (UPDATE it_deployments SET status = 'Done' RETURNING id) SELECT * FROM x"):
        result = db.execute_sql(sql)
        assert not result.ok and result.error, sql
    assert db.execute_sql("SELECT 1 AS one;").rows == [{"one": 1}]
    rows = db.execute_query("SELECT status FROM it_deployments", fetch="all")
    assert [dict(r)["status"] for r in rows] == ["Pending"] * 3


def test_execute_sql_shows_the_database_error(db):
    """SYS-016: an error must be shown, not replaced by 'Executed.'"""
    result = db.execute_sql("SELECT * FROM table_that_does_not_exist")
    assert not result.ok
    assert "table_that_does_not_exist" in result.error


# ------------------------------------------------------------ atomic


def test_atomic_commits_everything_together(db):
    with db.atomic() as tx:
        first = tx.write(
            "INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
            "VALUES (%s, %s, %s, %s) RETURNING id", ("A", "WS", "me", "Pending"))
        tx.write("UPDATE it_deployments SET status = %s WHERE id = %s",
                 ("Done", first.last_id), expect_rows=True)
        assert tx.value("SELECT COUNT(*) FROM it_deployments") == 1
    rows = db.execute_query("SELECT status FROM it_deployments", fetch="all")
    assert [dict(r)["status"] for r in rows] == ["Done"]


def test_atomic_rolls_back_on_a_refused_statement(db):
    with pytest.raises(DatabaseWriteError):
        with db.atomic() as tx:
            tx.write("INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
                     "VALUES (%s, %s, %s, %s)", ("A", "WS", "me", "Pending"))
            tx.write("UPDATE no_such_table SET x = 1")
    assert db.execute_query("SELECT id FROM it_deployments", fetch="all") == []


def test_atomic_rolls_back_when_a_required_row_is_missing(db):
    with pytest.raises(NoRowsError):
        with db.atomic() as tx:
            tx.write("INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
                     "VALUES (%s, %s, %s, %s)", ("A", "WS", "me", "Pending"))
            tx.write("UPDATE it_deployments SET status = 'x' WHERE id = %s", (424242,),
                     expect_rows=True)
    assert db.execute_query("SELECT id FROM it_deployments", fetch="all") == []


def test_atomic_rolls_back_on_any_exception(db):
    with pytest.raises(ZeroDivisionError):
        with db.atomic() as tx:
            tx.write("INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
                     "VALUES (%s, %s, %s, %s)", ("A", "WS", "me", "Pending"))
            1 / 0
    assert db.execute_query("SELECT id FROM it_deployments", fetch="all") == []


def test_the_atomic_helper_works_on_a_handle_without_atomic():
    """Test doubles and old handles get a sequential stand-in that still stops at a failure."""
    class Old:
        def __init__(self):
            self.ran = []

        def execute_update(self, sql, params=None):
            self.ran.append(sql)
            return "bad" not in sql

        def execute_query(self, sql, params=None, fetch="all"):
            return []

    old = Old()
    with pytest.raises(DatabaseWriteError):
        with atomic(old) as tx:
            tx.write("good 1")
            tx.write("bad")
            tx.write("never")
    assert old.ran == ["good 1", "bad"]


def test_an_outage_is_not_a_refusal():
    """An unreachable database raises, it does not come back as ok=False."""
    class Down:
        def execute_update(self, *a, **k):
            raise DatabaseUnavailableError("down")

    with pytest.raises(DatabaseUnavailableError):
        with atomic(Down()) as tx:
            tx.write("anything")
