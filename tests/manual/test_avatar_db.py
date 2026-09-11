import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from ut_vfx.core.infra.database_manager import DatabaseManager
import tempfile

def test_avatar_persistence():
    print("Testing Avatar Persistence...")
    
    # 1. Setup Temp DB
    # We want to test the actual logic, but maybe not mess with the real DB if possible.
    # However, the class uses a strict path. 
    # Let's try to instantiate with a temp path if the class supports it.
    # Looking at code: __init__(self, db_path: Optional[Path] = None)
    
    from ut_vfx.core.infra.sqlite_manager import SQLiteManager
    SQLiteManager._instance = None
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test_ut_vfx.db"
        db = DatabaseManager(db_path=db_path)
        
        # 2. Setup User
        username = "test_user_avatar"
        with db.get_connection() as conn:
            conn.execute("INSERT INTO ut_users (username, display_name, password_hash) VALUES (?, ?, ?)", (username, "Test User", "hash123"))
            conn.commit()
            
        # 3. Test Update
        fake_path = "X:/Server/Avatars/test_user_123.png"
        print(f"Updating avatar for {username} to {fake_path}")
        success = db.update_user_profile_pic(username, fake_path)
        
        if not success:
            print("FAIL: update_user_profile_pic returned False")
            return
            
        # 4. Test Get
        print("Retrieving avatar...")
        retrieved_path = db.get_user_profile_pic(username)
        
        print(f"Retrieved: {retrieved_path}")
        
        if retrieved_path == fake_path:
            print("SUCCESS: Avatar path matches!")
        else:
            print(f"FAIL: Expected {fake_path}, got {retrieved_path}")

        if hasattr(db, 'close_connection'):
            db.close_connection()
        SQLiteManager._instance = None

if __name__ == "__main__":
    try:
        test_avatar_persistence()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
