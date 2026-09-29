import glob
import html
import os
import re
import subprocess
import logging
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PySide6.QtWidgets import QFileDialog, QMessageBox, QApplication

from slate.core.infra.config_manager import ConfigManager
from slate.core.infra.database_manager import database_manager

logger = logging.getLogger(__name__)


# Every app Slate can open a shot in, and where to look for it.
#
# The search patterns are relative to Program Files and match any version, so a
# studio that upgrades Nuke or Blender does not wait for a Slate release to be
# able to open it. "exe" is the program's own file name, matched exactly: the
# Nuke folder also holds python.exe, crash handlers and helpers that a loose
# "Nuke*\*.exe" would happily pick.
#
# "setting" is the older key the shot panel read from config.json. It still
# works; new choices are saved as dcc_path_<id> in the global settings.
DCC_APPS = {
    "nuke": {
        "name": "Foundry Nuke",
        "setting": "nuke_path",
        "env_path": "Slate_NUKE_PATH",
        "env_var": "NUKE_PATH",
        "plugin_dir": "nuke",
        "search": [r"Nuke*\Nuke*.exe", r"Foundry\Nuke*\Nuke*.exe"],
        "exe": r"Nuke\d+(\.\d+)*\.exe",
        "on_path": "Nuke*.exe",
        "filter": "Executable (*.exe)",
    },
    "blender": {
        "name": "Blender",
        "setting": "blender_path",
        "env_path": "Slate_BLENDER_PATH",
        "env_var": "BLENDER_USER_SCRIPTS",
        "plugin_dir": "blender",
        "search": [r"Blender Foundation\Blender*\blender.exe"],
        "exe": r"blender\.exe",
        "on_path": "blender.exe",
        "filter": "Executable (*.exe)",
    },
    "silhouette": {
        "name": "BorisFX Silhouette",
        "setting": "silhouette_path",
        "env_path": "Slate_SILHOUETTE_PATH",
        "env_var": "SFX_SCRIPT_PATH",
        "plugin_dir": "silhouette",
        "search": [r"BorisFX\Silhouette*\silhouette.exe",
                   r"Boris FX\Silhouette*\silhouette.exe",
                   r"SilhouetteFX\Silhouette*\silhouette.exe"],
        "exe": r"silhouette\.exe",
        "on_path": "silhouette.exe",
        "filter": "Executable (*.exe)",
    },
    "natron": {
        "name": "Natron",
        "setting": "natron_path",
        "env_path": "Slate_NATRON_PATH",
        "env_var": "NATRON_PLUGIN_PATH",
        "plugin_dir": "natron",
        "search": [r"INRIA\Natron*\bin\Natron.exe", r"Natron*\bin\Natron.exe"],
        "exe": r"Natron\.exe",
        "on_path": "Natron.exe",
        "filter": "Executable (*.exe)",
    },
    "after_effects": {
        "name": "Adobe After Effects",
        "setting": "after_effects_path",
        "env_path": "Slate_AFTER_EFFECTS_PATH",
        "search": [r"Adobe\Adobe After Effects*\Support Files\AfterFX.exe"],
        "exe": r"AfterFX\.exe",
        "on_path": "AfterFX.exe",
        "filter": "Executable (*.exe)",
    },
    "premiere": {
        "name": "Adobe Premiere Pro",
        "setting": "premiere_path",
        "env_path": "Slate_PREMIERE_PATH",
        "search": [r"Adobe\Adobe Premiere Pro*\Adobe Premiere Pro.exe"],
        "exe": r"Adobe Premiere Pro\.exe",
        "on_path": "Adobe Premiere Pro.exe",
        "filter": "Executable (*.exe)",
    },
}

# How Nuke opens. One program, three products: the flag picks which. NukeX is
# the default because that is what compositors work in; a machine licensed for
# plain Nuke sets "Open Nuke as" to Nuke in Settings (global setting nuke_mode).
NUKE_MODES = {
    "nukex": ("NukeX", ["--nukex"]),
    "nuke": ("Nuke", []),
    "studio": ("Nuke Studio", ["--studio"]),
}
DEFAULT_NUKE_MODE = "nukex"

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)(?:v(\d+))?", re.IGNORECASE)


def parse_version(path) -> Tuple[int, ...]:
    """
    The version an installed program's path names, as numbers.

    Read from the nearest folder that has one - "Nuke15.1v3" is (15, 1, 3),
    "Blender 4.2" is (4, 2), "Adobe After Effects 2024" is (2024,). When the
    file name has a version of its own ("Nuke15.1.exe") it wins, unless a
    folder only adds detail to it (the v3), so a digit in some unrelated
    folder above cannot outrank it. Numbers, not text: as text "Nuke9" sorts
    after "Nuke16" and the oldest install would win.
    """
    p = Path(str(path))
    own = _version_in(p.stem)
    for parent in list(p.parents)[:3]:
        found = _version_in(parent.name)
        if found and (not own or found[:len(own)] == own):
            return found
    return own


