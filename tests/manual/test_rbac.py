import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import json
import logging
from ut_vfx.core.infra.database_manager import DatabaseManager
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot

def test_rbac_security():
    print("--- 1. Setup ---")
    db_mgr = DatabaseManager()
    project_code = "TEST_RBAC"
    shot_name = "rbac010"
    
    # Clean setup
    with db_mgr.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO tracking_projects (code, name) VALUES (?, ?)", (project_code, "RBAC Project"))
        conn.execute("DELETE FROM tracking_shots WHERE project_code=?", (project_code,))
        
        initial = Shot(shot_name=shot_name, status="WIP", notes="")
        conn.execute("INSERT INTO tracking_shots (project_code, shot_name, data_json, version) VALUES (?, ?, ?, 1)", 
                    (project_code, shot_name, json.dumps(initial.to_dict())))
        conn.commit()

    print("\n--- 2. Test ARTIST Role (Expect Failure) ---")
    artist_handler = SQLiteHandler(project_code, db_mgr, user_id=10, user_role="artist")
    try:
        artist_handler.update_shot_field(shot_name, "status", "Approved", 1)
        print("FAIL: Artist was able to update shot!")
    except PermissionError as e:
        print(f"SUCCESS: Artist blocked: {e}")
    except Exception as e:
        print(f"FAIL: Unexpected error for artist: {e}")

    print("\n--- 3. Test SUPERVISOR Role (Expect Success) ---")
    sup_handler = SQLiteHandler(project_code, db_mgr, user_id=1, user_role="supervisor")
    try:
        success = sup_handler.update_shot_field(shot_name, "status", "Approved", 1)
        if success:
            print("SUCCESS: Supervisor update allowed.")
        else:
            print("FAIL: Supervisor update returned False (maybe stale data?)")
    except PermissionError:
        print("FAIL: Supervisor was blocked!")
    except Exception as e:
        print(f"FAIL: Unexpected error for supervisor: {e}")
        
    print("\n--- Done ---")

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    test_rbac_security()
