import os
import shutil
import sys
import subprocess
import logging
import hashlib
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from ..infra.global_config import GlobalConfig

class SidecarEngine(QObject):
    """
    Orchestrates the download and launching of the sidecar updater.
    """
    update_progress = Signal(int, str) # percent, status
    update_error = Signal(str)
    
    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest
        self.download_dir = Path(os.getenv('TEMP')) / "UTVFXUpdate"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
    def stage_update(self, source_zip_path=None):
        """
        Phase 1: Download package to temp and verify integrity.
        """
        try:
            package_name = self.manifest.get("package_name", "UTVFX_Update.zip")
            
            if source_zip_path:
                source_zip = Path(source_zip_path)
            else:
                server_root = GlobalConfig.server_root()
                # Central folder architecture
                source_zip = server_root / "Updates" / "releases" / package_name
            
            if not source_zip.exists():
                raise FileNotFoundError(f"Update package not found at {source_zip}")
                
            self.local_zip = self.download_dir / package_name
            
            # 1. Download (Copy)
            self.update_progress.emit(10, "Downloading update package...")
            shutil.copy2(source_zip, self.local_zip)
            self.update_progress.emit(50, "Download complete.")
            
            # 1.5 Hash Verification
            self.update_progress.emit(55, "Verifying package integrity...")
            expected_hash = self.manifest.get("hash_sha256")
            if expected_hash:
                sha256_hash = hashlib.sha256()
                with open(self.local_zip, "rb") as f:
                    for byte_block in iter(lambda: f.read(4096), b""):
                        sha256_hash.update(byte_block)
                actual_hash = sha256_hash.hexdigest()
                
                if actual_hash != expected_hash:
                    os.remove(self.local_zip)
                    raise ValueError(f"Hash mismatch! Expected {expected_hash}, got {actual_hash}. The package is corrupted or tampered with.")
                logging.info(f"Package verified successfully: {actual_hash}")
            
            # 2. Locate Updater Executable
            if getattr(sys, 'frozen', False):
                base_dir = Path(os.path.dirname(sys.executable))
                updater_exe = base_dir / "UTVFXUpdater.exe"
            else:
                base_dir = Path.cwd()
                updater_exe = base_dir / "UTVFXUpdater.exe"
            
            if not updater_exe.exists():
                # Dev fallback: we might not have it. Create a dummy or fail.
                logging.warning("UTVFXUpdater.exe not found! This is expected in dev but fatal in production.")
                # We will continue staging but apply_update will fail.
                
            # 3. Stage Updater in Temp
            self.temp_updater = self.download_dir / "UTVFXUpdater.exe"
            if updater_exe.exists():
                shutil.copy2(updater_exe, self.temp_updater)
            
            self.update_progress.emit(100, "Staging complete. Ready to restart.")
            return True
            
        except Exception as e:
            logging.exception(f"Update Staging Failed: {e}")
            self.update_error.emit(str(e))
            return False

    def apply_update(self):
        """
        Phase 2: Launch the sidecar updater to overwrite the app and restart.
        """
        try:
            if not hasattr(self, 'temp_updater') or not hasattr(self, 'local_zip'):
                raise ValueError("Update not staged! Call stage_update() first.")
                
            if not self.temp_updater.exists():
                raise FileNotFoundError("Staged UTVFXUpdater.exe not found.")

            pid = os.getpid()
            base_dir = Path(os.path.dirname(sys.executable)) if getattr(sys, 'frozen', False) else Path.cwd()
            install_dir = base_dir
            
            # Use sys.executable name if frozen, otherwise fallback to main.py
            exe_name = Path(sys.executable).name if getattr(sys, 'frozen', False) else "main.py"
            
            cmd = [
                str(self.temp_updater),
                str(pid),
                str(self.local_zip),
                str(install_dir),
                exe_name
            ]
            
            logging.info(f"Launching Sidecar: {cmd}")
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            
        except Exception as e:
            logging.exception(f"Apply Update Failed: {e}")
            self.update_error.emit(str(e))
