"""
Backups with the real pg_dump / pg_restore against a throwaway cluster.

    server: BackupEngine     (slate_server/core/backup_engine.py)
    workstation: take_backup (slate/core/domain/backup_service.py)

A backup counts only if it can be restored, so every dump here is restored into
a new database and compared row for row.
"""

import json
import sys
import os
import string
import time
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
import pytest

from tests.realtools.conftest import APP_PASSWORD, BIN, DBNAME

pytestmark = [pytest.mark.realtools,
              pytest.mark.skipif(not (BIN / "pg_dump.exe").exists(),
                                 reason="the bundled pg_dump is not here")]


def _seed(lab, table="rt_backup_rows"):
    """A table the app role owns (as Slate's are), with a sequence, jsonb and a trigger."""
    conn = lab.connect("ut_vfx_app", APP_PASSWORD)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE TABLE {table} (id SERIAL PRIMARY KEY, name TEXT, "
                    "data JSONB, touched TEXT)")
        cur.execute("CREATE OR REPLACE FUNCTION rt_touch() RETURNS trigger AS $$ "
                    "BEGIN NEW.touched := 'yes'; RETURN NEW; END $$ LANGUAGE plpgsql")
        cur.execute(f"DROP TRIGGER IF EXISTS rt_touch ON {table}")
        cur.execute(f"CREATE TRIGGER rt_touch BEFORE INSERT ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION rt_touch()")
        cur.executemany(f"INSERT INTO {table} (name, data) VALUES (%s, %s)",
                        [("shot_%04d" % i, json.dumps({"frame": 1000 + i, "é": "ü"}))
                         for i in range(250)])
    conn.close()


def _rows(lab, dbname, user="postgres", password=APP_PASSWORD, table="rt_backup_rows"):
    conn = lab.connect(user, password, dbname)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, name, data, touched FROM {table} ORDER BY id")
            return cur.fetchall()
    finally:
        conn.close()


def _fresh_db(lab, name):
    lab.sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    lab.sql(f'CREATE DATABASE "{name}" OWNER ut_vfx_app')


def _engine(lab, folder, dbname=DBNAME):
    from slate_server.core.backup_engine import BackupEngine
    return BackupEngine(BIN, folder, port=lab.port, dbname=dbname)


# ------------------------------------------------------------------ server

def test_server_backup_restores_into_a_new_database_row_for_row(server, tmp_path):
    lab = server
    _seed(lab)
    folder = tmp_path / "Backups"
    result = _engine(lab, folder).back_up()
    assert result["ok"], result["message"]
    dump = Path(result["path"])
    assert dump.exists() and dump.stat().st_size > 0
    assert not list(folder.glob("*.partial")), "no half-written file may be left"
    assert _engine(lab, folder).latest()["path"] == dump

    # Refused without the explicit yes, and nothing is touched.
    _fresh_db(lab, "slate_rt_restored")
    refused = _engine(lab, folder, "slate_rt_restored").restore(dump)
    assert not refused["ok"] and "did not confirm" in refused["message"]

    done = _engine(lab, folder, "slate_rt_restored").restore(dump, confirm_overwrite=True)
    assert done["ok"], done["message"]
    assert _rows(lab, "slate_rt_restored") == _rows(lab, DBNAME)
    lab.sql('DROP DATABASE IF EXISTS "slate_rt_restored" WITH (FORCE)')


def test_after_a_server_restore_the_workstations_account_can_still_read_and_write(
        server, tmp_path):
    """
    Restoring is done as the superuser. Workstations log in as ut_vfx_app, so
    what comes back has to belong to it - or every screen says "permission
    denied" until somebody restarts the server.
    """
    lab = server
    _seed(lab)
    folder = tmp_path / "Backups"
    dump = _engine(lab, folder).back_up()["path"]

    # Restored over the live database, as the Operations screen does it.
    lab.sql("UPDATE rt_backup_rows SET name = 'changed after the backup'")
    done = _engine(lab, folder).restore(dump, confirm_overwrite=True)
    assert done["ok"], done["message"]

    rows = _rows(lab, DBNAME, user="ut_vfx_app")
    assert len(rows) == 250 and rows[0][1] == "shot_0000"
    conn = lab.connect("ut_vfx_app", APP_PASSWORD)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("INSERT INTO rt_backup_rows (name) VALUES ('after restore') "
                    "RETURNING id, touched")
        new_id, touched = cur.fetchone()
        assert new_id == 251 and touched == "yes", "sequence and trigger came back"
        # Slate's own migrations replace functions at start-up.
        cur.execute("CREATE OR REPLACE FUNCTION rt_touch() RETURNS trigger AS $$ "
                    "BEGIN NEW.touched := 'again'; RETURN NEW; END $$ LANGUAGE plpgsql")
    conn.close()


