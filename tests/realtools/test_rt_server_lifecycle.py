"""
The server's own lifecycle with the real PostgreSQL and PgBouncer:

    DatabaseEngine     init / start / stop / adopt a cluster on another port
    PgBouncerEngine    start, the Restart pool button, ensure_running, stop
    server_facts       cluster, schema_state, sessions, terminate, pool

All on throwaway clusters listening on 127.0.0.1 only, on free ports.
"""

import json
import socket
import subprocess
from types import SimpleNamespace

import psycopg2
import pytest

from conftest import APP_PASSWORD, BIN, DBNAME

pytestmark = [pytest.mark.realtools,
              pytest.mark.skipif(not (BIN / "initdb.exe").exists(),
                                 reason="the bundled PostgreSQL is not here")]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _loopback_only(monkeypatch):
    """initdb's settings say listen on '*'; this PC is on a real network."""
    from slate_server.core.db_engine import DatabaseEngine
    original = DatabaseEngine._configure_network_access

    def configure(self):
        original(self)
        with open(self.data_dir / "postgresql.conf", "a", encoding="utf-8") as conf:
            conf.write("listen_addresses = '127.0.0.1'\n")
    monkeypatch.setattr(DatabaseEngine, "_configure_network_access", configure)


def _login(port, user="ut_vfx_app", password="rt-pass", dbname="slate_rt"):
    return psycopg2.connect(host="127.0.0.1", port=port, user=user, password=password,
                            dbname=dbname, connect_timeout=5)


def test_engine_builds_starts_stops_and_adopts_a_cluster_on_another_port(tmp_path,
                                                                        monkeypatch):
    from slate_server.core import db_credentials
    from slate_server.core.db_engine import DatabaseEngine, NoDatabaseHere, PortInUse

    for name in ("SLATE_DB_PASSWORD", "SLATE_DB_ADMIN_PASSWORD", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)
    credentials = tmp_path / "config.json"
    credentials.write_text(json.dumps({"db_password": "rt-pass", "db_name": "slate_rt"}),
                           encoding="utf-8")
    monkeypatch.setattr(db_credentials, "_config_layers", lambda: [credentials])
    db_credentials.reload()
    _loopback_only(monkeypatch)

    data = tmp_path / "db" / "LocalDatabase"
    port = _free_port()
    engines = []
    try:
        # An empty folder is never built on unless a person said so.
        with pytest.raises(NoDatabaseHere):
            DatabaseEngine(str(data), port=port).start()
        assert not (data / "PG_VERSION").exists()

        first = DatabaseEngine(str(data), port=port, allow_create=True)
        engines.append(first)
        assert first.start() and first.is_ready()
        hba = (data / "pg_hba.conf").read_text(encoding="utf-8")
        assert "trust" not in "".join(l.split("#")[0] for l in hba.splitlines())
        with _login(port) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE rt_kept (v TEXT)")
            cur.execute("INSERT INTO rt_kept VALUES ('still here')")
        conn.close()
        assert first.stop()
        assert not first.is_ready() and not (data / "postmaster.pid").exists()

        # Adopt the same cluster on a different port (the port was taken by
        # something else, so the studio moved it).
        conf_before = (data / "postgresql.conf").read_text(encoding="utf-8").splitlines()
        new_port = _free_port()
        second = DatabaseEngine(str(data), port=new_port)
        engines.append(second)
        assert second.start()
        conf_after = (data / "postgresql.conf").read_text(encoding="utf-8").splitlines()
        changed = [(a, b) for a, b in zip(conf_before, conf_after) if a != b]
        assert changed and all("port" in a and "port" in b for a, b in changed), changed
        assert "port = %d" % new_port in conf_after
        with _login(new_port) as conn, conn.cursor() as cur:
            cur.execute("SELECT v FROM rt_kept")
            assert cur.fetchone()[0] == "still here"
        conn.close()
        assert second.stop()

        # Another program on the port: refused by name, nothing started.
        holder = socket.socket()
        holder.bind(("127.0.0.1", new_port))
        holder.listen(1)
        try:
            with pytest.raises(PortInUse):
                DatabaseEngine(str(data), port=new_port).start()
        finally:
            holder.close()

        # A postmaster.pid left by a crash (its process long gone) is cleared.
        (data / "postmaster.pid").write_text("999999\n%s\n" % data, encoding="utf-8")
        third = DatabaseEngine(str(data), port=new_port)
        engines.append(third)
        assert third.start() and third.is_ready()
    finally:
        for engine in reversed(engines):
            try:
                engine.stop()
            except Exception:
                pass
        db_credentials._cache = None


# ------------------------------------------------------------------ pool

