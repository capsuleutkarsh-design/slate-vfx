import os
import sys
import subprocess
import logging
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QApplication
from PySide6.QtCore import Qt

from ut_vfx.core.infra.config_manager import ConfigManager
from ut_vfx.core.infra.database_manager import database_manager

logger = logging.getLogger(__name__)

class DCCLauncher:
    """
    Handles launching of Digital Content Creation (DCC) tools like Nuke, Blender, and Silhouette.
    Injects the UTVFX environment variables into the process so the DCC plugins can communicate 
    back with the SQLite database.
    """
    
    _prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    
    SUPPORTED_DCCS = {
        "nuke": {
            "name": "Foundry Nuke",
            "env_var": "NUKE_PATH",
            "plugin_dir": "nuke",
            "default_paths": [
                os.path.join(_prog_files, "Nuke15.0v1", "Nuke15.0.exe"),
                os.path.join(_prog_files, "Nuke14.1v1", "Nuke14.1.exe"),
                os.path.join(_prog_files, "Nuke14.0v5", "Nuke14.0.exe"),
                os.path.join(_prog_files, "Nuke13.2v4", "Nuke13.2.exe")
            ],
            "filter": "Executable (*.exe)"
        },
        "blender": {
            "name": "Blender",
            "env_var": "BLENDER_USER_SCRIPTS",
            "plugin_dir": "blender",
            "default_paths": [
                os.path.join(_prog_files, "Blender Foundation", "Blender 4.0", "blender.exe"),
                os.path.join(_prog_files, "Blender Foundation", "Blender 3.6", "blender.exe")
            ],
            "filter": "Executable (*.exe)"
        },
        "silhouette": {
            "name": "BorisFX Silhouette",
            "env_var": "SFX_SCRIPT_PATH",
            "plugin_dir": "silhouette",
            "default_paths": [
                os.path.join(_prog_files, "BorisFX", "Silhouette 2023.5", "silhouette.exe"),
                os.path.join(_prog_files, "BorisFX", "Silhouette 2023", "silhouette.exe")
            ],
            "filter": "Executable (*.exe)"
        },
        "natron": {
            "name": "Natron",
            "env_var": "NATRON_PLUGIN_PATH",
            "plugin_dir": "natron",
            "default_paths": [
                os.path.join(_prog_files, "INRIA", "Natron-2.5.0", "bin", "Natron.exe"),
                os.path.join(_prog_files, "Natron", "bin", "Natron.exe")
            ],
            "filter": "Executable (*.exe)"
        }
    }

    def __init__(self, parent_widget=None):
        self.config_manager = ConfigManager()
        self.parent_widget = parent_widget

    def _get_dcc_executable(self, dcc_id: str) -> str:
        """
        Retrieves the path to the DCC executable. 
        If not found in settings, tries default paths. 
        If still not found, prompts the user via GUI.
        """
        dcc_info = self.SUPPORTED_DCCS.get(dcc_id)
        if not dcc_info:
            raise ValueError(f"Unsupported DCC: {dcc_id}")

        settings_key = f"dcc_path_{dcc_id}"
        
        # 1. Check settings first
        saved_path = self.config_manager.settings.get('global_settings', {}).get(settings_key)
        if saved_path and os.path.exists(saved_path):
            return saved_path

        # 2. Try common default installation paths
        for path in dcc_info["default_paths"]:
            if os.path.exists(path):
                logger.info(f"Auto-detected {dcc_info['name']} at {path}")
                self._save_dcc_path(settings_key, path)
                return path

        # 3. If running headless (no UI), we can't ask the user
        if not QApplication.instance():
            logger.error(f"Cannot find {dcc_info['name']} and no UI is available to prompt.")
            return ""

        # 4. Fallback to GUI popup
        QMessageBox.information(
            self.parent_widget,
            "Executable Not Found",
            f"{dcc_info['name']} was not found in the standard locations.\n\nPlease locate the executable (.exe) file.",
        )
        
        file_path, _ = QFileDialog.getOpenFileName(
            self.parent_widget,
            f"Locate {dcc_info['name']} Executable",
            "C:\\Program Files",
            dcc_info["filter"]
        )

        if file_path and os.path.exists(file_path):
            self._save_dcc_path(settings_key, file_path)
            return file_path

        return ""

    def _save_dcc_path(self, key: str, path: str):
        """Saves the detected or selected DCC path back to the global settings."""
        settings = self.config_manager.settings.get('global_settings', {})
        settings[key] = path
        self.config_manager.update_global_settings(settings)

    def launch(self, dcc_id: str, shot_id: int):
        """
        Launches the specified DCC with the environment set up for the given shot.
        """
        dcc_info = self.SUPPORTED_DCCS.get(dcc_id)
        if not dcc_info:
            logger.error(f"Invalid DCC: {dcc_id}")
            return False

        executable = self._get_dcc_executable(dcc_id)
        if not executable:
            logger.warning(f"Launch cancelled for {dcc_info['name']}. No executable found.")
            return False

        # Prepare environment
        env = os.environ.copy()
        
        # Inject UTVFX specific variables
        env["UTVFX_SHOT_ID"] = str(shot_id)
        
        # The absolute path to the main ut_vfx database
        db_path = database_manager.db_path
        if db_path:
            env["UTVFX_DB_PATH"] = str(db_path)
            
        # The absolute path to our python source root so plugins can import ut_vfx directly
        src_root = str(Path(__file__).parent.parent.parent.resolve())
        env["PYTHONPATH"] = f"{src_root};{env.get('PYTHONPATH', '')}"

        # Inject the DCC specific plugin path
        plugin_dir = Path(__file__).parent.parent / "plugins" / "dcc" / dcc_info["plugin_dir"]
        plugin_dir.mkdir(parents=True, exist_ok=True)
        
        env_var = dcc_info["env_var"]
        existing_path = env.get(env_var, "")
        if existing_path:
            env[env_var] = f"{plugin_dir};{existing_path}"
        else:
            env[env_var] = str(plugin_dir)

        logger.info(f"Launching {dcc_info['name']} for Shot ID {shot_id}...")
        
        try:
            # Launch asynchronously so it doesn't block the UI
            subprocess.Popen([executable], env=env)
            return True
        except Exception as e:
            logger.error(f"Failed to launch {dcc_info['name']}: {e}")
            if self.parent_widget:
                QMessageBox.critical(
                    self.parent_widget,
                    "Launch Error",
                    f"Failed to launch {dcc_info['name']}:\n\n{e}"
                )
            return False
