
import sys
import os
from pathlib import Path
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO)

# Add project root to path
project_root = Path(os.getcwd())
sys.path.append(str(project_root))

try:
    from ut_vfx.core.infra.database_manager import DatabaseManager
    from ut_vfx.core.infra.global_config import GlobalConfig
    
    print("-" * 50)
    print("DEBUGGING STOCK LIBRARY REGRESSION")
    print("-" * 50)

    # 1. Check Global Config
    print(f"\n[1] Checking Server Root...")
    try:
        server_root = GlobalConfig.server_root()
        print(f"Server Root resolved to: {server_root}")
        print(f"Exists? {server_root.exists()}")
    except Exception as e:
        print(f"Error checking server root: {e}")

    # 2. Check Database Path
    print(f"\n[2] Checking Database Manager...")
    try:
        db = DatabaseManager()
        print(f"Database Path used: {db.db_path}")
        print(f"DB Exists? {db.db_path.exists()}")
    except Exception as e:
        print(f"Error initializing DB Manager: {e}")
        sys.exit(1)

    # 3. Query Assets
    print(f"\n[3] Querying Stock Assets...")
    try:
        assets = db.get_all_stock_assets()
        print(f"Total Assets found: {len(assets)}")
        
        if len(assets) > 0:
            print("\n[4] Inspecting first 5 assets:")
            for i, asset in enumerate(assets[:5]):
                print(f"  Asset #{i+1}:")
                print(f"    ID: {asset.get('id')}")
                print(f"    Name: {asset.get('file_name')}")
                
                path = asset.get('file_path')
                thumb = asset.get('thumb_path')
                
                print(f"    File Path: {path}")
                print(f"      -> Exists? {Path(path).exists() if path else False}")
                
                print(f"    Thumb Path: {thumb}")
                print(f"      -> Exists? {Path(thumb).exists() if thumb else False}")
                print("-" * 30)
        else:
            print("WARNING: Stock Library is empty!")
            
    except Exception as e:
        print(f"Error querying assets: {e}")

except ImportError as e:
    print(f"Import Error: {e}")
    print(f"Current Path: {sys.path}")
