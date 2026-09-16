"""
My Tickets writes its replies to it_ticket_comments. Until now only an Alembic
revision created that table, and Alembic never runs on a studio - so on a
fresh install every reply was dropped and the thread read as empty.
"""

import pytest


def _tables(db):
    postgres = db.get_runtime_status().get("active_mode") == "postgres"
    if postgres:
        rows = db.execute_query(
            "select table_name from information_schema.tables where table_schema='public'", fetch="all")
        return {r["table_name"] if isinstance(r, dict) else r[0] for r in rows}
    rows = db.execute_query("select name from sqlite_master where type='table'", fetch="all")
    return {r["name"] if isinstance(r, dict) else r[0] for r in rows}


def test_the_table_is_declared_for_both_backends():
    from slate.core.infra.migrations import workplace_schema as ws
    assert "it_ticket_comments" in ws.TABLES_PG
    assert "it_ticket_comments" in ws.TABLES_SQLITE
    for sql in (ws.TABLES_PG["it_ticket_comments"], ws.TABLES_SQLITE["it_ticket_comments"]):
        for column in ("ticket_id", "author", "comment_text", "timestamp"):
            assert column in sql, "the view reads and writes exactly these"


def test_a_fresh_sqlite_database_has_it(mock_db):
    from slate.core.infra.migrations.workplace_schema import apply_migration
    apply_migration(mock_db)
    assert "it_ticket_comments" in _tables(mock_db)
    mock_db.execute_update(
        "INSERT INTO it_ticket_comments (ticket_id, author, comment_text) VALUES (?, ?, ?)"
        if mock_db.get_runtime_status().get("active_mode") != "postgres" else
        "INSERT INTO it_ticket_comments (ticket_id, author, comment_text) VALUES (%s, %s, %s)",
        (1, "EMP0001", "on it"))
    rows = mock_db.execute_query("SELECT author, comment_text, timestamp FROM it_ticket_comments", fetch="all")
    assert len(rows) == 1


def test_a_fresh_postgres_database_has_it(pg_db):
    assert "it_ticket_comments" in _tables(pg_db)
    pg_db.execute_update(
        "INSERT INTO it_ticket_comments (ticket_id, author, comment_text) VALUES (%s, %s, %s)",
        (1, "EMP0001", "on it"))
    rows = pg_db.execute_query(
        "SELECT author, comment_text, timestamp FROM it_ticket_comments WHERE ticket_id = %s ORDER BY id ASC",
        (1,), fetch="all")
    assert len(rows) == 1 and rows[0]["timestamp"]
