import sys
import os
import logging
import platform
import subprocess
import socket
from pathlib import Path

# Setup path so we can import internal modules
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

def print_header(title):
    print(f"\n{'-'*60}")
    print(f" {title.upper()}")
    print(f"{'-'*60}")

def check_ping(host):
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, '1', host]
    return subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0

def check_network():
    print_header("Network Connectivity")
    
    # Check Server Root
    from ut_vfx.core.infra.global_config import GlobalConfig
    server_root = GlobalConfig.server_root()
    
    if server_root.exists():
        print(f"[OK] Server Drive Accessible: {server_root}")
    else:
        print(f"[FAIL] Server Drive NOT Accessible: {server_root}")
        print("      (Check VPN or mapped drive X:)")

    # Check Database Host Ping
    # Try to extract host from PostgresManager (via DatabaseManager)
    try:
        from ut_vfx.core.infra.database_manager import database_manager
        pm = database_manager.backend
        db_host = pm.host
        
        print(f"[-] Pinging Database Host: {db_host}...")
        if check_ping(db_host):
            print(f"[OK] Database Host ({db_host}) is online.")
        else:
            print(f"[FAIL] Database Host ({db_host}) is unreachable!")
            
    except Exception as e:
        print(f"[WARN] Could not determine DB host path: {e}")

def check_database():
    print_header("Database Integrity")
    try:
        from ut_vfx.core.infra.database_manager import database_manager
        
        # Test Connection (This will trigger Lazy Load - wait, lazy load might show GUI?)
        # IMPORTANT: If we run this as a script without QApplication, Lazy Load *might* fail if password isn't in env/keyring!
        # But wait, verify_startup_safety.py showed it DID NOT crash, but password was None.
        # So we need to enable CLI password input? Or assume it's set?
        # Let's try to connect. If it fails due to password, we'll catch it.
        
        print("[-] Connecting to PostgreSQL...")
        with database_manager.get_connection() as conn:
            with conn.cursor() as cur:
                # Query version
                cur.execute("SELECT version();")
                ver = cur.fetchone()
                print(f"[OK] Connection Successful: {ver[0][:50]}...")
                
                # Count Projects
                cur.execute("SELECT COUNT(*) FROM projects")
                count = cur.fetchone()
                print(f"[OK] Projects Table Accessible: {count[0]} projects found.")
            
    except Exception as e:
        print(f"[FAIL] Database Check Failed: {e}")
        print("      (Try running 'python scripts/setup_credentials.py' if this is an Auth error)")

def check_ai_server():
    print_header("AI Server Status")
    
    from ut_vfx.core.infra.global_config import GlobalConfig
    server_root = GlobalConfig.server_root()
    ai_models_path = server_root / "Cache" / "AI_Models"
    
    if ai_models_path.exists():
        # Check for any cached model directories (legacy checks retained for diagnostics)
        found_models = [d.name for d in ai_models_path.iterdir() if d.is_dir()]
        
        if found_models:
            print(f"[OK] Central Model Cache Found: {ai_models_path}")
            print(f"     Models detected: {len(found_models)} ({found_models[0]}...)")
        else:
            print(f"[WARN] Central Model Cache Found but NO models detected inside.")
            print(f"       Contents: {[x.name for x in ai_models_path.iterdir()]}")
        
        # Check logs for recent activity?
        logs_dir = server_root / "Logs"
        if logs_dir.exists():
            print(f"[OK] Server Logs Directory Exists: {logs_dir}")
        else:
            print(f"[WARN] Server Logs Directory Missing: {logs_dir}")
            
    else:
        print(f"[FAIL] Central Model Cache Missing: {ai_models_path}")
        print("      (AI server tooling is retired in this build.)")


def check_attendance():
    print_header("Attendance System")
    
    from ut_vfx.core.infra.global_config import GlobalConfig
    server_root = GlobalConfig.server_root()
    attendance_dir = server_root / "Attendance"
    
    if attendance_dir.exists():
        print(f"[OK] Attendance Log Directory Found: {attendance_dir}")
        
        # Check for Month Files
        logs = list(attendance_dir.glob("Attendance_*.json"))
        if logs:
            print(f"[OK] Found {len(logs)} attendance log files.")
            print(f"     Latest: {logs[-1].name}")
        else:
            print(f"[WARN] No attendance logs found yet (New System?). Directory exists though.")
            
        # Test Permissions (Dry Run)
        try:
            test_file = attendance_dir / ".write_test"
            with open(test_file, 'w') as f: f.write("test")
            os.remove(test_file)
            print(f"[OK] Write Permissions Verified.")
        except Exception as e:
            print(f"[FAIL] Write Permission DENIED: {e}")
            print("      Users won't be able to punch in/out!")
    else:
        print(f"[FAIL] Attendance Directory MISSING: {attendance_dir}")

def check_python_dependencies():
    print_header("Python Environment")
    
    deps = ['psycopg2', 'keyring', 'cryptography', 'matplotlib', 'seaborn', 'PySide6', 'ut_vfx']
    for dep in deps:
        try:
            __import__(dep)
            print(f"[OK] Module found: {dep}")
        except ImportError:
            print(f"[FAIL] Module MISSING: {dep}")

if __name__ == "__main__":
    print("\nUT_VFX - SYSTEM HEALTH DIAGNOSTIC")
    check_python_dependencies()
    check_network()
    check_ai_server()
    check_database()
    check_attendance()
    
    print("\n[DONE] Diagnostic Complete.\n")
