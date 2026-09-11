import sqlite3
import logging
import os
import sys
import json
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.append(os.getcwd())
print("Starting Tracking Data migration...", flush=True)

try:
    from ut_vfx.core.infra.postgres_manager import PostgresManager
    print("PostgresManager imported.", flush=True)
except ImportError:
    print("Could not import PostgresManager.")
    sys.exit(1)

SQLITE_DB_PATH = r".\DB\ut_vfx.db"

def migrate_tracking_data():
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"Error: DB not found at {SQLITE_DB_PATH}")
        return

    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    db = PostgresManager()
    from psycopg2.extras import execute_values

    # Clean info
    print("\n--- Cleaning Target Tables (to ensure ID preservation) ---")
    try:
        # We must clear child tables first
        db.execute_query("TRUNCATE TABLE change_history, tracking_tasks, tracking_shots, tracking_projects CASCADE", fetch="none")
        # We don't truncate users usually, but if we need IDs to match exactly...
        # Let's try to update users in place or strictly upsert. 
        # But 'active' failed.
        print("Tables truncated.")
    except Exception as e:
        print(f"Truncate warning: {e}")

    # 0. Migrating Users
    print("\n--- Migrating Users ---")
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    
    user_values = []
    for u_row in users:
        u = dict(u_row)
        
        # Cast active to bool
        act = u.get('active')
        if act is None: act = True
        else: act = bool(act) # 1 -> True, 0 -> False

        user_values.append((
            u['id'],
            u['username'],
            u['display_name'],
            u['role'],
            act, # Boolean
            u.get('profile_pic_path', ''),
            u.get('password_hash', 'legacy_hash') 
        ))

    if user_values:
        # Note: We use ON CONFLICT DO NOTHING for users to avoid overwriting if they exist (e.g. from fresh install)
        # But if we need to link history, the IDs MUST match.
        # If ID conflict exists, we have a problem. 
        # For now, try inserting with ID.
        sql_users = """
            INSERT INTO users (id, username, display_name, role, active, profile_pic_path, password_hash)
            VALUES %s
            ON CONFLICT (id) DO UPDATE SET username=EXCLUDED.username -- Dummy update
        """
        # Actually user PK is usually ID? Or Username? 
        # PostgresManager sync_users uses (username) as conflict target.
        # But FKs use ID.
        # Let's hope IDs are free.
        try:
            with db.get_connection() as pg_conn:
                with pg_conn.cursor() as pg_cur:
                    execute_values(pg_cur, sql_users, user_values)
                    pg_conn.commit()
            print("Users migrated.")
        except Exception as e:
            print(f"User migration warning: {e}")


    # 1. Migrate Tracking Projects
    print("\n--- Migrating Tracking Projects ---")
    cursor.execute("SELECT * FROM tracking_projects")
    projects = cursor.fetchall()
    print(f"Found {len(projects)} projects.")
    
    for p in projects:
        try:
            sql = """
                INSERT INTO tracking_projects (code, name, config_json, active, last_updated)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    config_json = EXCLUDED.config_json,
                    active = EXCLUDED.active,
                    last_updated = EXCLUDED.last_updated
            """
            active_val = True
            if 'active' in p.keys():
                active_val = p['active'] in (1, '1', True, 'true')
                
            db.execute_query(sql, (
                p['code'], 
                p['name'], 
                p['config_json'], 
                active_val, 
                p['last_updated']
            ), fetch="none")
        except Exception as e:
            print(f"Failed to migrate project {p['code']}: {e}")

    # 2. Migrate Tracking Shots (WITH IDs)
    print("\n--- Migrating Tracking Shots ---")
    cursor.execute("SELECT * FROM tracking_shots")
    shots = cursor.fetchall()
    print(f"Found {len(shots)} shots.")
    
    shot_values = []
    
    for s in shots:
        shot_values.append((
            s['id'], # INCLUDE ID
            s['project_code'],
            s['shot_name'],
            s['status'],
            s['priority'],
            s['data_json'],
            s['version'],
            s['last_updated']
        ))
    
    if shot_values:
        sql = """
            INSERT INTO tracking_shots (id, project_code, shot_name, status, priority, data_json, version, last_updated)
            VALUES %s
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                data_json = EXCLUDED.data_json
        """
        
        try:
            with db.get_connection() as pg_conn:
                with pg_conn.cursor() as pg_cur:
                    execute_values(pg_cur, sql, shot_values)
                    # Reset sequence
                    pg_cur.execute("SELECT setval('tracking_shots_id_seq', (SELECT MAX(id) FROM tracking_shots))")
                    pg_conn.commit()
            print(f"Successfully migrated {len(shot_values)} shots.")
        except Exception as e:
            print(f"Batch migration of shots failed: {e}")

    # 3. Migrate Tracking Tasks
    print("\n--- Migrating Tracking Tasks ---")
    try:
        cursor.execute("SELECT * FROM tracking_tasks")
        tasks = cursor.fetchall()
        print(f"Found {len(tasks)} tasks.")
        
        if tasks:
            keys = tasks[0].keys() 
            placeholders = ["%s"] * len(keys)
            cols = list(keys)
            col_str = ",".join(cols)
            
            task_values = []
            for t in tasks:
                task_values.append(tuple([t[k] for k in keys]))
                
            full_sql = f"INSERT INTO tracking_tasks ({col_str}) VALUES %s ON CONFLICT (id) DO NOTHING"
            
            with db.get_connection() as pg_conn:
                with pg_conn.cursor() as pg_cur:
                     execute_values(pg_cur, full_sql, task_values)
                     # Reset sequence if exists
                     try:
                        pg_cur.execute("SELECT setval('tracking_tasks_id_seq', (SELECT MAX(id) FROM tracking_tasks))")
                     except: pass
                     pg_conn.commit()
            print(f"Successfully migrated {len(task_values)} tasks.")

    except Exception as e:
        print(f"Task migration skipped or failed: {e}")

    # 4. Migrate Change History
    print("\n--- Migrating Change History ---")
    try:
        cursor.execute("SELECT * FROM change_history")
        hist = cursor.fetchall()
        print(f"Found {len(hist)} history items.")
        
        if hist:
            keys = hist[0].keys()
            col_str = ",".join(keys)
            vals = []
            for h in hist:
                vals.append(tuple([h[k] for k in keys]))
                
            full_sql = f"INSERT INTO change_history ({col_str}) VALUES %s ON CONFLICT DO NOTHING"
            
            with db.get_connection() as pg_conn:
                with pg_conn.cursor() as pg_cur:
                    execute_values(pg_cur, full_sql, vals)
                    pg_conn.commit()
            print("History migrated.")
            
    except Exception as e:
        print(f"History migration failed: {e}")

    conn.close()
    print("\nTracking Data Migration Complete.")

if __name__ == "__main__":
    migrate_tracking_data()
