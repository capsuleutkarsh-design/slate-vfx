"""
Automatic snapshots before any security change, and one-click restore.

    from slate_server.core.recovery.snapshots import before_security_change
    folder = before_security_change("strict_pg_hba")      # BEFORE touching anything

Every hardening step calls this first. It copies, into
<data folder's parent>\\slate_recovery\\snapshots\\NNNN_<time>_<name>\\:

    pg_hba.conf, postgresql.conf, postgresql.auto.conf     (the database's rules)
    slate_server_config.json, slate_server_last_good.json  (the server's settings)
    the config.json the server takes its passwords from
    pgbouncer.ini and userlist.txt
    security_switches.json                                 (switches forced off here)
    auth_tables.dump    pg_dump -Fc of ut_users, ut_roles, ut_role_seeds and
                        security_switches - for a full manual restore
    accounts.json       the same rows, which "restore accounts" merges back

and writes manifest.json with what was taken, its size and SHA-256, and what
could not be taken and why. A snapshot that cannot reach the database still
copies every file - it never fails because the database is down.

The folder is readable only by administrators and the server's account (it
holds password hashes and the settings file). "Latest" means the highest
number, not the newest clock time, so a wrong clock cannot reorder them.

restore_snapshot() puts the files back (after taking one more snapshot of the
current state, so a restore can itself be undone) and, if asked, merges the
accounts back in: users and roles from the snapshot are written back; accounts
made since are left alone.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import fs

logger = logging.getLogger(__name__)

AUTH_TABLES = ("ut_users", "ut_roles", "ut_role_seeds", "security_switches")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _sources(layout) -> dict:
    """{role: path} of every file a snapshot keeps."""
    found = {
        "pg_hba.conf": layout.pg_hba,
        "postgresql.conf": layout.pg_conf,
        "postgresql.auto.conf": layout.data_dir / "postgresql.auto.conf",
        "pgbouncer.ini": layout.pgbouncer_dir / "pgbouncer.ini",
        "userlist.txt": layout.pgbouncer_dir / "userlist.txt",
        "security_switches.json": layout.switches_file,
    }
    if layout.settings_path:
        found["slate_server_config.json"] = Path(layout.settings_path)
    if layout.server_home:
        from slate_server.core.server_home import LAST_GOOD_NAME
        found["slate_server_last_good.json"] = Path(layout.server_home) / LAST_GOOD_NAME
    if layout.credentials_path:
        found["credentials config.json"] = Path(layout.credentials_path)
    return found


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "change")).strip("-")[:40] or "change"


def list_snapshots(layout) -> List[Path]:
    base = layout.snapshots_dir
    if not base.exists():
        return []
    items = []
    for child in base.iterdir():
        match = re.match(r"^(\d+)_", child.name)
        if child.is_dir() and match and (child / "manifest.json").exists():
            items.append((int(match.group(1)), child))
    return [path for _, path in sorted(items)]


UNDO_PREFIX = "before-restore-of-"


def latest_snapshot(layout, include_undo: bool = False) -> Optional[Path]:
    """
    The newest snapshot by number. The copies a restore takes of what it is
    about to replace are skipped unless asked for, so pressing "restore the
    last snapshot" twice does not flip back and forth.
    """
    items = [p for p in list_snapshots(layout)
             if include_undo or UNDO_PREFIX not in p.name]
    return items[-1] if items else None


def read_manifest(folder) -> dict:
    return fs.read_json(Path(folder) / "manifest.json", {}) or {}


def _default_db(layout):
    """A connection with the server's own configured login, or None."""
    try:
        import psycopg2
        from slate_server.core.db_credentials import connect_kwargs
        from slate.core.security.dbapi import ConnectionDB
        conn = psycopg2.connect(**connect_kwargs(layout.port, connect_timeout=4))
        return ConnectionDB(conn)
    except Exception as exc:
        logger.debug("Snapshot: no database connection (%s)", exc)
        return None


