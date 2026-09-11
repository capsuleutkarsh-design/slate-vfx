
# The password is not in this file. It comes from the machine - either the
# SLATE_DB_PASSWORD environment variable or the git-ignored slate/config.json
# that setup.bat writes. This repository is public.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from slate.core.infra.local_secrets import db_password as _db_password

import json
import os

def create_client_config():
    # Detect local valid IP (prefer 192.168 or 172.16)
    # Since we know the setup, we can ask or default to the one user stated.
    # User said: "my other pc on lan uses 192.168.0.xxxx"
    # So we should use the server's 192.168.0.45 address.
    
    server_ip = "192.168.0.45" 
    
    config = {
        "db_host": server_ip,
        "db_port": 5440,
        "db_name": "ut_vfx",
        "db_user": "postgres",
        "db_password": _db_password(required=False),
        "SERVER_ROOT": "X:/Extra/UT_Central"
    }
    
    filename = "client_config.json"
    with open(filename, 'w') as f:
        json.dump(config, f, indent=4)
        
    print(f"Successfully generated '{filename}'")
    print(f"Copy this file to 'RuntimeData/Slate/config.json' on client PCs.")

if __name__ == "__main__":
    create_client_config()