def test_server_backup_with_a_wrong_or_missing_password_fails_and_leaves_nothing(
        server, tmp_path, monkeypatch):
    from slate_server.core import db_credentials
    lab = server
    folder = tmp_path / "Backups"
    # The settings file decides, not the passwords the server keeps protected.
    monkeypatch.setattr(db_credentials, "_secrets_path", tmp_path / "no_secrets.dat")
    original = lab.credentials.read_text(encoding="utf-8")
    try:
        for settings in ({"db_password": "not-the-password", "db_name": DBNAME},
                         {"db_name": DBNAME}):
            lab.credentials.write_text(json.dumps(settings), encoding="utf-8")
            db_credentials.reload()
            started = time.monotonic()
            result = _engine(lab, folder).back_up()
            assert not result["ok"]
            assert "password" in result["message"].lower(), result["message"]
            assert time.monotonic() - started < 15, "it must not sit waiting for a prompt"
            assert not list(folder.glob("*")), "no dump and no partial file"
    finally:
        lab.credentials.write_text(original, encoding="utf-8")
        db_credentials.reload()


def _unused_drive():
    for letter in reversed(string.ascii_uppercase):
        if not os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\"
    return None


def test_server_backup_to_an_unusable_folder_says_so(server, tmp_path):
    lab = server
    blocker = tmp_path / "a_file"
    blocker.write_text("not a folder")
    result = _engine(lab, blocker / "Backups").back_up()
    assert not result["ok"] and "Cannot write" in result["message"]

    drive = _unused_drive()
    if drive:
        result = _engine(lab, Path(drive) / "Backups").back_up()
        assert not result["ok"] and "Cannot write" in result["message"]


def test_server_backup_into_a_read_only_folder_fails_cleanly(server, tmp_path):
    """pg_dump itself cannot create the file: a deny-write ACL on a sandbox folder."""
    import getpass
    import subprocess
    lab = server
    folder = tmp_path / "ro"
    folder.mkdir()
    user = getpass.getuser()
    deny = subprocess.run(["icacls", str(folder), "/deny", f"{user}:(W,AD)"],
                          capture_output=True, text=True)
    if deny.returncode != 0:
        pytest.skip("could not make a read-only folder here: %s" % deny.stderr)
    try:
        result = _engine(lab, folder).back_up()
        assert not result["ok"], result["message"]
        assert "pg_dump failed" in result["message"] or "Cannot write" in result["message"]
    finally:
        subprocess.run(["icacls", str(folder), "/remove:d", user], capture_output=True)
    assert not list(folder.glob("*"))


def test_server_prune_keeps_the_newest_and_removes_only_the_old(tmp_path):
    from slate_server.core.backup_engine import BackupEngine, NAME_PATTERN
    folder = tmp_path / "Backups"
    folder.mkdir()
    now = datetime.now()
    for days in (0, 1, 2, 40, 41, 42, 43, 44, 45, 46, 60):
        stamp = (now - timedelta(days=days)).strftime("%Y%m%d_%H%M%S")
        (folder / (NAME_PATTERN % {"database": "slate_lab", "stamp": stamp})).write_bytes(b"x")
    (folder / "someone_elses_file.txt").write_text("left alone")
    engine = BackupEngine(BIN, folder, dbname="slate_lab")

    preview = engine.prune(keep_days=30, keep_at_least=7, apply=False)
    assert len(list(folder.glob("*.dump"))) == 11, "a preview deletes nothing"
    removed = engine.prune(keep_days=30, keep_at_least=7, apply=True)
    assert [r["name"] for r in removed] == [r["name"] for r in preview]
    assert len(removed) == 4 and all(r["age_days"] > 30 for r in removed)
    assert len(list(folder.glob("*.dump"))) == 7
    assert (folder / "someone_elses_file.txt").exists()

    # A server switched off for months: everything is "old", the floor still holds.
    assert engine.prune(keep_days=0, keep_at_least=7, apply=True) == []
    assert len(engine.backups()) == 7


# ------------------------------------------------------------ workstation