def _pg_dump(layout, target: Path, user: str, password: Optional[str], dbname: str,
             tables) -> str:
    exe = layout.bin_dir / ("pg_dump.exe" if sys.platform == "win32" else "pg_dump")
    if not exe.exists():
        return "pg_dump is not on this PC"
    env = dict(os.environ)
    env.pop("PGPASSWORD", None)
    if password:
        env["PGPASSWORD"] = password
    cmd = [str(exe), "-h", "127.0.0.1", "-p", str(layout.port), "-U", user, "-d", dbname,
           "-Fc", "-f", str(target), "--no-password"]
    for table in tables:
        cmd += ["-t", "public.%s" % table]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        return str(exc)
    if out.returncode != 0:
        return (out.stderr or "pg_dump failed").strip().splitlines()[-1][:300]
    return ""


def before_security_change(name: str, layout=None, *, db=None, dump_login=None,
                           take_dump: bool = True) -> Path:
    """
    Snapshot everything a security change could break. Call it BEFORE the change.

    db:          a connection (DatabaseManager / ConnectionDB) for accounts.json;
                 by default the server's own login is tried.
    dump_login:  (user, password) for pg_dump; by default the server's own
                 (postgres + the configured password). Pass (\"postgres\", None)
                 inside a trust window.
    Returns the snapshot folder. Never raises because the database is down.
    """
    if layout is None:
        from .layout import find_layout
        layout = find_layout()
    fs.ensure_private_dir(layout.recovery_dir)
    fs.ensure_private_dir(layout.snapshots_dir)
    existing = list_snapshots(layout)
    seq = (int(existing[-1].name.split("_", 1)[0]) + 1) if existing else 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = layout.snapshots_dir / ("%04d_%s_%s" % (seq, stamp, _slug(name)))
    folder.mkdir(parents=True)

    manifest = {
        "name": str(name), "seq": seq,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "data_dir": str(layout.data_dir), "port": int(layout.port),
        "files": [], "missing": [], "dump": {}, "accounts": {},
    }
    try:
        manifest["created_by"] = getpass.getuser()
    except Exception:
        manifest["created_by"] = ""

    for role, source in _sources(layout).items():
        source = Path(source)
        if not source.is_file():
            manifest["missing"].append({"role": role, "source": str(source)})
            continue
        stored = folder / role.replace(" ", "_")
        shutil.copy2(source, stored)
        if role == "pg_hba.conf":
            # Taken while a trust window is open, the live file is the
            # temporary rule. The snapshot keeps the real one, from the marker.
            from . import marker
            open_window = marker.read_marker(layout.data_dir)
            if open_window and open_window.get("original") is not None:
                with open(stored, "w", encoding="utf-8", newline="") as handle:
                    handle.write(open_window["original"])
        manifest["files"].append({"role": role, "source": str(source), "stored": stored.name,
                                  "sha256": _sha256(stored), "size": stored.stat().st_size})

    from slate_server.core.db_credentials import admin_password, admin_user, database_name
    dbname = database_name()
    own_db = db is None
    db = db or _default_db(layout)
    if db is not None:
        try:
            rows = {}
            for table in AUTH_TABLES:
                try:
                    rows[table] = [{k: (v if isinstance(v, (str, int, float, type(None))) else str(v))
                                    for k, v in dict(r).items()}
                                   for r in (db.execute_query("SELECT * FROM %s" % table,
                                                              fetch="all") or [])]
                except Exception:
                    continue
            fs.write_json(folder / "accounts.json", rows)
            manifest["accounts"] = {"file": "accounts.json",
                                    "tables": {t: len(v) for t, v in rows.items()}}
        except Exception as exc:
            manifest["accounts"] = {"error": str(exc).splitlines()[0][:300]}
        finally:
            if own_db:
                try:
                    db.conn.close()
                except Exception:
                    pass
    else:
        manifest["accounts"] = {"error": "the database could not be reached"}

    if take_dump:
        user, password = dump_login if dump_login else (admin_user(), admin_password())
        error = _pg_dump(layout, folder / "auth_tables.dump", user, password, dbname,
                         AUTH_TABLES)
        manifest["dump"] = ({"file": "auth_tables.dump", "tables": list(AUTH_TABLES)}
                            if not error else {"error": error})
    else:
        manifest["dump"] = {"error": "not taken"}

    fs.write_json(folder / "manifest.json", manifest)
    logger.warning("Security snapshot %s taken before %r.", folder.name, name)
    prune(layout)
    return folder


KEEP = 100


