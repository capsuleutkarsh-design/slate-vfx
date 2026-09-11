"""
Quick script to check if indexes are being created or if it's stuck
"""

from ut_vfx.core.infra.postgres_manager import PostgresManager
import sys

try:
    db = PostgresManager()
    
    print("Checking current index creation status...")
    print("=" * 70)
    
    # Check for active index creation
    result = db.execute_query("""
        SELECT 
            schemaname,
            tablename,
            indexname,
            pg_size_pretty(pg_relation_size(indexrelid)) as index_size
        FROM pg_stat_user_indexes
        WHERE schemaname = 'public'
        AND indexname LIKE 'idx_%'
        ORDER BY indexname
    """)
    
    if result:
        print(f"\nFound {len(result)} indexes:")
        for row in result:
            print(f"  ✓ {row['tablename']}.{row['indexname']} ({row['index_size']})")
    else:
        print("\n  No custom indexes found yet")
    
    # Check table sizes
    print("\n" + "=" * 70)
    print("Table sizes (this might explain slow index creation):")
    print("=" * 70)
    
    sizes = db.execute_query("""
        SELECT 
            tablename,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
            n_live_tup as row_count
        FROM pg_stat_user_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
        LIMIT 10
    """)
    
    if sizes:
        for row in sizes:
            print(f"  {row['tablename']}: {row['size']} ({row['row_count']:,} rows)")
    
    print("\n" + "=" * 70)
    
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
