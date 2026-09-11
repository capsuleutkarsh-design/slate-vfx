import json
import os
from pathlib import Path
import time
import logging
import re
from .global_config import GlobalConfig
from ut_vfx.utils.safe_json import SafeJsonIO

class ServerHub:
    """
    Manages centralized server resources, commands, and PC registration.
    Now uses SafeJsonIO for concurrency and GlobalConfig for paths.
    """
    def __init__(self):
        self.server_root = GlobalConfig.server_root()
        self.config_dir = self.server_root / "Config"
        self.settings_file = self.config_dir / "settings.json"
        
        # Ensure server structure exists (if on admin machine)
        if not self.config_dir.exists():
            try:
                self.config_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logging.debug(f"Config dir creation failed (likely read-only client): {e}")

        # Old directories, kept for compatibility or if still needed by other parts
        self.dirs = {
            "config": self.server_root / "Config",
            "attendance": self.server_root / "Attendance",
            "assets": self.server_root / "Assets",
            "commands": self.server_root / "Commands",
            "livestatus": self.server_root / "LiveStatus" # NEW FOLDER
        }
        # Ensure old directories exist if they are still in use
        for key, path in self.dirs.items():
            if not path.exists():
                try:
                    path.mkdir(exist_ok=True)
                except Exception as e:
                    logging.warning(f"Could not create directory {path}: {e}")

    def get_users_file(self) -> Path:
        return self.config_dir / "users.json"
    
    def get_config_dir(self) -> Path: return self.config_dir
    def get_attendance_dir(self): return self.dirs["attendance"]
    def get_assets_dir(self): return self.dirs["assets"]
    def get_livestatus_dir(self): return self.dirs["livestatus"] # Getter

    def load_settings(self):
        """Load global settings with locking."""
        return SafeJsonIO.load_json(self.settings_file)

    def save_settings(self, settings):
        """Save global settings with locking."""
        SafeJsonIO.save_json(self.settings_file, settings)

    def register_pc(self, pc_name: str) -> bool:
        """Atomic PC Registration"""
        if not re.match(r'^[A-Za-z0-9_-]+$', pc_name):
            logging.warning(f"SECURITY: Invalid PC name attempted: {pc_name}")
            return False

        def update_logic(data):
            active_pcs = data.get('active_pcs', [])
            if pc_name not in active_pcs:
                active_pcs.append(pc_name)
                data['active_pcs'] = active_pcs
                logging.info(f"Registered new PC: {pc_name}")
                
        return SafeJsonIO.update_json(self.settings_file, update_logic)

    def is_gatekeeper_enabled(self) -> bool:
        settings = self.load_settings()
        return settings.get('gatekeeper_enabled', True)

    def set_gatekeeper_enabled(self, enabled: bool) -> bool:
        """Atomic Gatekeeper Toggle"""
        def update_logic(data):
            data["gatekeeper_enabled"] = enabled
            
        return SafeJsonIO.update_json(self.settings_file, update_logic)

    def trigger_remote_unlock(self, target_pc_name):
        trigger_file = self.dirs["commands"] / f"UNLOCK_{target_pc_name}.trigger"
        try:
            with open(trigger_file, 'w') as f: f.write(f"UNLOCK REQUEST: {time.time()}")
            return True
        except Exception as e:
            logging.exception(f"Trigger remote unlock failed: {e}")
            return False

    def check_for_unlock_trigger(self):
        import socket
        my_pc = socket.gethostname()
        trigger_file = self.dirs["commands"] / f"UNLOCK_{my_pc}.trigger"
        if trigger_file.exists():
            try: 
                os.remove(trigger_file)
                return True
            except Exception as e:
                logging.warning(f"Failed to remove trigger file: {e}")
        return False
    
    # --- BROADCAST SYSTEM ---
    def post_command(self, cmd_type, target="all", message=""):
        # Write a unique command file to avoid race conditions
        cmd_id = f"cmd_{int(time.time())}_{target}"
        cmd_file = self.dirs["commands"] / f"{cmd_id}.json"
        data = {
            "command": cmd_type, 
            "target": target, 
            "message": message, 
            "timestamp": time.time(),
            "expires": time.time() + 60 # Command valid for 60s
        }
        try:
            with open(cmd_file, 'w') as f: json.dump(data, f)
        except Exception as e:
            logging.exception(f"Failed to post command {cmd_type}: {e}")

    def get_active_commands(self):
        """Reads all commands valid for this PC."""
        try:
            import socket
            my_pc = socket.gethostname()
            commands = []
            now = time.time()
            
            # Directory Check (Avoid crash if network drive lost)
            if not self.dirs["commands"].exists():
                return []

            # Cleanup old commands
            for f in self.dirs["commands"].glob("*.json"):
                try:
                    # Basic cleanup of old files
                    if now - f.stat().st_mtime > 120: 
                        os.remove(f)
                        continue
                        
                    with open(f, 'r') as file:
                        data = json.load(file)
                        
                    if data['expires'] > now:
                        if data['target'] == 'all' or data['target'].lower() == my_pc.lower():
                            commands.append(data)
                except Exception:
                    # logging.debug(f"Failed to read command file {f}: {e}")
                    pass
            return commands
        except Exception as e:
            logging.exception(f"Failed to get active commands: {e}")
            return []