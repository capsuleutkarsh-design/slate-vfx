import os
import json
from pathlib import Path

print("Looking for configs...")

appdata = Path(os.getenv('LOCALAPPDATA', '')) / "UTVFX" / "config.json"
docs = Path.home() / "Documents" / "UTVFX" / "config.json"

for p in [appdata, docs]:
    if p.exists():
        print(f"FOUND: {p}")
        try:
            with open(p, 'r') as f:
                data = json.load(f)
                print(f"  db_host -> {data.get('db_host')}")
        except Exception as e:
            print(f"  Error reading: {e}")
    else:
        print(f"MISSING: {p}")
