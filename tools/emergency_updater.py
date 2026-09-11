import os
import sys
import json
import shutil
import hashlib
import logging
import traceback
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def verify_file_hash(file_path, expected_hash):
    """Verify SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest() == expected_hash
    except OSError as e:
        logging.error(f"Failed to read file for hashing: {e}")
        return False

def main():
    print("========================================")
    print("    UT_VFX Emergency Recovery Updater   ")
    print("========================================")
    print("This tool will force an update from the Central Server without launching the application.")
    print("Use this if the application crashes on startup and cannot update normally.\n")

    target = input("Are you updating the 'client' or 'server'? [client/server]: ").strip().lower()
    if target not in ["client", "server"]:
        print("Invalid target. Must be 'client' or 'server'. Exiting.")
        sys.exit(1)
        
    exe_name = "ut_vfx.exe" if target == "client" else "ut_server.exe"

    # Find Central Directory
    config_path = Path.home() / ".capsule_vfx" / "local_config.json"
    if not config_path.exists():
        print(f"ERROR: Local config not found at {config_path}")
        print("Cannot determine Central Server path.")
        sys.exit(1)
        
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        central_path = Path(config.get("server_root", ""))
    except Exception as e:
        print(f"ERROR: Failed to read local config: {e}")
        sys.exit(1)
        
    if not central_path.exists():
        print(f"ERROR: Central Server path does not exist: {central_path}")
        sys.exit(1)
        
    manifest_path = central_path / "Updates" / "releases" / f"manifest_{target}.json"
    if not manifest_path.exists():
        print(f"ERROR: Manifest not found at {manifest_path}")
        sys.exit(1)
        
    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read manifest: {e}")
        sys.exit(1)
        
    version = manifest.get("version", "Unknown")
    download_url = manifest.get("download_url", "")
    file_hash = manifest.get("hash", "")
    
    print(f"\nFound Update: v{version}")
    
    # Resolve the download path
    if download_url.startswith("file:///"):
        source_zip = Path(download_url.replace("file:///", ""))
    else:
        # Fallback, assume it's next to the manifest
        source_zip = manifest_path.parent / f"v{version}" / download_url.split("/")[-1]
        if not source_zip.exists():
            source_zip = central_path / download_url
            
    if not source_zip.exists():
        print(f"ERROR: Update package not found at {source_zip}")
        sys.exit(1)
        
    print(f"Staging update package...")
    
    # Get current application directory (where this script is located, tools folder parent)
    app_dir = Path(__file__).resolve().parent.parent
    staging_dir = app_dir / "Updates" / "Staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    
    dest_zip = staging_dir / "UTVFX_Update.zip"
    
    try:
        shutil.copy2(source_zip, dest_zip)
    except Exception as e:
        print(f"ERROR: Failed to copy update package: {e}")
        sys.exit(1)
        
    print("Verifying hash...")
    if not verify_file_hash(dest_zip, file_hash):
        print("ERROR: Hash verification failed. The update package may be corrupted.")
        sys.exit(1)
        
    print("Hash verified successfully.")
    
    # Locate sidecar updater
    updater_exe = app_dir / "ut_vfx" / "core" / "updater" / "updater_script.exe"
    updater_py = app_dir / "ut_vfx" / "core" / "updater" / "updater_script.py"
    
    if updater_exe.exists():
        updater_path = updater_exe
        is_python = False
    elif updater_py.exists():
        updater_path = updater_py
        is_python = True
    else:
        print(f"ERROR: Sidecar updater not found in {app_dir / 'ut_vfx' / 'core' / 'updater'}")
        sys.exit(1)
        
    print("\nLaunching Sidecar Updater...")
    # PID=0 skips the kill check
    cmd = []
    if is_python:
        cmd.append(sys.executable)
    cmd.extend([str(updater_path), "0", str(dest_zip), str(app_dir), exe_name])
    
    try:
        subprocess.Popen(cmd, cwd=str(app_dir))
        print("Update initiated! This console will now close, and the updater will take over in the background.")
    except Exception as e:
        print(f"ERROR: Failed to launch updater: {e}")
        sys.exit(1)
        
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Critical Error: {traceback.format_exc()}")
    input("Press Enter to exit...")