def prune(layout, keep: int = KEEP) -> int:
    """Keep the newest ``keep`` snapshots (by number) and the very first one."""
    items = list_snapshots(layout)
    if len(items) <= keep + 1:
        return 0
    removed = 0
    for folder in items[1:len(items) - keep]:
        try:
            shutil.rmtree(folder)
            removed += 1
        except OSError as exc:
            logger.debug("Old snapshot %s not removed: %s", folder.name, exc)
    return removed


# ------------------------------------------------------------------ restore

def _current_path(layout, role, recorded):
    return _sources(layout).get(role, Path(recorded))


def restore_snapshot(layout, snapshot=None, *, accounts: bool = False, db=None,
                     say=None) -> List[str]:
    """
    Put a snapshot back (default: the latest). Returns what was done, line by line.

    The current state is snapshotted first, so this can be undone the same way.
    accounts=True also merges the users and roles back - give ``db`` (inside a
    trust window: ConnectionDB(window.connect())).
    """
    say = say or (lambda message: None)
    folder = Path(snapshot) if snapshot else latest_snapshot(layout)
    if folder is None or not (folder / "manifest.json").exists():
        raise FileNotFoundError("There is no snapshot to restore yet.")
    manifest = read_manifest(folder)
    done = []

    undo = before_security_change(UNDO_PREFIX + folder.name.split("_", 1)[0],
                                  layout, db=db, take_dump=False)
    done.append("The current state was kept first as %s." % undo.name)

    for entry in manifest.get("files", []):
        stored = folder / entry["stored"]
        if not stored.exists() or _sha256(stored) != entry.get("sha256"):
            done.append("Skipped %s: the copy in the snapshot is missing or damaged."
                        % entry["role"])
            continue
        target = Path(_current_path(layout, entry["role"], entry["source"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".restoring")
        shutil.copy2(stored, tmp)
        os.replace(tmp, target)
        done.append("Restored %s." % entry["role"])

    try:
        from slate_server.core import db_credentials
        db_credentials.reload()
    except Exception:
        pass
    try:
        from slate.core.security import switches
        switches.reset_cache()
    except Exception:
        pass

    engine = layout.engine()
    if engine.is_ready():
        engine._reload_configuration()
        done.append("The database re-read its access rules. Changes to postgresql.conf "
                    "other than access need the server restarted.")

    if accounts:
        done += restore_accounts(folder, db)
    for line in done:
        say(line)
    logger.warning("Snapshot %s restored (%d steps).", folder.name, len(done))
    return done


def restore_accounts(folder, db) -> List[str]:
    """Merge users and roles from a snapshot back. Accounts made since are kept."""
    if db is None:
        return ["Accounts were not restored: no database connection was given."]
    rows = fs.read_json(Path(folder) / "accounts.json")
    if not isinstance(rows, dict):
        return ["Accounts were not restored: the snapshot has no accounts.json."]
    done = []
    keys = {"ut_users": "username", "ut_roles": "role_name", "ut_role_seeds": "role_name",
            "security_switches": "name"}
    for table, key in keys.items():
        items = rows.get(table) or []
        if not items:
            continue
        try:
            columns = {r["column_name"] for r in (db.execute_query(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s", (table,), fetch="all") or [])}
        except Exception:
            columns = set()
        if not columns:
            done.append("%s does not exist in the database now; skipped." % table)
            continue
        count = 0
        for item in items:
            values = {k: v for k, v in item.items() if k in columns and k != "id"}
            if key not in values:
                continue
            names = list(values)
            there = db.execute_query("SELECT 1 AS x FROM %s WHERE %s=%%s" % (table, key),
                                     (values[key],), fetch="one")
            if there:
                sets = [n for n in names if n != key]
                if sets:
                    db.execute_update("UPDATE %s SET %s WHERE %s=%%s" % (
                        table, ", ".join("%s=%%s" % n for n in sets), key),
                        tuple(values[n] for n in sets) + (values[key],))
            else:
                db.execute_update("INSERT INTO %s (%s) VALUES (%s)" % (
                    table, ", ".join(names), ", ".join(["%s"] * len(names))),
                    tuple(values[n] for n in names))
            count += 1
        done.append("Restored %d row(s) of %s." % (count, table))
    return done
