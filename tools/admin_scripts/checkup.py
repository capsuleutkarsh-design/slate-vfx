import sys
import os
import json
import traceback

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

try:
    from ut_vfx.core.infra.postgres_manager import PostgresManager
    from ut_vfx.core.infra.global_config import GlobalConfig
    from ut_vfx.core.domain.central_attendance import CentralAttendance
except ImportError as e:
    print(f"FAILED IMPORT: {e}")
    sys.exit(1)

def run_checkup():
    print("=== UT_VFX POSTGRES & SOFTWARE HEALTH CHECKUP ===")
    print("1. Checking Configuration...")
    mode = GlobalConfig.get('db_mode')
    print(f"   Config DB Mode: {mode}")
    if mode != "postgres":
        print("   [CRITICAL] GlobalConfig thinks db_mode is NOT postgres!")
    else:
        print("   [PASS] DB mode is postgres.")

    print("\n2. Checking PostgresManager Connection & Pool...")
    try:
        db = PostgresManager()
        pool_stats = db.get_pool_stats()
        print(f"   Pool Stats: {pool_stats}")
        if pool_stats.get('backend') == 'postgres' and pool_stats.get('pool_initialized'):
            print("   [PASS] Postgres pool initialized successfully.")
        else:
            print("   [FAIL] Pool not properly initialized.")
    except Exception as e:
        print(f"   [FAIL] PostgresManager Error: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n3. Checking Core Tables existence (Schema Check)...")
    expected_tables = [
        "projects", "operations", "task_details", "stock_library", "tracking_projects",
        "tracking_shots", "tracking_tasks", "users", "change_history", "audit_log",
        "attendance_log", "ut_vfx_json_write_locks"
    ]
    all_ok = True
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                tables = [r[0] for r in cur.fetchall()]
                for t in expected_tables:
                    if t in tables:
                        print(f"   [PASS] Table '{t}' found.")
                    else:
                        print(f"   [FAIL] Table '{t}' MISSING.")
                        all_ok = False
        if all_ok:
            print("   [PASS] All expected tables exist.")
    except Exception as e:
        print(f"   [FAIL] Table Check Error: {e}")
        all_ok = False

    print("\n4. Checking Basic Data Integrity & CRUD Operations...")
    try:
        # Insert test
        test_project = "HEALTH_CHECK_TEST"
        q_insert = "INSERT INTO projects (name) VALUES (%s) RETURNING id"
        test_id = db.execute_query(q_insert, (test_project,), fetch="lastrowid")
        print(f"   [PASS] INSERT working! Created ID: {test_id}")

        # Update test
        q_update = "UPDATE projects SET target_directory = %s WHERE id = %s"
        db.execute_update(q_update, ("/test/dir", test_id))
        print("   [PASS] UPDATE working!")
        
        # Select test
        q_select = "SELECT * FROM projects WHERE id = %s"
        row = db.execute_query(q_select, (test_id,), fetch="one")
        if row and row['name'] == test_project:
            print("   [PASS] SELECT working and data matches.")
        else:
            print("   [FAIL] SELECT returned mismatched data.")

        # Delete test
        q_delete = "DELETE FROM projects WHERE id = %s"
        db.execute_update(q_delete, (test_id,))
        print("   [PASS] DELETE working!")
    except Exception as e:
        print(f"   [FAIL] CRUD operations failed: {e}")
        traceback.print_exc()

    print("\n5. Checking JSONB capabilities (Tracking Data/Attendance Metadata)...")
    try:
        # JSONB || operator test (important for Postgres/SQLite compatibility logic)
        q_json_test = "SELECT '{\"test\": 1}'::jsonb || '{\"new\": 2}'::jsonb as res"
        res = db.execute_query(q_json_test, fetch="one")
        if res and res['res']['new'] == 2:
            print("   [PASS] JSONB merge functionality native check passed.")
        else:
            print("   [FAIL] JSONB merge logic failed.")
        
        # Validate CentralAttendance Domain Controller JSON flow
        att = CentralAttendance()
        sql_merge = att._json_merge_sql("metadata", "%s")
        print(f"   [PASS] CentralAttendance JSON merge generator produced: {sql_merge}")
        if "||" not in sql_merge or "jsonb" not in sql_merge:
             print("   [WARN] CentralAttendance might not be recognizing the backend correctly.")
    except Exception as e:
         print(f"   [FAIL] JSONB capability check failed: {e}")
         traceback.print_exc()

    print("\n--- ALL CHECKS COMPLETED ---")

if __name__ == "__main__":
    run_checkup()
