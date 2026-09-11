
# The password is not in this file. It comes from the machine - either the
# SLATE_DB_PASSWORD environment variable or the git-ignored ut_vfx/config.json
# that setup.bat writes. This repository is public.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from ut_vfx.core.infra.local_secrets import db_password as _db_password

import os
import time
import subprocess
import glob
from datetime import datetime
import logging

# Configuration
BACKUP_DIR = r"X:\Extra\UT_Central\Backups"
PG_DUMP_PATH = r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"
DB_HOST = "127.0.0.1"
DB_PORT = "5432"
DB_USER = "postgres"
DB_NAME = "ut_vfx"
# Note: PGPASSWORD environment variable is safer than passing via command line,
# but for this script we will set it in the env dict for the subprocess.
DB_PASS = _db_password()

RETENTION_DAYS = 30

def backup_database():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"ut_vfx_backup_{timestamp}.sql"
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
    files = glob.glob(os.path.join(BACKUP_DIR, "ut_vfx_backup_*"))
    
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
