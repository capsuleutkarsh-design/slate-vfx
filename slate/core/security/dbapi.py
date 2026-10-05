"""
A raw DB-API connection dressed as Slate's database manager.

The recovery tool and the server talk to PostgreSQL with a plain psycopg2
connection (often through the loopback trust window, as the superuser). The
guard and switch code is written against Slate's managers - execute_query(sql,
params, fetch=) returning dict rows, and execute_update(sql, params). This is
the small bridge between the two, so the same code runs in both places.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConnectionDB:
    """execute_query / execute_update over one DB-API connection (autocommit)."""

    def __init__(self, conn):
        self.conn = conn
        try:
            conn.autocommit = True
        except Exception:
            pass

    def execute_query(self, sql, params=None, fetch="all"):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or None)
            if cur.description is None:
                return None
            names = [d[0] for d in cur.description]
            if fetch == "one":
                row = cur.fetchone()
                return dict(zip(names, row)) if row else None
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def execute_update(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or None)
            return max(cur.rowcount, 0) or True

    def table_exists(self, name: str) -> bool:
        row = self.execute_query(
            "SELECT 1 AS x FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s", (name,), fetch="one")
        return bool(row)

    def column_exists(self, table: str, column: str) -> bool:
        row = self.execute_query(
            "SELECT 1 AS x FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
            (table, column), fetch="one")
        return bool(row)
