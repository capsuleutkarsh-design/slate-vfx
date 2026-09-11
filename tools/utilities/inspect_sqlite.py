import sqlite3
import os

DB_PATH = r".\DB\ut_vfx.db"

def inspect_db():
    if not os.path.exists(DB_PATH):
        print(f"File not found: {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # List tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"Tables found: {[t[0] for t in tables]}")
        
        for table_name in tables:
            t = table_name[0]
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {t}")
                count = cursor.fetchone()[0]
                print(f"Table '{t}': {count} rows")
                
                if 'stock' in t.lower() or 'library' in t.lower():
                    print(f"--- Schema for {t} ---")
                    cursor.execute(f"PRAGMA table_info({t})")
                    columns = cursor.fetchall()
                    for col in columns:
                        print(col)
                    
                    print(f"--- First 1 rows of {t} ---")
                    cursor.execute(f"SELECT * FROM {t} LIMIT 1")
                    rows = cursor.fetchall()
                    for r in rows:
                        print(r)
            except Exception as e:
                print(f"Error reading table {t}: {e}")
                
        conn.close()
        
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    inspect_db()