def _version_in(name: str) -> Tuple[int, ...]:
    match = _VERSION_RE.search(name)
    if not match:
        return ()
    numbers = [int(n) for n in match.group(1).split(".")]
    if match.group(2):
        numbers.append(int(match.group(2)))
    return tuple(numbers)


def newest(paths: Iterable[str]) -> str:
    """The newest of several installs of one app, or "" when there are none."""
    unique = {}
    for path in paths:
        unique.setdefault(os.path.normcase(os.path.abspath(path)), path)
    if not unique:
        return ""
    return max(unique.values(), key=lambda p: (parse_version(p), p.lower()))


def program_files_roots() -> List[str]:
    """Where Windows installs programs on this machine."""
    roots = []
    for var in ("PROGRAMFILES", "PROGRAMW6432"):
        value = os.environ.get(var, "").strip()
        if value and value not in roots:
            roots.append(value)
    return roots or ["C:\\Program Files"]


def _is_app_exe(dcc_id: str, path: str) -> bool:
    pattern = DCC_APPS[dcc_id]["exe"]
    return bool(re.fullmatch(pattern, os.path.basename(path), re.IGNORECASE)) and os.path.isfile(path)


def find_installed(dcc_id: str, roots: Optional[Iterable[str]] = None) -> str:
    """The newest install of an app under Program Files, or ""."""
    info = DCC_APPS.get(dcc_id)
    if not info:
        return ""
    found = []
    for root in (roots if roots is not None else program_files_roots()):
        for pattern in info.get("search", []):
            found.extend(p for p in glob.glob(os.path.join(root, pattern))
                         if _is_app_exe(dcc_id, p))
    return newest(found)


def find_on_path(dcc_id: str) -> str:
    """The newest copy of an app in a folder on PATH, or ""."""
    info = DCC_APPS.get(dcc_id)
    if not info or not info.get("on_path"):
        return ""
    found = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip()
        if folder:
            found.extend(p for p in glob.glob(os.path.join(folder, info["on_path"]))
                         if _is_app_exe(dcc_id, p))
    return newest(found)


def _legacy_setting(key: str) -> str:
    """A path saved under the shot panel's older config.json key."""
    try:
        from slate.core.infra.global_config import GlobalConfig
        return str(GlobalConfig.get(key, "") or "").strip()
    except Exception as exc:
        logger.debug("Could not read %s from config: %s", key, exc)
        return ""


def get_nuke_mode(config_manager=None) -> str:
    """nukex, nuke or studio - from the global settings; NukeX when unset."""
    try:
        settings = (config_manager or ConfigManager()).settings.get("global_settings", {})
        mode = str(settings.get("nuke_mode", "") or "").strip().lower()
    except Exception:
        mode = ""
    return mode if mode in NUKE_MODES else DEFAULT_NUKE_MODE


def app_label(dcc_id: str, nuke_mode: str = DEFAULT_NUKE_MODE) -> str:
    """What to call an app in a menu: Nuke is named for the mode it opens in."""
    if dcc_id == "nuke":
        return NUKE_MODES.get(nuke_mode, NUKE_MODES[DEFAULT_NUKE_MODE])[0]
    info = DCC_APPS.get(dcc_id)
    return info["name"] if info else dcc_id


def build_command(dcc_id: str, executable: str, file_path=None,
                  nuke_mode: str = DEFAULT_NUKE_MODE) -> List[str]:
    """The command line: the program, then Nuke's mode flag, then the file."""
    command = [str(executable)]
    if dcc_id == "nuke":
        command += NUKE_MODES.get(nuke_mode, NUKE_MODES[DEFAULT_NUKE_MODE])[1]
    if file_path:
        command.append(str(file_path))
    return command


def resolve_executable(dcc_id: str, config_manager=None, save: bool = True) -> str:
    """
    Where an app's program is, without asking anyone. "" when it is not found.

    In order: the path saved in the global settings, the older config.json
    key, the Slate_<APP>_PATH environment variable, the newest install under
    Program Files, then PATH. A saved path that no longer exists - Nuke
    upgraded and the old folder gone - is skipped, and what is found instead
    is saved in its place.
    """
    info = DCC_APPS.get(dcc_id)
    if not info:
        raise ValueError(f"Unsupported DCC: {dcc_id}")
    cm = config_manager or ConfigManager()
    settings_key = f"dcc_path_{dcc_id}"

    # Settings are saved HTML-escaped; a path with an & in it must still match.
    saved = html.unescape(str(cm.settings.get("global_settings", {}).get(settings_key, "") or "")).strip()
    if saved:
        if os.path.isfile(saved):
            return saved
        logger.info("Saved path for %s no longer exists: %s", info["name"], saved)

    legacy = _legacy_setting(info["setting"])
    if legacy and os.path.isfile(legacy):
        return legacy

    env_value = os.environ.get(info["env_path"], "").strip()
    if env_value and os.path.isfile(env_value):
        return env_value

    found = find_installed(dcc_id) or find_on_path(dcc_id)
    if found:
        logger.info(f"Auto-detected {info['name']} at {found}")
        if save:
            _save_path(cm, settings_key, found)
    return found