@pytest.fixture
def workstation_config(server, tmp_path, monkeypatch):
    """The workstation's config.json, pointing at the throwaway server."""
    from slate.core.infra import local_secrets
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"db_host": "127.0.0.1", "db_port": server.port,
                                  "db_name": DBNAME, "db_user": "ut_vfx_app",
                                  "db_password": APP_PASSWORD}), encoding="utf-8")
    monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([config]))
    # The same file is this workstation's settings, as it is for the client
    # itself, and it has no keyring entry (find_db_password reads both).
    from slate.core.infra.global_config import GlobalConfig
    monkeypatch.setattr(GlobalConfig, "get", classmethod(
        lambda cls, key, default=None: json.loads(config.read_text(encoding="utf-8")).get(key, default)))
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.delenv("SLATE_DB_PASSWORD", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    recorded = []
    from slate.core.domain import backup_service
    monkeypatch.setattr(backup_service, "_record", lambda ok, msg: recorded.append((ok, msg)))
    return config, recorded


def test_workstation_backup_is_a_dump_the_server_can_restore(server, tmp_path,
                                                             workstation_config):
    from slate.core.domain.backup_service import find_pg_dump, take_backup
    lab = server
    _seed(lab)
    _config, recorded = workstation_config
    pg_dump = find_pg_dump()
    assert pg_dump is not None

    target = take_backup(pg_dump, tmp_path / "Workstation")
    assert target is not None and target.exists(), recorded
    assert recorded[-1][0] is True
    assert not list(target.parent.glob("*.partial"))

    _fresh_db(lab, "slate_rt_ws_restored")
    done = _engine(lab, tmp_path, "slate_rt_ws_restored").restore(target, confirm_overwrite=True)
    assert done["ok"], done["message"]
    assert _rows(lab, "slate_rt_ws_restored") == _rows(lab, DBNAME)
    lab.sql('DROP DATABASE IF EXISTS "slate_rt_ws_restored" WITH (FORCE)')


def test_workstation_backup_without_or_with_a_wrong_password(server, tmp_path,
                                                             workstation_config):
    from slate.core.domain.backup_service import find_pg_dump, take_backup
    config, recorded = workstation_config
    settings = json.loads(config.read_text(encoding="utf-8"))

    settings["db_password"] = ""
    config.write_text(json.dumps(settings), encoding="utf-8")
    assert take_backup(find_pg_dump(), tmp_path / "W1") is None
    assert recorded[-1][0] is False and "no database password" in recorded[-1][1]
    assert not (tmp_path / "W1").exists() or not list((tmp_path / "W1").iterdir())

    settings["db_password"] = "wrong"
    config.write_text(json.dumps(settings), encoding="utf-8")
    started = time.monotonic()
    assert take_backup(find_pg_dump(), tmp_path / "W2") is None
    assert time.monotonic() - started < 15
    assert recorded[-1][0] is False and "password" in recorded[-1][1].lower()
    assert not list((tmp_path / "W2").glob("*")), "no dump and no partial file"


def test_workstation_backup_folder_and_pruning_on_real_files(share, tmp_path, monkeypatch):
    from slate.core.domain import backup_service
    monkeypatch.delenv("SLATE_BACKUP_DIR", raising=False)
    assert backup_service.backup_dir() == share / "Backups" / "Workstation"

    folder = tmp_path / "W"
    folder.mkdir()
    old = folder / "slate_backup_2026-01-01_00-00-00.dump"
    new = folder / "slate_backup_2026-10-01_00-00-00.dump"
    other = folder / "notes.txt"
    for path in (old, new, other):
        path.write_bytes(b"x")
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    assert backup_service.prune(folder, keep_days=30) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_the_workstation_backup_thread_records_a_backup_it_could_not_take(tmp_path,
                                                                          monkeypatch):
    """The scheduled path: one round of the thread, into a folder it cannot create."""
    from slate.core.domain import backup_service
    recorded = []
    monkeypatch.setattr(backup_service, "_record", lambda ok, msg: recorded.append((ok, msg)))
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("SLATE_BACKUP_DIR", str(blocker / "Backups"))
    from slate.core.infra import local_secrets
    monkeypatch.setattr(local_secrets, "db_settings",
                        lambda: {"host": "127.0.0.1", "port": 1, "dbname": "x",
                                 "user": "x", "password": "x"})
    thread = backup_service.AutoBackupThread()
    thread.pg_dump = Path(BIN / "pg_dump.exe")
    thread._run_backup()
    assert recorded and recorded[-1][0] is False
