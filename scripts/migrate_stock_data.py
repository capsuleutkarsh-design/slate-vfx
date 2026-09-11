import sqlite3
import logging
import os
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())
print("Starting migration script...", flush=True)

try:
    print("Importing PostgresManager...", flush=True)
    from ut_vfx.core.infra.postgres_manager import PostgresManager
    print("PostgresManager imported.", flush=True)
except ImportError:
    print("Could not import PostgresManager. Ensure you are running from the project root.")
    sys.exit(1)

# Configuration
SQLITE_DB_PATH = r".\DB\ut_vfx.db"

def migrate_data():
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"Error: SQLite DB not found at {SQLITE_DB_PATH}")
        return

    print(f"Reading legacy SQLite DB: {SQLITE_DB_PATH}")
    
    try:
        # Connect to legacy SQLite
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM stock_library")
        rows = cursor.fetchall()
        
        print(f"Found {len(rows)} assets in SQLite. Connecting to PostgreSQL...")
        
        if not rows:
            print("No assets to migrate.")
            conn.close()
            return

        db = PostgresManager()
        
        batch_list = []
        for row in rows:
            # SQLite row keys: id, file_path, file_name, file_size, file_type, 
            # thumb_path, proxy_path, tags, ingest_date, metadata, embedding
            
            # Metadata handling
            meta = {}
            if row['metadata']:
                try:
                    if isinstance(row['metadata'], str):
                        meta = json.loads(row['metadata'])
                    else:
                        meta = row['metadata']
                except:
                    meta = {}

            # Tags handling
            tags = []
            if row['tags']:
                if isinstance(row['tags'], str):
                    # Some legacy tags might be 'Pending' or 'Tag1, Tag2'
                    tags = [t.strip() for t in row['tags'].split(',')]
            
            item = {
                'file_path': row['file_path'],
                'file_name': row['file_name'],
                'file_size': row['file_size'],
                'file_type': row['file_type'],
                'thumb_path': row['thumb_path'],
                'proxy_path': row['proxy_path'],
                'tags': tags,
                'metadata': meta,
                'embedding': None # Embedding format might differ, safest to skip re-importing old embeddings unless compatible
            }
            batch_list.append(item)

        conn.close()

        print(f"Prepared {len(batch_list)} items for insertion...")
        
        # Batch insert
        chunk_size = 500
        total_inserted = 0
        
        for i in range(0, len(batch_list), chunk_size):
            chunk = batch_list[i:i + chunk_size]
            db.add_stock_assets_batch(chunk)
            total_inserted += len(chunk)
            print(f"Migrated {total_inserted}/{len(batch_list)}")

        print("Migration successful.")
        
        # Verify
        count_res = db.execute_query("SELECT COUNT(*) as c FROM stock_library", fetch="one")
        print(f"Total assets in Postgres DB now: {count_res['c']}")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    migrate_data()
