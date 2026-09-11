import json
import time
import logging
from pathlib import Path
from packaging import version as pkg_version # Try to use packaging if available
# Fallback logic handled within the class if import fails

from PySide6.QtCore import QThread, Signal

from ... import __version__ as CURRENT_VERSION
from ..infra.global_config import GlobalConfig

class UpdateChecker(QThread):
    """
    Background thread to check for available updates on the server.
    Fires `update_available` signal if a newer version is found.
    """
    update_available = Signal(dict) # Emits the manifest data
    update_not_found = Signal(str) # Emits current version if up to date
    
    def __init__(self, parent=None, manual_mode=False, target="client"):
        super().__init__(parent)
        self._is_running = True
        self.manual_mode = manual_mode
        self.target = target
        self.last_result_reason = "not_checked"

    def _should_stop(self) -> bool:
        return (not self._is_running) or self.isInterruptionRequested()

    def _sleep_interruptibly(self, seconds: int) -> bool:
        """Sleep in short slices so close/stop requests are honored quickly."""
        for _ in range(max(0, int(seconds * 10))):
            if self._should_stop():
                return False
            time.sleep(0.1)
        return not self._should_stop()
        
    def run(self):
        """Main thread loop."""
        # 1. Initial Start Delay (only in background mode)
        if not self.manual_mode:
            if not self._sleep_interruptibly(10):
                return
        
        try:
            self.last_result_reason = "running"
            if self._should_stop():
                return
            logging.info(f"UpdateChecker: Starting check for target {self.target}...")
            
            # 2. Get Server Path
            server_root = GlobalConfig.server_root()
            manifest_path = server_root / "Updates" / "releases" / f"manifest_{self.target}.json"
            
            if not manifest_path.exists():
                logging.debug(f"UpdateChecker: No update pointers found at {manifest_path}")
                self.last_result_reason = "manifest_missing"
                if self.manual_mode and not self._should_stop():
                    self.update_not_found.emit(CURRENT_VERSION)
                return

            # 3. Read Manifest
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                
            remote_version_str = manifest.get("version", "")
            if not remote_version_str:
                self.last_result_reason = "invalid_manifest"
                if self.manual_mode and not self._should_stop():
                    self.update_not_found.emit(CURRENT_VERSION)
                return
                
            if self._should_stop():
                return
                
            # 4. Compare Versions
            # In central-folder manual updates, 'latest' might just trigger an update if hash differs or user wants to force it.
            # But we can compare versions if available.
            if self._is_newer(remote_version_str, CURRENT_VERSION) or remote_version_str.lower() == "latest":
                logging.info(f"UpdateChecker: Update found for {self.target}!")
                self.last_result_reason = "update_available"
                if not self._should_stop():
                    self.update_available.emit(manifest)
            else:
                logging.info(f"UpdateChecker: App is up to date ({CURRENT_VERSION})")
                self.last_result_reason = "up_to_date"
                if self.manual_mode and not self._should_stop():
                    self.update_not_found.emit(CURRENT_VERSION)
                
        except Exception as e:
            logging.exception(f"UpdateChecker Error: {e}")
            self.last_result_reason = "error"
            if self.manual_mode and not self._should_stop():
                self.update_not_found.emit(CURRENT_VERSION)

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self._is_running = False
        self.requestInterruption()
    
    def _is_newer(self, remote: str, local: str) -> bool:
        """
        Compare two version strings.
        Handle 'BETA' prefix if present.
        """
        def clean(v):
            return v.replace("BETA ", "").replace("ALPHA ", "").strip()
            
        r_clean = clean(remote)
        l_clean = clean(local)
        
        # Try semantic comparison
        try:
            return pkg_version.parse(r_clean) > pkg_version.parse(l_clean)
        except Exception:
            # Fallback to simple string check (unsafe but better than crash)
            return r_clean != l_clean and r_clean > l_clean
