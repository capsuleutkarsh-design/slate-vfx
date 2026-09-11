import sys
import os
from pathlib import Path

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from ut_vfx.core.infra.global_config import GlobalConfig

# Print loaded config details
config = GlobalConfig()
print("Loaded DB_HOST:", config.get("db_host"))

# Print paths
print("LOCAL_APP_DATA:", config.config_path, "Exists:", config.config_path.exists())
print("LEGACY_DOCS:", config.legacy_config_path, "Exists:", config.legacy_config_path.exists())
print("DEV_CONFIG:", config.dev_config_path, "Exists:", config.dev_config_path.exists())

# Print all client config paths tested
client_configs = []
if getattr(sys, 'frozen', False):
    client_configs.append(Path(sys._MEIPASS) / "client_config.json")
    client_configs.append(Path(sys.executable).parent / "client_config.json")
    
client_configs.append(Path.cwd() / "client_config.json")
try:
    source_root = Path(__file__).parent.parent.parent.parent
    client_configs.append(source_root / "client_config.json")
except: pass

print("Client Configs Tested:")
for p in client_configs:
    print(f"  - {p} (Exists: {p.exists()})")
