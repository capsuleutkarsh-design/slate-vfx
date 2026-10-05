
# No password goes in the file: it would be copied to every PC in plain text.
# Each PC is given it once (Reconfigure on the sign-in screen) and keeps it in
# Windows Credential Manager.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import json
import os

def create_client_config():
    # Detect local valid IP (prefer 192.168 or 172.16)
    # These were literals: one studio's server address and one studio's drive
    # letter, in a file meant to be copied to every workstation. Given as
    # arguments now, and otherwise taken from this machine's own settings.
    import argparse

    parser = argparse.ArgumentParser(
        description="Write a client_config.json for the workstations.")
    parser.add_argument("--host", default="",
                        help="the server's address on the studio network")
    parser.add_argument("--server-root", default="",
                        help="the shared Slate_Central folder, as the "
                             "workstations see it")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--database", default="")
    args = parser.parse_args()

    from slate.core.infra.local_secrets import db_settings, setting
    here = db_settings()

    server_root = args.server_root or setting("SERVER_ROOT", "")
    if not server_root:
        parser.error("--server-root is required: there is no sensible default "
                     "for where a studio keeps its shared folder.")

    config = {
        "db_host": args.host or here["host"],
        "db_port": args.port or here["port"],
        "db_name": args.database or here["dbname"],
        "db_user": here["user"],
        "SERVER_ROOT": server_root,
    }

    filename = "client_config.json"
    with open(filename, 'w') as f:
        json.dump(config, f, indent=4)

    print(f"Successfully generated '{filename}'")
    print(f"Copy this file to 'RuntimeData/Slate/config.json' on client PCs.")
    print("It holds no password: on each PC, type the studio's database password once with "
          "Reconfigure server / database (Recover Slate > Show app password shows it).")

if __name__ == "__main__":
    create_client_config()
