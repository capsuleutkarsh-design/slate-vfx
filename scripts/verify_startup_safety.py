import sys
import os
import logging
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Configure basic logging
logging.basicConfig(level=logging.INFO)

print("[-] Starting Startup Safety Verification...")

try:
    # 1. Simulate Import-Time (Module Level)
    print("[-] Importing gatekeeper_main...")
    import ut_vfx.gatekeeper_main
    print("[+] gatekeeper_main imported successfully.")

    # 2. Check DatabaseManager Import
    print("[-] Importing database_manager...")
    from ut_vfx.core.infra.database_manager import database_manager
    print("[+] database_manager imported successfully.")

    # 3. Verify Lazy Loading State
    print("[-] Checking PostgresManager state...")
    pm = database_manager.backend
    
    if pm.password is None:
        print("[+] SUCCESS: PostgresManager.password is None (Lazy Loaded).")
    else:
        print(f"[?] NOTE: PostgresManager.password is set: {pm.password and '***'}")
        
    if pm._connection_pool is None:
        print("[+] SUCCESS: PostgresManager._connection_pool is None (Not Connected).")
    else:
        print("[!] WARNING: Connection Pool already exists!")

    print("[OK] VERIFICATION PASSED: No import-time crashes detected.")
    sys.exit(0)

except Exception as e:
    print(f"\n[FAIL] CRASH DETECTED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
