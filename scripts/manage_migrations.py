import os
import sys
import logging
from datetime import datetime

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from ut_vfx.core.infra.postgres_manager import PostgresManager
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

def init_migration_table(db):
    """Ensure schema_version table exists."""
    q = """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    db.execute_query(q, fetch="none")

def get_applied_versions(db):
    init_migration_table(db)
    q = "SELECT version FROM schema_version"
    rows = db.execute_query(q) or []
    return {r['version'] for r in rows}

def run_migrations():
    db = PostgresManager()
    
    # 1. Ensure Folder
    if not os.path.exists(MIGRATIONS_DIR):
        print(f"No migrations folder found at {MIGRATIONS_DIR}")
        return

    # 2. Get Applied
    applied = get_applied_versions(db)
    
    # 3. List Files (Format: 001_name.sql)
    files = sorted([f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")])
    
    print(f"Checking migrations in {MIGRATIONS_DIR}...")
    
    for f in files:
        try:
            version = int(f.split('_')[0])
        except ValueError:
            print(f"Skipping invalid filename: {f}")
            continue
            
        if version in applied:
            print(f"  [x] {f} (Already applied)")
            continue
            
        print(f"  [ ] Applying {f}...")
        
        # Read SQL
        path = os.path.join(MIGRATIONS_DIR, f)
        with open(path, 'r') as sql_file:
            sql_script = sql_file.read()
            
        try:
            # Transactional Apply
            # PostgresManager handles commits internally in execute_query usually, 
            # but for DDL we want explicit success.
            # Splitting by ; can be risky if ; is in string, but specific migrations are usually simple statements.
            # Better to run as one block if possible.
            
            db.execute_query(sql_script, fetch="none")
            
            # Record Success
            db.execute_query(
                "INSERT INTO schema_version (version, name) VALUES (%s, %s)", 
                (version, f), 
                fetch="none"
            )
            print(f"  --> Success!")
            
        except Exception as e:
            print(f"  --> FAILED: {e}")
            break

if __name__ == "__main__":
    run_migrations()
