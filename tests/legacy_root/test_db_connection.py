import sys
from pathlib import Path

# Add project root (Document root) to sys.path
root_dir = Path(r".")
sys.path.append(str(root_dir))

print(f"Added {root_dir} to sys.path")

try:
    from ut_vfx.core.infra.global_config import GlobalConfig
    from ut_vfx.core.infra.postgres_manager import PostgresManager

    print("--- GLOBAL CONFIG ---")
    try:
        # Check if SERVER_ROOT is accessible
        server_root = GlobalConfig.server_root()
        print(f"Server Root: {server_root}")
        
        # Check Developer Mode
        is_dev = GlobalConfig.is_developer()
        print(f"Developer Mode: {is_dev}")
        
        # Check abstract_path functionality (to verify fix)
        # Assuming server root is X:/Extra/UT_Central
        test_path = str(server_root / "Assets/test.mov")
        abstract = GlobalConfig.abstract_path(test_path)
        print(f"Abstract Path Test: '{test_path}' -> '{abstract}'")
        
        # Verify it works correctly (should contain $SERVER if server root matches)
        if "$SERVER" in abstract:
            print("Status: Abstract Path Logic WORKING")
        else:
            print("Status: Abstract Path Logic FAILED (Expected $SERVER)")
            
    except Exception as e:
        print(f"GLOBAL CONFIG ERROR: {e}")

    print("\n--- POSTGRES MANAGER ---")
    try:
        db = PostgresManager()
        print(f"Connecting to DB: {db.host}:{db.port} ({db.dbname})")
        
        # Try simple select
        res = db.execute_query("SELECT NOW() as server_time", fetch="one")
        
        if res:
            print(f"SUCCESS: Connected! Server Time: {res}")
        else:
            print("FAILURE: No result from query (Check DB Connection)")
            
    except Exception as e:
        print(f"DB CONNECTION ERROR: {e}")

except ImportError as e:
    print(f"IMPORT ERROR: {e}")