@pytest.fixture
def pool(server, monkeypatch):
    from slate_server.core.pgbouncer_engine import PgBouncerEngine
    render = PgBouncerEngine._render_ini
    monkeypatch.setattr(PgBouncerEngine, "_render_ini",
                        lambda self: render(self).replace("listen_addr = *",
                                                          "listen_addr = 127.0.0.1"))
    engine = PgBouncerEngine(str(server.data), db_port=server.port,
                             listen_port=server.pooler_port, dbname=DBNAME,
                             db_user="ut_vfx_app", db_password=APP_PASSWORD)
    if not engine.is_installed():
        pytest.skip("PgBouncer is not bundled here")
    yield engine
    engine.stop()


def _pool_pid(engine) -> int:
    return int(engine.pid_path.read_text(encoding="utf-8").strip())


def _through_pool(server, statements):
    conn = psycopg2.connect(host="127.0.0.1", port=server.pooler_port, dbname=DBNAME,
                            user="ut_vfx_app", password=APP_PASSWORD, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            out = None
            for statement in statements:
                cur.execute(statement)
                if cur.description:
                    out = cur.fetchall()
        conn.commit()
        return out
    finally:
        conn.close()


def test_the_pool_starts_restarts_comes_back_and_stops(server, pool):
    from slate_server.core.server_facts import pool as pool_facts
    from slate_server.gui.app_window import DBWorker

    assert pool.start(psql_exe=BIN / "psql.exe")
    assert _through_pool(server, ["SELECT current_user, current_database()"]) == \
        [("ut_vfx_app", DBNAME)]
    facts = pool_facts(pool)
    assert facts["running"] and facts["agrees"] is True and facts["publishes"] == DBNAME

    # The Restart pool button: rewrite the config, restart, still usable.
    old_pid = _pool_pid(pool)
    said = []
    worker = DBWorker(SimpleNamespace(pooler=pool, bin_dir=BIN), "pool")
    worker.finished.connect(lambda ok, error: said.append((ok, error)))
    worker.run()
    assert said == [(True, "")], pool.log_path.read_text()[-2500:]
    assert _pool_pid(pool) != old_pid
    _through_pool(server, ["CREATE TABLE IF NOT EXISTS rt_pool (v INT)",
                           "INSERT INTO rt_pool VALUES (1)"])
    assert _through_pool(server, ["SELECT count(*) FROM rt_pool"])[0][0] >= 1

    # The pool died: the dashboard timer brings it back.
    subprocess.run(["taskkill", "/F", "/PID", str(_pool_pid(pool))], capture_output=True)
    for _ in range(20):
        if not pool.is_ready():
            break
        import time
        time.sleep(0.25)
    assert pool.ensure_running(psql_exe=BIN / "psql.exe")
    assert _through_pool(server, ["SELECT 1"]) == [(1,)]

    # Stop: the port is closed and the process is gone.
    pid = _pool_pid(pool)
    pool.stop()
    assert not pool.is_ready()
    listed = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                            capture_output=True, text=True).stdout.lower()
    assert "pgbouncer.exe" not in listed


# ------------------------------------------------------------ server_facts

def test_server_facts_against_a_real_cluster(server):
    from slate_server.core import server_facts
    lab = server

    facts = server_facts.cluster(lab.data, lab.port)
    assert facts["error"] == "", facts["error"]
    assert facts["initialised"] and DBNAME in facts["databases"]
    assert facts["database"] == DBNAME and facts["matches_config"] is True
    assert isinstance(facts["tables"], int) and facts["size_bytes"] > 0

    state = server_facts.schema_state(lab.port)
    assert state["error"] == "" and "ut_users" in state["missing"]

    # A workstation that opened a transaction and wandered off.
    artist = psycopg2.connect(host="127.0.0.1", port=lab.port, dbname=DBNAME,
                              user="ut_vfx_app", password=APP_PASSWORD,
                              application_name="rt-artist")
    try:
        with artist.cursor() as cur:
            cur.execute("SELECT 1")          # psycopg2 leaves the transaction open
        rows = [r for r in server_facts.sessions(lab.port) if r["application"] == "rt-artist"]
        assert len(rows) == 1 and rows[0]["state"] == "idle in transaction"
        assert rows[0]["user"] == "ut_vfx_app" and rows[0]["database"] == DBNAME

        ok, message = server_facts.terminate(lab.port, rows[0]["pid"])
        assert ok, message
        with pytest.raises(psycopg2.Error):
            with artist.cursor() as cur:
                cur.execute("SELECT 1")
        assert not [r for r in server_facts.sessions(lab.port)
                    if r["application"] == "rt-artist"]
    finally:
        artist.close()


def test_server_facts_say_what_a_wrong_password_means(server):
    from slate_server.core import db_credentials, server_facts
    lab = server
    original = lab.credentials.read_text(encoding="utf-8")
    try:
        lab.credentials.write_text(json.dumps({"db_password": "wrong", "db_name": DBNAME}),
                                   encoding="utf-8")
        db_credentials.reload()
        facts = server_facts.cluster(lab.data, lab.port)
        assert "rejected this server's password" in facts["error"], facts["error"]
    finally:
        lab.credentials.write_text(original, encoding="utf-8")
        db_credentials.reload()
