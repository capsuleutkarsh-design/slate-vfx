"""
Apply Database Indexes to PostgreSQL
Uses existing psycopg2 connection (no psql required)
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ut_vfx.core.infra.postgres_manager import PostgresManager
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')

def apply_indexes():
    """Apply all performance indexes to the database"""
    
    print("=" * 70)
    print("  PostgreSQL Index Creation for UT_VFX")
    print("=" * 70)
    print()
    
    # Initialize database connection
    print("[1/4] Connecting to PostgreSQL server at 172.16.1.45...")
    try:
        db = PostgresManager()
        # Test connection
        result = db.execute_query("SELECT version()", fetch="one")
        if result:
            print(f"✓ Connected successfully!")
            print()
        else:
            print("✗ Connection test failed!")
            return False
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print()
        print("Troubleshooting:")
        print("  1. Verify database server is running at 172.16.1.45")
        print("  2. Check your database password is set (Windows Credential Manager)")
        print("  3. Verify network connectivity")
        return False
    
    # Read SQL file
    print("[2/4] Reading index definitions from create_indexes.sql...")
    sql_file = Path(__file__).parent / "create_indexes.sql"
    
    if not sql_file.exists():
        print(f"✗ SQL file not found: {sql_file}")
        return False
    
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    print(f"✓ Loaded {len(sql_content)} characters of SQL")
    print()
    
    # Parse into individual statements
    print("[3/4] Creating indexes...")
    statements = []
    current_statement = []
    
    for line in sql_content.split('\n'):
        line = line.strip()
        
        # Skip comments and empty lines
        if not line or line.startswith('--'):
            continue
        
        # Skip SELECT statements (verification queries)
        if line.upper().startswith('SELECT') or line.upper().startswith('SHOW') or line.upper().startswith('ANALYZE'):
            # Skip verification queries
            if ';' in line:
                continue
            # Multi-line SELECT, skip until semicolon
            while ';' not in line:
                continue
            continue
        
        current_statement.append(line)
        
        # Statement complete
        if ';' in line:
            stmt = ' '.join(current_statement)
            if 'CREATE INDEX' in stmt.upper() or 'CREATE EXTENSION' in stmt.upper():
                statements.append(stmt)
            current_statement = []
    
    print(f"Found {len(statements)} CREATE INDEX statements")
    print()
    
    # Execute each index creation
    success_count = 0
    skip_count = 0
    error_count = 0
    
    for i, stmt in enumerate(statements, 1):
        # Extract index name for display
        index_name = "unknown"
        if "IF NOT EXISTS" in stmt:
            parts = stmt.split("IF NOT EXISTS")
            if len(parts) > 1:
                index_name = parts[1].split()[0].strip()
        
        try:
            db.execute_query(stmt, fetch="none")
            print(f"  ✓ [{i}/{len(statements)}] Created: {index_name}")
            success_count += 1
        except Exception as e:
            error_str = str(e).lower()
            if "already exists" in error_str:
                print(f"  ⊙ [{i}/{len(statements)}] Already exists: {index_name}")
                skip_count += 1
            else:
                print(f"  ✗ [{i}/{len(statements)}] Failed: {index_name}")
                print(f"      Error: {e}")
                error_count += 1
    
    print()
    print("[4/4] Running ANALYZE to update statistics...")
    
    # Analyze tables
    tables = ['stock_library', 'tracking_shots', 'tracking_tasks', 
              'tracking_projects', 'operations']
    
    for table in tables:
        try:
            db.execute_query(f"ANALYZE {table}", fetch="none")
            print(f"  ✓ Analyzed: {table}")
        except Exception as e:
            print(f"  ⊙ Skipped: {table} ({e})")
    
    print()
    print("=" * 70)
    
    if error_count == 0:
        print("  ✓ INDEX CREATION SUCCESSFUL!")
    else:
        print("  ⚠ INDEX CREATION COMPLETED WITH ERRORS")
    
    print("=" * 70)
    print()
    print(f"Summary:")
    print(f"  Created: {success_count}")
    print(f"  Already existed: {skip_count}")
    print(f"  Errors: {error_count}")
    print()
    
    if success_count > 0 or skip_count > 0:
        print("Performance improvements:")
        print("  ✓ Stock library searches - Faster")
        print("  ✓ Project/shot queries - Faster")
        print("  ✓ Tag searches - Full-text indexed")
        print()
        print("Next: Restart UT_VFX to see improved performance.")
    
    return error_count == 0

if __name__ == '__main__':
    try:
        success = apply_indexes()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
