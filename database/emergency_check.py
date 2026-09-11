"""
Emergency check - is the database ACTUALLY working or just hung?
"""

# The password is not in this file. It comes from the machine - either the
# SLATE_DB_PASSWORD environment variable or the git-ignored ut_vfx/config.json
# that setup.bat writes. This repository is public.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from ut_vfx.core.infra.local_secrets import db_password as _db_password

import psycopg2
import sys

try:
    # Quick direct connection
    conn = psycopg2.connect(
        host="172.16.1.45",
        port=5432,
        database="ut_vfx",
        user="postgres",
        password=_db_password(),
        connect_timeout=3
    )
    
    cur = conn.cursor()
    
    print("=" * 70)
    print("EMERGENCY DATABASE CHECK")
    print("=" * 70)
    
    # Check 1: Is there an active CREATE INDEX command?
    cur.execute("""
        SELECT 
            pid,
            now() - query_start as duration,
            state,
            query
        FROM pg_stat_activity 
        WHERE datname = 'ut_vfx'
        AND query ILIKE '%CREATE INDEX%'
        AND state = 'active'
    """)
    
    active = cur.fetchall()
    
    if active:
        print(f"\n✅ YES - Index creation is ACTIVE!")
        for pid, duration, state, query in active:
            print(f"\nPID: {pid}")
            print(f"Duration: {duration}")
            print(f"Query: {query[:150]}...")
        print("\n→ Script IS working, just very slow")
    else:
        print("\n⚠️ NO active CREATE INDEX found")
        print("\nChecking what indexes exist...")
        
        # Check 2: What indexes were created?
        cur.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE schemaname = 'public' AND indexname LIKE 'idx_%'
            ORDER BY indexname
        """)
        
        indexes = cur.fetchall()
        
        if indexes:
            print(f"\n✓ {len(indexes)} indexes created:")
            for (idx,) in indexes:
                print(f"  - {idx}")
        else:
            print("\n✗ NO indexes created!")
        
        print("\n→ Script might be stuck or finished")
    
    # Check 3: Any locks?
    cur.execute("""
        SELECT 
            locktype,
            relation::regclass,
            mode,
            granted
        FROM pg_locks
        WHERE NOT granted
        LIMIT 5
    """)
    
    locks = cur.fetchall()
    
    if locks:
        print(f"\n⚠️ Found {len(locks)} blocked locks:")
        for lock in locks:
            print(f"  {lock}")
    
    cur.close()
    conn.close()
    
    print("\n" + "=" * 70)
    print("CHECK COMPLETE")
    print("=" * 70)
    
except Exception as e:
    print(f"ERROR: {e}")
    print("\nDatabase might be completely locked up!")
    sys.exit(1)
