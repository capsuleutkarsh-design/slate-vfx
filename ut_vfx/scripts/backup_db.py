import os
import sys
import subprocess
import logging
from pathlib import Path
from datetime import datetime

# Setup paths to import core modules
current_dir = Path(__file__).resolve().parent
ut_vfx_root = current_dir.parent.parent
if str(ut_vfx_root) not in sys.path:
    sys.path.insert(0, str(ut_vfx_root))

from ut_vfx.core.infra.global_config import GlobalConfig

def get_db_password():
    # Try GlobalConfig first
    config_password = GlobalConfig.get('db_password') or GlobalConfig.get('password')
    if config_password and str(config_password).strip():
        return config_password
    
    # Try Keyring
    try:
        import keyring
        pwd = keyring.get_password("UTVFX", "db_password")
        if pwd: return pwd
    except Exception:
        pass
    
    return None

def find_pg_dump():
    # Common installation paths for PostgreSQL on Windows
    common_paths = [
        r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\14\bin\pg_dump.exe",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return None

def run_backup():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    config = GlobalConfig.get('db_config', {}) or {}
    
    host = config.get('host') or GlobalConfig.get('db_host') or "127.0.0.1"
    port = config.get('port') or GlobalConfig.get('db_port') or 5440
    dbname = config.get('name') or GlobalConfig.get('db_name') or "ut_vfx"
    user = config.get('user') or GlobalConfig.get('db_user') or "postgres"
    password = get_db_password()
    
    if not password:
        logging.error("Could not find database password in config or keyring.")
        return False
        
    pg_dump_path = find_pg_dump()
    if not pg_dump_path:
        logging.error("pg_dump.exe not found. Is PostgreSQL installed?")
        return False
        
    # Store backups on the server root (so it's safe on the network drive if mapped)
    backup_dir = GlobalConfig.server_root() / "Backups" / "Database"
    
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # Fallback to local app data if network drive is missing
        logging.warning(f"Could not create backup directory at {backup_dir}: {e}. Falling back to local data.")
        backup_dir = Path(os.getenv('LOCALAPPDATA', 'C:/')) / "UTVFX" / "Backups" / "Database"
        backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"{dbname}_backup_{timestamp}.sql"
    
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    
    hosts_to_try = [host]
    if host != "127.0.0.1" and host != "localhost":
        hosts_to_try.append("127.0.0.1")

    success = False
    for attempt_host in hosts_to_try:
        cmd = [
            pg_dump_path,
            "-h", str(attempt_host),
            "-p", str(port),
            "-U", str(user),
            "-d", str(dbname),
            "-F", "c", # custom format, compressed and good for pg_restore
            "-f", str(backup_file)
        ]
        
        logging.info(f"Starting backup of database '{dbname}' at {attempt_host} to {backup_file}")
        
        try:
            result = subprocess.run(cmd, env=env, check=True, capture_output=True)
            logging.info(f"Backup completed successfully from {attempt_host}! Size: {backup_file.stat().st_size / 1024 / 1024:.2f} MB")
            success = True
            break
        except subprocess.CalledProcessError as e:
            logging.error(f"Backup failed on {attempt_host}: {e.stderr.decode()}")
            
    if not success:
        logging.warning("PostgreSQL backup failed. Attempting SQLite fallback backup if it exists...")
        sqlite_db = Path(os.getenv('LOCALAPPDATA', 'C:/')) / "UTVFX" / "ut_vfx_local.db"
        if sqlite_db.exists():
            import shutil
            sqlite_backup = backup_dir / f"sqlite_backup_{timestamp}.db"
            try:
                shutil.copy2(sqlite_db, sqlite_backup)
                logging.info(f"SQLite fallback backup completed successfully! Size: {sqlite_backup.stat().st_size / 1024 / 1024:.2f} MB")
                success = True
            except Exception as e:
                logging.error(f"SQLite fallback backup failed: {e}")
        else:
            logging.error(f"No SQLite fallback database found at {sqlite_db}")
            return False
            
    # Cleanup old backups (keep last 14)
    try:
        backups = sorted(backup_dir.glob(f"*_backup_*.sql")) + sorted(backup_dir.glob(f"*_backup_*.db"))
        if len(backups) > 14:
            for old_backup in backups[:-14]:
                old_backup.unlink()
                logging.info(f"Deleted old backup: {old_backup.name}")
    except Exception as e:
        logging.error(f"Failed during cleanup of old backups: {e}")
                
    return success

if __name__ == "__main__":
    run_backup()
