"""
What the server is actually serving.

The dashboard used to show an IP, a port and a connection count. All three were
correct while the server quietly ran a brand new empty cluster in a fallback
location and the studio's real database sat untouched somewhere else - so every
figure on screen agreed with every other one, and all of them were about the
wrong database.

Nothing here changes anything. Every function answers one question about the
running server, returns a plain dict, and reports trouble as a string rather
than raising - a diagnostics panel that crashes is worse than no panel.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _connect(port: int, dbname: str = ""):
    import psycopg2
    from slate_server.core.db_credentials import connect_kwargs
    return psycopg2.connect(**connect_kwargs(port, dbname, connect_timeout=3))


def explain(exc) -> str:
    """
    Turn a driver error into something worth putting on a dashboard.

    Two of these matter more than the rest and neither says what it means.
    "no password supplied" is not a network problem and not a wrong password:
    it is a server that has no password configured at all, which is the state a
    frozen build shipped in. "password authentication failed" is the opposite -
    a password is configured and the database disagrees with it. Left as they
    are, both read as "the database is down" and send somebody to the wrong
    place for an afternoon.
    """
    text = str(exc).strip()
    text = text.splitlines()[0] if text else "the database did not answer"
    lowered = text.lower()

    if "no password supplied" in lowered:
        return ("no database password is configured on this server, so it "
                "cannot log in to its own database. Set it in Settings and "
                "restart the server")
    if "password authentication failed" in lowered:
        return ("the database rejected this server's password - the password "
                "in Settings is not the one the database has")
    if "does not exist" in lowered and "role" in lowered:
        return "%s - the accounts were never created on this cluster" % text
    return text


def directory_size(path) -> int:
    """Bytes under a directory. 0 when it is not there or cannot be read."""
    total = 0
    try:
        for root, _dirs, files in os.walk(str(path)):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def human_size(num_bytes: int) -> str:
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "%.0f %s" % (size, unit) if unit in ("B", "KB") else "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%.1f TB" % size


def cluster(data_dir, port: int) -> dict:
    """
    The cluster on disk and what is in it.

    ``tables`` is the count in the studio database's public schema, and it is
    the number that matters most: a database that exists with no tables in it is
    what a database created by mistake looks like, and it is indistinguishable
    from a healthy one by any other figure on the screen.
    """
    path = Path(str(data_dir)) if data_dir else None
    facts = {
        "data_dir": str(path) if path else "",
        "exists": bool(path and path.exists()),
        "initialised": bool(path and (path / "PG_VERSION").exists()),
        "size_bytes": 0,
        "size": "-",
        "databases": [],
        "database": "",
        "tables": None,
        "empty": None,
        "error": "",
    }
    if path is not None:
        facts["size_bytes"] = directory_size(path)
        facts["size"] = human_size(facts["size_bytes"])

    try:
        from slate_server.core.db_credentials import database_name
        facts["database"] = database_name()
    except Exception:
        facts["database"] = ""

    try:
        with _connect(port) as con:
            with con.cursor() as cur:
                cur.execute("SELECT datname FROM pg_database "
                            "WHERE NOT datistemplate ORDER BY datname")
                facts["databases"] = [r[0] for r in cur.fetchall()]

                cur.execute("SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema = 'public'")
                facts["tables"] = int(cur.fetchone()[0])
                facts["empty"] = facts["tables"] == 0

                # The directory the running server actually opened, which is not
                # necessarily the one anybody configured.
                cur.execute("SHOW data_directory")
                running = cur.fetchone()[0]
                facts["running_data_dir"] = running
                if path is not None and running:
                    try:
                        facts["matches_config"] = (
                            Path(running).resolve() == path.resolve())
                    except OSError:
                        facts["matches_config"] = None
    except Exception as exc:
        facts["error"] = explain(exc)

    return facts


def data_bytes(data_dir) -> int:
    """
    How much more than an empty database the biggest database here holds.

    Measured against the smallest database directory in the same cluster -
    template0, a pristine empty database PostgreSQL keeps beside the real ones -
    so the baseline is right for this version and this machine. Counting files
    does not work: thirty empty tables are thirty files and no data, and a
    schema with nothing in it is exactly what this must not mistake for the
    studio's work. It is a hint on a dashboard, not a verdict, and it is shown
    as a size so a person can judge it.
    """
    base = Path(str(data_dir)) / "base"
    try:
        sizes = [directory_size(c) for c in base.iterdir() if c.is_dir()]
    except OSError:
        return 0
    if not sizes:
        return 0
    return max(0, max(sizes) - min(sizes))


def looks_populated(data_dir) -> bool:
    """More than a megabyte beyond empty: a schema, and probably rows."""
    return data_bytes(data_dir) > 1024 * 1024


def known_cluster_locations() -> list:
    """
    Everywhere this software has ever put, or been pointed at, a cluster.

    Four empty clusters were found on one machine in a day, each created when
    a default resolved to an empty folder. The studio's real one was a fifth
    location none of the screens mentioned.
    """
    home = Path(os.path.expanduser("~"))
    local = Path(os.environ.get("LOCALAPPDATA", str(home)))
    checkout = Path(__file__).resolve().parents[2]

    candidates = [
        local / "Slate_Central" / "LocalDatabase",
        checkout / "LocalDatabase",
        home / "RuntimeData" / "Slate_Central" / "Database",
        home / "RuntimeData" / "Slate_Central" / "LocalDatabase",
    ]
    try:
        from slate.core.infra.local_secrets import local_config
        root = str(local_config().get("SERVER_ROOT") or "").strip()
        if root:
            candidates.append(Path(root) / "Database")
            candidates.append(Path(root) / "LocalDatabase")
    except Exception:
        pass
    return candidates


def other_clusters(current) -> list:
    """
    Clusters on this machine other than the one being served.

    Each entry says whether it looks populated. The point is the one that does
    while the served one does not: that is the studio's database, sitting
    where nothing is looking.
    """
    try:
        here = Path(str(current)).resolve() if current else None
    except OSError:
        here = None

    found = []
    seen = set()
    for path in known_cluster_locations():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or (here is not None and resolved == here):
            continue
        seen.add(key)
        if not (path / "PG_VERSION").exists():
            continue
        extra = data_bytes(path)
        found.append({
            "path": str(path),
            "size": human_size(directory_size(path)),
            "data": human_size(extra),
            "data_bytes": extra,
            "populated": extra > 1024 * 1024,
        })
    # The one with the most in it first - that is the one to point at.
    found.sort(key=lambda o: o["data_bytes"], reverse=True)
    return found


def schema_state(port: int) -> dict:
    """
    Whether the database has the shape the software expects.

    Two separate things, and they can disagree. Alembic's version marker says
    which migrations have been recorded; the presence of the tables says what is
    actually there. A studio that has had columns added outside Alembic has the
    tables and a stale marker, which is worth seeing before somebody runs a
    migration over it.
    """
    state = {"alembic_version": "", "tables": None, "missing": [], "error": ""}

    # The tables the workplace modules cannot run without.
    expected = ("ut_users", "leave_requests", "it_tickets", "hardware_inventory",
                "onboarding_workflows", "software_licenses", "holiday_calendar",
                "tracking_projects", "tracking_shots", "attendance_log")

    try:
        with _connect(port) as con:
            with con.cursor() as cur:
                cur.execute("SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'")
                present = {r[0] for r in cur.fetchall()}
                state["tables"] = len(present)
                state["missing"] = sorted(t for t in expected if t not in present)

                if "alembic_version" in present:
                    cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                    row = cur.fetchone()
                    state["alembic_version"] = row[0] if row else ""
    except Exception as exc:
        state["error"] = explain(exc)

    return state


def sessions(port: int) -> list:
    """
    Who is connected, for how long, and what they are doing.

    The duration and the state are the two columns that matter when the database
    has gone slow: a session idle in transaction holds locks and blocks
    everybody, and it looks identical to a healthy idle one without them.
    """
    rows = []
    try:
        with _connect(port) as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT pid, client_addr, application_name, state, "
                    "       COALESCE(EXTRACT(EPOCH FROM (now() - state_change)), 0), "
                    "       COALESCE(EXTRACT(EPOCH FROM (now() - xact_start)), 0), "
                    "       LEFT(COALESCE(query, ''), 200) "
                    "FROM pg_stat_activity "
                    "WHERE pid <> pg_backend_pid() AND client_addr IS NOT NULL "
                    "ORDER BY state, state_change")
                for pid, addr, app, state, idle_for, xact_for, query in cur.fetchall():
                    rows.append({
                        "pid": int(pid),
                        "client": str(addr or ""),
                        "application": str(app or ""),
                        "state": str(state or ""),
                        "idle_seconds": float(idle_for or 0),
                        "transaction_seconds": float(xact_for or 0),
                        "query": str(query or "").strip(),
                    })
    except Exception as exc:
        logger.debug("Could not read the session list: %s", exc)
    return rows


def session_warning(row: dict) -> str:
    """
    Why this session is worth looking at. Empty when it is not.

    Idle in transaction is the one to catch: it is a client that opened a
    transaction and wandered off, and it holds its locks until it comes back or
    is disconnected.
    """
    state = (row.get("state") or "").lower()
    if state == "idle in transaction" and row.get("idle_seconds", 0) > 60:
        return "Idle in transaction - holding locks"
    if state == "active" and row.get("transaction_seconds", 0) > 300:
        return "Running for over five minutes"
    if state == "idle in transaction (aborted)":
        return "Transaction aborted and not rolled back"
    return ""


def terminate(port: int, pid: int) -> tuple:
    """
    Disconnect one session. Returns (ok, message).

    Asks politely first: pg_cancel_backend stops the query and leaves the
    connection, which is enough for a runaway SELECT and costs the client
    nothing. Only a session that ignores that is terminated.
    """
    try:
        with _connect(port) as con:
            con.autocommit = True
            with con.cursor() as cur:
                cur.execute("SELECT pg_cancel_backend(%s)", (int(pid),))
                cancelled = bool(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE pid = %s",
                            (int(pid),))
                still_there = int(cur.fetchone()[0]) > 0
                if not still_there:
                    return True, "Session %d closed." % pid

                cur.execute("SELECT pg_terminate_backend(%s)", (int(pid),))
                killed = bool(cur.fetchone()[0])
        if killed:
            return True, "Session %d disconnected." % pid
        if cancelled:
            return True, "Cancelled the query on session %d." % pid
        return False, "Session %d did not respond; it may have already gone." % pid
    except Exception as exc:
        return False, str(exc).strip().splitlines()[0] if str(exc).strip() else "failed"


def pool(engine) -> dict:
    """
    What the connection pool is doing, and whether clients can use it.

    A pool that publishes a different database name from the one clients ask for
    refuses every connection, and nothing about the pool being "up" reveals it.
    That mismatch has happened here: a pooler left running from an older install
    held the port with a stale config for a day.
    """
    facts = {"installed": False, "running": False, "listen_port": None,
             "publishes": "", "expects": "", "agrees": None, "ini": "",
             "clients": None, "error": ""}
    if engine is None:
        facts["error"] = "no pool configured"
        return facts

    try:
        facts["installed"] = bool(engine.is_installed())
        facts["running"] = bool(engine.is_ready())
        facts["listen_port"] = int(getattr(engine, "listen_port", 0) or 0)
        facts["expects"] = str(getattr(engine, "dbname", "") or "")
        ini = getattr(engine, "ini_path", None)
        facts["ini"] = str(ini) if ini else ""

        # What the file on disk actually publishes, rather than what this
        # process would write if it were asked to.
        if ini and Path(ini).exists():
            in_databases = False
            for line in Path(ini).read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("["):
                    in_databases = line.lower() == "[databases]"
                    continue
                if in_databases and "=" in line and not line.startswith(";"):
                    facts["publishes"] = line.split("=", 1)[0].strip()
                    break

        if facts["publishes"] and facts["expects"]:
            facts["agrees"] = facts["publishes"].lower() == facts["expects"].lower()
    except Exception as exc:
        facts["error"] = str(exc)

    return facts
