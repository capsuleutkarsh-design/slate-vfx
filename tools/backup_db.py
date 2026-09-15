
# The password is not in this file. It comes from the machine - either the
# SLATE_DB_PASSWORD environment variable or the git-ignored slate/config.json
# that setup.bat writes. This repository is public.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from slate.core.infra.local_secrets import db_password as _db_password

import os
import time
import subprocess
import glob
from datetime import datetime
import logging

# Everything here used to be a literal: one studio's backup drive, one
# machine's PostgreSQL install path, port 5432 and a database called "slate".
# The studio's database is on 5440 and is called ut_vfx, so the script as
# written backed up nothing, on a drive most studios do not have. It now asks
# the same settings the software uses.


def _settings():
    from slate.core.infra.local_secrets import db_settings
    return db_settings()


def _backup_dir():
    override = os.environ.get("SLATE_BACKUP_DIR")
    if override:
        return override
    try:
        from slate.core.infra.global_config import GlobalConfig
        root = _Path(str(GlobalConfig.server_root()))
        if str(root):
            return str(root / "Backups")
    except Exception:
        pass
    # Somewhere that exists on this machine, rather than a drive letter that
    # might not.
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "Slate", "Backups", "Database")


def _pg_dump():
    override = os.environ.get("SLATE_PG_DUMP")
    if override:
        return override
    # The copy the server ships with, which is the one that matches the cluster.
    bundled = (_Path(__file__).resolve().parents[1] / "slate_server" / "bin"
               / "pgsql" / "bin" / "pg_dump.exe")
    if bundled.exists():
        return str(bundled)
    return "pg_dump"


_DB = _settings()
BACKUP_DIR = _backup_dir()
PG_DUMP_PATH = _pg_dump()
DB_HOST = _DB["host"]
DB_PORT = str(_DB["port"])
DB_USER = _DB["user"]
DB_NAME = _DB["dbname"]
# Note: PGPASSWORD environment variable is safer than passing via command line,
# but for this script we will set it in the env dict for the subprocess.
DB_PASS = _db_password(required=False) or _DB["password"]

RETENTION_DAYS = int(os.environ.get("SLATE_BACKUP_RETENTION_DAYS", "30"))

def backup_database():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"slate_backup_{timestamp}.sql"
    filepath = os.path.join(BACKUP_DIR, filename)

    # Ensure backup directory exists
    if not os.path.exists(BACKUP_DIR):
        try:
            os.makedirs(BACKUP_DIR)
        except Exception as e:
            print(f"Error creating backup directory: {e}")
            return

    print(f"Starting backup of '{DB_NAME}' to: {filepath}")

    # Set password in environment
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASS

    cmd = [
        PG_DUMP_PATH,
        "-h", DB_HOST,
        "-p", DB_PORT,
        "-U", DB_USER,
        "-F", "c", # Custom format (compressed)
        "-b",      # Include large objects
        "-v",      # Verbose
        "-f", filepath,
        DB_NAME
    ]

    try:
        subprocess.run(cmd, env=env, check=True)
        print("Backup SUCCESSFUL.")
        prune_old_backups()
    except subprocess.CalledProcessError as e:
        print(f"Backup FAILED: {e}")

def prune_old_backups():
    """Remove backups older than RETENTION_DAYS"""
    print(f"Checking for backups older than {RETENTION_DAYS} days...")
    cutoff_time = time.time() - (RETENTION_DAYS * 86400)
    
    # List all .sql (or custom format) files
    files = glob.glob(os.path.join(BACKUP_DIR, "slate_backup_*"))
    
    deleted_count = 0
    for f in files:
        if os.path.getmtime(f) < cutoff_time:
            try:
                os.remove(f)
                print(f"Deleted old backup: {f}")
                deleted_count += 1
            except Exception as e:
                print(f"Could not delete {f}: {e}")
                
    if deleted_count == 0:
        print("No old backups to prune.")

if __name__ == "__main__":
    backup_database()