def _save_path(config_manager, key: str, path: str):
    try:
        config_manager.update_global_settings({key: path})
    except Exception as exc:
        logger.warning("Could not save %s: %s", key, exc)


class DCCLauncher:
    """
    Handles launching of Digital Content Creation (DCC) tools like Nuke, Blender, and Silhouette.
    Injects the Slate environment variables into the process so the DCC plugins can communicate
    back with the SQLite database.
    """

    SUPPORTED_DCCS = DCC_APPS

    def __init__(self, parent_widget=None):
        self.config_manager = ConfigManager()
        self.parent_widget = parent_widget

    @property
    def nuke_mode(self) -> str:
        return get_nuke_mode(self.config_manager)

    def label(self, dcc_id: str) -> str:
        """The app's name for a menu or button - "NukeX" for Nuke by default."""
        return app_label(dcc_id, self.nuke_mode)

    def _get_dcc_executable(self, dcc_id: str) -> str:
        """
        Retrieves the path to the DCC executable.
        If it is not saved or installed anywhere Slate looks, prompts the user via GUI.
        """
        dcc_info = self.SUPPORTED_DCCS.get(dcc_id)
        if not dcc_info:
            raise ValueError(f"Unsupported DCC: {dcc_id}")

        # 1. Settings, config.json, environment, Program Files, PATH
        found = resolve_executable(dcc_id, self.config_manager)
        if found:
            return found

        # 2. If running headless (no UI), we can't ask the user
        if not QApplication.instance():
            logger.error(f"Cannot find {dcc_info['name']} and no UI is available to prompt.")
            return ""

        # 3. Fallback to GUI popup
        QMessageBox.information(
            self.parent_widget,
            "Executable Not Found",
            f"{dcc_info['name']} was not found in the standard locations.\n\nPlease locate the executable (.exe) file.",
        )

        file_path, _ = QFileDialog.getOpenFileName(
            self.parent_widget,
            f"Locate {dcc_info['name']} Executable",
            program_files_roots()[0],
            dcc_info["filter"]
        )

        if file_path and os.path.exists(file_path):
            self._save_dcc_path(f"dcc_path_{dcc_id}", file_path)
            return file_path

        return ""

    def _save_dcc_path(self, key: str, path: str):
        """Saves the detected or selected DCC path back to the global settings."""
        _save_path(self.config_manager, key, path)

    def launch(self, dcc_id: str, shot_id: int, file_path=None):
        """
        Launches the specified DCC with the environment set up for the given shot,
        opening file_path in it when one is given.
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

        # Inject Slate specific variables
        env["SLATE_SHOT_ID"] = str(shot_id)

        # The local database file, when there is one. Only the SQLite backend
        # has a file, and neither backend has a db_path attribute: asking for
        # it raised, and the launch stopped after the program had been found.
        try:
            backend = getattr(database_manager, "backend", None)
            db_path = getattr(backend, "_db_path", None)
        except Exception as exc:
            logger.debug("No local database path to pass on: %s", exc)
            db_path = None
        if db_path:
            env["SLATE_DB_PATH"] = str(db_path)

        # The absolute path to our python source root so plugins can import slate directly
        src_root = str(Path(__file__).parent.parent.parent.resolve())
        env["PYTHONPATH"] = f"{src_root};{env.get('PYTHONPATH', '')}"

        # Inject the DCC specific plugin path (the Adobe apps have none)
        env_var = dcc_info.get("env_var")
        if env_var:
            plugin_dir = Path(__file__).parent.parent / "plugins" / "dcc" / dcc_info["plugin_dir"]
            plugin_dir.mkdir(parents=True, exist_ok=True)

            existing_path = env.get(env_var, "")
            if existing_path:
                env[env_var] = f"{plugin_dir};{existing_path}"
            else:
                env[env_var] = str(plugin_dir)

        # Nuke15.1.exe --nukex path\to\shot.nk
        command = build_command(dcc_id, executable, file_path, self.nuke_mode)
        cwd = str(Path(file_path).parent) if file_path else None
        logger.info(f"Launching {self.label(dcc_id)} for Shot ID {shot_id}: {command}")

        try:
            # Launch asynchronously so it doesn't block the UI
            subprocess.Popen(command, env=env, cwd=cwd)
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
