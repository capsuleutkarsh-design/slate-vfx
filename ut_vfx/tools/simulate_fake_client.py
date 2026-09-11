import logging
import json
import time
import sys
from pathlib import Path

# Important: make sure we can import GlobalConfig from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
try:
    from ut_vfx.core.infra.global_config import GlobalConfig
    REPO_PATH = GlobalConfig.server_root() / "LiveStatus"
except ImportError:
    # Fallback only if import fails
    REPO_PATH = Path(r".\ut_vfx\LiveStatus_Test")
    
REPO_PATH.mkdir(parents=True, exist_ok=True)

# Config
FAKE_PC_NAME = "ART-STATION-01"
FAKE_SOFTWARE_USER = "Artist_JohnDoe" # The UT Login
FAKE_OS_USER = "artist_01" # The Windows Login

data = {
    "pc_name": FAKE_PC_NAME,
    "user": FAKE_SOFTWARE_USER,
    "os_user": FAKE_OS_USER, # <--- NEW FIELD
    "status": "Online",
    "last_seen": time.time(),
    "ComputerName": FAKE_PC_NAME,
    "IPAddress": "192.168.1.105",
    "MACAddress": "00:1A:2B:3C:4D:5E",
    "Manufacturer": "Dell Inc.",
    "Model": "Precision 3660",
    "SerialNo": "JKL12345",
    "Motherboard": "Dell Inc. 0W1234",
    "CPU": "Intel(R) Core(TM) i9-12900K",
    "GPU": "NVIDIA GeForce RTX 3090 (24.0 GB)",
    "RAM_GB": "64.0 GB",
    "OS": "Windows 10 Pro",
    "WindowsVersion": "10.0.19045",
    "client_version": "1.2.0",
    "Drives": [
        {"Root": "C:", "Label": "OS", "Capacity_GB": 1024.0, "Free_GB": 450.5, "Usage": "56%"},
        {"Root": "D:", "Label": "Projects", "Capacity_GB": 4096.0, "Free_GB": 1200.0, "Usage": "70%"}
    ]
}

file_path = REPO_PATH / f"{FAKE_PC_NAME}.json"
with open(file_path, 'w') as f:
    json.dump(data, f, indent=4)

logging.info(f"Created fake heartbeat for {FAKE_PC_NAME}")
logging.info(f"Location: {file_path}")
logging.info("Check Admin Panel -> Live Ops now.")
logging.info(f"(If you don't see it, ensure Admin Panel is reading from {REPO_PATH})")
