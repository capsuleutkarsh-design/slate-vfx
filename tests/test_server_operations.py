"""
The server's own operations: facts, backups and maintenance.

The server could start and stop a database and show three numbers about it. It
could not say which database it was serving, take a backup, run a vacuum, read
its own log, or disconnect a stuck session - and every one of those needed a
terminal or a script somebody had to know existed.

The test that matters most here is the first one. A server showing a correct IP,
a correct port and a correct connection count while serving a brand new empty
cluster in a fallback location is what actually happened, and nothing on the
screen contradicted anything else.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from slate_server.core import server_facts
from slate_server.core.backup_engine import BackupEngine
from slate_server.core.maintenance import Maintenance, MaintenanceLog


# ------------------------------------------------------------------ the facts

def test_a_directory_with_no_cluster_in_it_is_reported_as_such(tmp_path):
    """
    An empty folder is not a database. The server will happily build one there,
    and a new empty cluster looks identical to the real thing by every other
    figure on the dashboard.
    """
    facts = server_facts.cluster(tmp_path, port=1)
    assert facts["exists"] is True
    assert facts["initialised"] is False
    assert facts["error"], "an unreachable database must say so, not report zero"


def test_a_missing_directory_is_not_mistaken_for_an_empty_one(tmp_path):
    facts = server_facts.cluster(tmp_path / "nowhere", port=1)
    assert facts["exists"] is False
    assert facts["initialised"] is False


def test_an_initialised_cluster_is_recognised(tmp_path):
    (tmp_path / "PG_VERSION").write_text("16")
    facts = server_facts.cluster(tmp_path, port=1)
    assert facts["initialised"] is True


def test_directory_size_and_how_it_reads(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 2048)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"y" * 1024)

    assert server_facts.directory_size(tmp_path) == 3072
    assert server_facts.human_size(3072) == "3 KB"
    assert server_facts.human_size(5 * 1024 * 1024) == "5.0 MB"
    assert server_facts.human_size(0) == "0 B"


def test_a_session_idle_in_transaction_is_flagged():
    """
    The one to catch. It holds its locks until it comes back or is disconnected,
    and without the duration beside it it looks like a healthy idle session.
    """
    stuck = {"state": "idle in transaction", "idle_seconds": 600,
             "transaction_seconds": 600}
    assert "locks" in server_facts.session_warning(stuck).lower()

    fine = {"state": "idle", "idle_seconds": 600, "transaction_seconds": 0}
    assert server_facts.session_warning(fine) == ""

    brief = {"state": "idle in transaction", "idle_seconds": 5,
             "transaction_seconds": 5}
    assert server_facts.session_warning(brief) == ""


def test_a_long_running_query_is_flagged():
    row = {"state": "active", "idle_seconds": 0, "transaction_seconds": 900}
    assert server_facts.session_warning(row)


def test_a_pool_publishing_the_wrong_name_is_caught(tmp_path):
    """
    The exact state that refused every client for a day: a pooler left running
    from an older install, publishing 'slate' while clients asked for 'ut_vfx'.
    Nothing about the pool being up revealed it.
    """
    conf = tmp_path / "pgbouncer"
    conf.mkdir()
    ini = conf / "pgbouncer.ini"
    ini.write_text(
        "[databases]\n"
        "slate = host=127.0.0.1 port=5440 dbname=ut_vfx user=app\n"
        "[pgbouncer]\nlisten_port = 6432\n", encoding="utf-8")

    class FakePool:
        listen_port = 6432
        dbname = "ut_vfx"
        ini_path = ini

        def is_installed(self):
            return True

        def is_ready(self):
            return True

    facts = server_facts.pool(FakePool())
    assert facts["publishes"] == "slate"
    assert facts["expects"] == "ut_vfx"
    assert facts["agrees"] is False


def test_a_pool_that_agrees_is_reported_as_agreeing(tmp_path):
    ini = tmp_path / "pgbouncer.ini"
    ini.write_text("[databases]\nut_vfx = host=127.0.0.1 dbname=ut_vfx\n",
                   encoding="utf-8")

    class FakePool:
        listen_port = 6432
        dbname = "ut_vfx"
        ini_path = ini

        def is_installed(self):
            return True

        def is_ready(self):
            return True

    assert server_facts.pool(FakePool())["agrees"] is True


def test_no_pool_at_all_is_not_an_exception():
    facts = server_facts.pool(None)
    assert facts["error"]
    assert facts["running"] is False


# ---------------------------------------------------------------- the backups

def _dump(directory: Path, database: str, when: datetime, size: int = 1024) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("slate_%s_%s.dump" % (database, when.strftime("%Y%m%d_%H%M%S")))
    path.write_bytes(b"x" * size)
    return path


def test_backups_are_listed_newest_first_with_their_age(tmp_path):
    backups = tmp_path / "Backups"
    _dump(backups, "ut_vfx", datetime.now() - timedelta(days=10))
    _dump(backups, "ut_vfx", datetime.now() - timedelta(days=1))

    engine = BackupEngine(tmp_path / "bin", backups, dbname="ut_vfx")
    rows = engine.backups()

    assert [row["age_days"] for row in rows] == [1, 10]
    assert engine.latest()["age_days"] == 1


def test_a_half_written_dump_is_never_listed_as_a_backup(tmp_path):
    """
    pg_dump writes to a .partial name and it is renamed only on success. A dump
    interrupted half way through must not be mistaken for a usable one - that is
    the failure that turns a backup policy into a false sense of security.
    """
    backups = tmp_path / "Backups"
    backups.mkdir(parents=True)
    (backups / "slate_ut_vfx_20260101_120000.partial").write_bytes(b"incomplete")

    engine = BackupEngine(tmp_path / "bin", backups, dbname="ut_vfx")
    assert engine.backups() == []


def test_retention_never_deletes_everything(tmp_path):
    """
    A studio that has not run the server for two months comes back to every
    backup being older than the retention window. Age alone would delete all of
    them at once, which is the worst possible moment to have no backups.
    """
    backups = tmp_path / "Backups"
    for days in range(1, 11):
        _dump(backups, "ut_vfx", datetime.now() - timedelta(days=60 + days))

    engine = BackupEngine(tmp_path / "bin", backups, dbname="ut_vfx")
    doomed = engine.prune(keep_days=30, keep_at_least=3, apply=False)

    assert len(doomed) == 7
    assert len(engine.backups()) == 10, "a dry run must change nothing"

    engine.prune(keep_days=30, keep_at_least=3, apply=True)
    assert len(engine.backups()) == 3


def test_retention_keeps_anything_inside_the_window(tmp_path):
    backups = tmp_path / "Backups"
    _dump(backups, "ut_vfx", datetime.now() - timedelta(days=2))
    _dump(backups, "ut_vfx", datetime.now() - timedelta(days=40))

    engine = BackupEngine(tmp_path / "bin", backups, dbname="ut_vfx")
    removed = engine.prune(keep_days=30, keep_at_least=1, apply=True)

    assert [row["age_days"] for row in removed] == [40]
    assert len(engine.backups()) == 1


def test_a_restore_is_refused_without_an_explicit_confirmation(tmp_path):
    """
    Restoring is the one operation here that destroys data, and it destroys all
    of it. It cannot happen as a side effect of a mis-click.
    """
    backups = tmp_path / "Backups"
    dump = _dump(backups, "ut_vfx", datetime.now())

    engine = BackupEngine(tmp_path / "bin", backups, dbname="ut_vfx")
    result = engine.restore(dump, confirm_overwrite=False)

    assert result["ok"] is False
    assert "confirm" in result["message"].lower()


def test_a_backup_without_pg_dump_says_so_rather_than_failing_quietly(tmp_path):
    engine = BackupEngine(tmp_path / "nothing-here", tmp_path / "Backups",
                          dbname="ut_vfx")
    assert engine.is_available() is False
    result = engine.back_up()
    assert result["ok"] is False
    assert "pg_dump" in result["message"]


# ------------------------------------------------------------- the maintenance

def test_a_job_that_has_never_run_says_never(tmp_path):
    """
    A maintenance job with no last-run time is one nobody can tell has stopped,
    and the way these fail is by not happening at all.
    """
    log = MaintenanceLog(tmp_path / "maintenance.json")
    summary = {row["job"]: row for row in log.summary()}

    assert summary["vacuum"]["last_at"] is None
    assert summary["vacuum"]["state"] == "never run"
    assert summary["vacuum"]["overdue"] is True


def test_a_job_that_just_ran_is_up_to_date(tmp_path):
    log = MaintenanceLog(tmp_path / "maintenance.json")
    log.record("vacuum", True, "Vacuum and analyze finished.")

    row = {r["job"]: r for r in log.summary()}["vacuum"]
    assert row["state"] == "up to date"
    assert row["overdue"] is False
    assert row["last_ok"] is True
    assert log.ran_today("vacuum") is True


def test_a_failure_is_remembered_as_a_failure(tmp_path):
    log = MaintenanceLog(tmp_path / "maintenance.json")
    log.record("backup", False, "pg_dump failed: no space left on device")

    row = {r["job"]: r for r in log.summary()}["backup"]
    assert row["state"] == "failed"
    assert "no space" in row["last_message"]


def test_the_overdue_list_is_what_an_unattended_run_would_do(tmp_path):
    jobs = Maintenance(tmp_path / "bin", tmp_path / "LocalDatabase", dbname="ut_vfx")
    jobs.log.record("vacuum", True, "done")

    due = jobs.due()
    assert "vacuum" not in due
    assert "backup" in due and "reindex" in due


def test_the_log_survives_being_unreadable(tmp_path):
    """A corrupt log must not stop the screen from drawing."""
    path = tmp_path / "maintenance.json"
    path.write_text("this is not json", encoding="utf-8")

    log = MaintenanceLog(path)
    assert log.last("vacuum")["at"] is None
    assert len(log.summary()) == 4


def test_comp_off_is_skipped_not_failed_when_the_studio_does_not_operate_it(tmp_path):
    """
    A job that reports failure for correctly doing nothing teaches people to
    ignore the column it reports in.
    """
    from slate.core.domain import leave_policy as lp

    lp.set_overrides({"comp_off_enabled": False})
    try:
        jobs = Maintenance(tmp_path / "bin", tmp_path / "LocalDatabase", dbname="ut_vfx")
        result = jobs.credit_comp_off()
        assert result["ok"] is True
        assert result.get("skipped") is True
        assert {r["job"]: r for r in jobs.log.summary()}["comp_off"]["last_ok"] is True
    finally:
        lp.set_overrides({})
