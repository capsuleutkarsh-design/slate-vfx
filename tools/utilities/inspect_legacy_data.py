import sqlite3
import os

DB_PATH = r".\DB\ut_vfx.db"

def inspect_other_tables():
    if not os.path.exists(DB_PATH):
        print(f"File not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    tables_to_check = ['users', 'projects', 'tracking_projects', 'tracking_shots']
    
    for t in tables_to_check:
        print(f"\n--- Checking Table: {t} ---")
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {t}")
            count = cursor.fetchone()[0]
            print(f"Row Count: {count}")
            
            if count > 0:
                print(f"Sample Row:")
                cursor.execute(f"SELECT * FROM {t} LIMIT 1")
                row = cursor.fetchone()
                print(dict(row))
        except Exception as e:
            print(f"Error checking {t}: {e}")

    conn.close()

if __name__ == "__main__":
    inspect_other_tables()
