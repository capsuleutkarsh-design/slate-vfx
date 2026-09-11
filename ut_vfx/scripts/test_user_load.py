import logging

import sys
import os
import json
from pathlib import Path

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ut_vfx.core.infra.global_config import GlobalConfig
from ut_vfx.core.infra.server_hub import ServerHub

def test_load():
    logging.info("Testing User Loading Logic...")
    
    # mimic GlobalConfig and ServerHub resolution
    GlobalConfig()
    hub = ServerHub()
    users_file = hub.get_users_file()
    
    logging.info(f"Resolving users file path: {users_file}")
    
    if not users_file.exists():
        logging.error(f"ERROR: File not found at {users_file}")
        # Try local fallback manually just to see
        local_fallback = Path.home() / "RuntimeData" / "UT_Central" / "Config" / "users.json"
        logging.info(f"Checking fallback: {local_fallback} -> Exists: {local_fallback.exists()}")
        return

    try:
        with open(users_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logging.info(f"File loaded successfully. Keys: {list(data.keys())}")
        
        if 'users' in data:
            users = data['users']
            logging.info(f"Found {len(users)} users.")
            for u in list(users.keys())[:3]:
                logging.info(f" - User: {u}, Role: {users[u].get('roles', users[u].get('role', 'N/A'))}")
        else:
            logging.warning("WARNING: 'users' key NOT found in root JSON.")
            logging.info(f"Data dump (partial): {str(data)[:200]}")

    except Exception as e:
        logging.exception(f"EXCEPTION: {e}")

if __name__ == "__main__":
    test_load()
