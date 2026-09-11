"""
Check if PostgreSQL is actively creating indexes RIGHT NOW
"""

from ut_vfx.core.infra.postgres_manager import PostgresManager

try:
    db = PostgresManager()
    
    print("Checking PostgreSQL activity...")
    print("=" * 70)
    
    # Check for active queries (index creation shows here)
    activity = db.execute_query("""
        SELECT 
            pid,
            state,
            query_start,
            state_change,
            EXTRACT(EPOCH FROM (NOW() - query_start)) as seconds_running,
            query
        FROM pg_stat_activity
        WHERE datname = 'ut_vfx'
        AND state = 'active'
        AND query NOT LIKE '%pg_stat_activity%'
    """)
    
    if activity:
        print(f"\n✅ ACTIVE QUERIES FOUND ({len(activity)}):\n")
        for row in activity:
            print(f"PID: {row['pid']}")
            print(f"Running for: {int(row['seconds_running'])} seconds ({int(row['seconds_running']/60)} minutes)")
            print(f"Query: {row['query'][:200]}...")
            print("-" * 70)
        print("\n✅ YES - Database is actively working on index creation!")
        print("   It's safe to wait. Progress is being made.")
    else:
        print("\n⚠️ NO ACTIVE QUERIES FOUND")
        print("   The script might be stuck or waiting.")
        print("   Check your terminal - did it error out?")
    
    # Also check what indexes exist now
    print("\n" + "=" * 70)
    print("Indexes created so far:")
    print("=" * 70)
    
    indexes = db.execute_query("""
        SELECT indexname 
        FROM pg_indexes 
        WHERE schemaname = 'public' 
        AND indexname LIKE 'idx_%'
        ORDER BY indexname
    """)
    
    if indexes:
        print(f"\n✓ {len(indexes)} custom indexes found:")
        for idx in indexes:
            print(f"  - {idx['indexname']}")
    else:
        print("\n  No custom indexes created yet")
        
except Exception as e:
    print(f"Error: {e}")
