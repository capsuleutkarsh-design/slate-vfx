"""
One thing owns the schema, and it is a thing that actually runs.

The studio's PostgreSQL database had 0 tables, and the reason was not the
server. The schema was owned by two mechanisms:

    postgres_manager._ensure_db + workplace_schema   runs on both backends,
                                                     on every start, and works
                                                     inside an installed build

    Alembic                                          runs on PostgreSQL only,
                                                     shells out to an alembic
                                                     program, needs alembic.ini
                                                     and a versions folder, and
                                                     none of those are bundled

So Alembic has never run on a studio machine. And on a machine where it could
run it aborted on its first statement, because its revisions add columns that
_ensure_db has already created - which meant it never stamped a version and
could never make progress either. No database in this studio carries an
alembic_version row.

Anything only Alembic provided therefore existed nowhere:

    leave_requests          the table the entire Leave tab reads and writes
    it_tickets.priority     read in twenty-five files
    hardware_inventory.cpu, .location
    it_tickets.resolved_at
    payroll_records.overtime_hours

These tests hold the rule that came out of it: if the application needs it, the
mechanism that creates it has to be one that runs everywhere.
"""

import re
from pathlib import Path

import pytest

from slate.core.infra.migrations import workplace_schema as ws


ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "slate" / "core" / "infra" / "migrations" / "versions"
PG_MANAGER = (ROOT / "slate" / "core" / "infra"
              / "postgres_manager.py").read_text(encoding="utf-8")


def _ensure_db_creates(table: str) -> bool:
    return ("CREATE TABLE IF NOT EXISTS %s (" % table) in PG_MANAGER


def _ensure_db_column(table: str, column: str) -> bool:
    match = re.search(r"CREATE TABLE IF NOT EXISTS %s \((.*?)\n        \)"
                      % re.escape(table), PG_MANAGER, re.S)
    return bool(match) and bool(re.search(r"^\s*%s\b" % re.escape(column),
                                          match.group(1), re.M))


def _alembic_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in VERSIONS.glob("*.py"))


# --------------------------------------------------- nothing is Alembic-only

def test_no_table_the_server_checks_for_needs_alembic():
    """
    server_facts lists the tables the workplace modules cannot run without. If
    one of them can only be made by Alembic, it is not made at all.
    """
    from slate_server.core import server_facts

    source = (ROOT / "slate_server" / "core"
              / "server_facts.py").read_text(encoding="utf-8")
    expected = re.search(r"expected = \((.*?)\)", source, re.S).group(1)
    names = re.findall(r'"([a-z_]+)"', expected)
    assert len(names) >= 10, "the expected list was not read correctly"

    for table in names:
        assert table in ws.TABLES_PG or _ensure_db_creates(table), \
            ("%s is created by neither workplace_schema nor _ensure_db, so on "
             "PostgreSQL it only exists if Alembic ran - and Alembic cannot "
             "run on an installed studio" % table)


def test_leave_requests_is_created_on_both_backends():
    """
    The one that was actually missing. It existed in sqlite_manager and in an
    Alembic revision, which is why Leave worked in local fallback and failed
    against the real database.
    """
    assert "leave_requests" in ws.TABLES_PG
    assert "leave_requests" in ws.TABLES_SQLITE

    for name, tables in (("postgres", ws.TABLES_PG), ("sqlite", ws.TABLES_SQLITE)):
        sql = tables["leave_requests"]
        for column in ("user_id", "type", "start_date", "end_date", "half_day",
                       "reason", "status"):
            assert column in sql, "%s leave_requests has no %s" % (name, column)


def test_every_column_alembic_adds_is_provided_by_something_that_runs():
    """
    The guard that would have caught it_tickets.priority. A column that only an
    Alembic revision adds is a column no studio has.
    """
    source = _alembic_source()

    added = set()
    for table, column in re.findall(
            r"add_column\(\s*'([a-z_]+)'\s*,\s*sa\.Column\(\s*'([a-z_]+)'",
            source):
        added.add((table, column))
    for table, column in re.findall(
            r"ALTER TABLE\s+([a-z_]+)\s+ADD COLUMN\s+(?:IF NOT EXISTS\s+)?([a-z_]+)",
            source, re.I):
        added.add((table.lower(), column.lower()))

    assert added, "no column additions were found; the parsing is wrong"

    known = {(t, c) for t, c, _pg, _lite in ws.COLUMNS}
    orphans = sorted("%s.%s" % (t, c) for t, c in added
                     if (t, c) not in known and not _ensure_db_column(t, c))

    assert not orphans, (
        "These exist only in an Alembic revision, so no studio has them:\n  "
        + "\n  ".join(orphans))


# ------------------------------------------------------- and it is not called

def test_the_application_does_not_run_alembic_at_startup():
    """
    It cannot work from an installed build and it aborted everywhere else, so
    all it produced on a studio machine was a traceback in the log during
    start-up.
    """
    source = (ROOT / "slate" / "core" / "infra"
              / "database_manager.py").read_text(encoding="utf-8")

    live = [l for l in source.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    assert not any("run_auto_migrations" in l for l in live), \
        "DatabaseManager is running Alembic again"


def test_alembic_is_kept_for_development():
    """Not deleted - it is a reasonable tool with a repository to work on."""
    assert (ROOT / "alembic.ini").is_file()
    assert list(VERSIONS.glob("*.py"))


def test_the_scheduling_revision_can_be_applied_twice():
    """
    Its bare ADD COLUMN is what aborted Alembic on any database a client had
    ever connected to, because _ensure_db creates prod_scheduling with that
    column already on it.
    """
    source = (VERSIONS / "f8397c278b5a_add_scheduling_deps.py").read_text(
        encoding="utf-8")
    assert "IF NOT EXISTS" in source
    assert "op.add_column(" not in source
