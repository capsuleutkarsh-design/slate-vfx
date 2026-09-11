"""
This test verifies the user authentication migration system.

It validates two critical scenarios:
1. Backward Compatibility: Ensures users with legacy SHA256 password hashes can still login.
2. Auto-Upgrade: Confirms that upon successful login, legacy hashes are automatically upgraded to secure Bcrypt hashes.
"""

import sys
import os
import hashlib

# Add project root to path
sys.path.append(os.getcwd())

from ut_vfx.core.domain.user_manager import UserManager

def test_migration():
    print("--- TESTING BCRYPT MIGRATION ---")
    
    # 1. Setup a Mock User with a LEGACY SHA256 HASH
    # Hash for "testpass"
    legacy_hash = hashlib.sha256(b"testpass").hexdigest()
    
    um = UserManager()
    # Manually inject legacy user
    um.users["legacy_user"] = {
        "password_hash": legacy_hash,
        "role": "Artist",
        "display_name": "Legacy User",
        "job_title": "Test"
    }
    um.save_users()
    print("[INIT] Created 'legacy_user' with SHA256 hash.")
    
    # 2. Authenticate
    print("[AUTH] Attempting login with 'testpass'...")
    user = um.authenticate("legacy_user", "testpass")
    
    if user:
        print("[OK] Login SUCCESSFUL")
    else:
        print("[FAIL] Login FAILED")
        return

    # 3. Check if Hash was Upgraded
    new_hash = um.users["legacy_user"]["password_hash"]
    if new_hash.startswith("$2b$") or new_hash.startswith("$2a$"):
        print(f"[OK] HASH UPGRADED: {new_hash}")
    else:
        print(f"[FAIL] HASH NOT UPGRADED: {new_hash}")

    # 4. Verify New Hash Works
    print("[AUTH] Attempting login with NEW hash...")
    user2 = um.authenticate("legacy_user", "testpass")
    if user2:
        print("[OK] Re-login SUCCESSFUL")
    else:
        print("[FAIL] Re-login FAILED")

if __name__ == "__main__":
    test_migration()
