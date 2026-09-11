import sys
import os
sys.path.append(os.getcwd())

from ut_vfx.core.infra.postgres_manager import PostgresManager

def check_tables():
    db = PostgresManager()
    
    tables = ['projects', 'tracking_projects', 'tracking_shots']
    
    print("\n--- DEEP DATA CHECK ---")
    for t in tables:
        try:
            # Check count
            row = db.execute_query(f"SELECT COUNT(*) as c FROM {t}", fetch="one")
            count = row['c']
            print(f"Table '{t}': {count} rows")
            
            if count > 0:
                sample = db.execute_query(f"SELECT * FROM {t} LIMIT 1", fetch="one")
                print(f"  Sample ID: {sample.get('id', 'N/A')}")
        except Exception as e:
            print(f"Error checking {t}: {e}")

if __name__ == "__main__":
    check_tables()
