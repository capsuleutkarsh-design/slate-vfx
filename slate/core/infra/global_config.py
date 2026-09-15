import json
import os
import sys
import logging
import time
from pathlib import Path

class GlobalConfig:
    """
    Centralized configuration manager.
    Loads paths and settings from a local config.json file,
    falling back to hardcoded defaults if not found.
    """
    _instance = None

    DEFAULTS = {
        "SERVER_ROOT": os.environ.get("SLATE_STUDIO_ROOT", str(Path.home() / "RuntimeData" / "Slate_Central")),
        "LOG_LEVEL": "INFO",
        "DEVELOPER_MODE": False,
        "db_mode": "sqlite",
        "allow_db_fallback": True,
        "enable_exr_loading": False,
    }

    def __init__(self):
        # Priority: LOCALAPPDATA (Machine Config) -> RuntimeData (Legacy User Config)
        self.local_app_data = Path(os.getenv('LOCALAPPDATA')) / "Slate"
        self.config_path = self.local_app_data / "config.json"

        self.legacy_config_path = Path.home() / "RuntimeData" / "Slate" / "config.json"

        self.data = self.DEFAULTS.copy()


        # 0. Load Bundled Defaults with Multiple Path Discovery
        import logging

        search_paths = []

        # A. Executable Directory (Frozen)
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)

            # Where the build actually puts it. The spec ships
            # slate/default_config.json, so in a frozen build it lands under a
            # "slate" folder - and every path looked at here was the bare
            # filename at the top. So no frozen client has ever read its own
            # bundled settings: it worked only on machines that already had a
            # per-machine config, and a workstation installed from scratch had
            # no database password, no user and no database name, fell back to
            # the local SQLite defaults, and never reached the studio at all.
            if hasattr(sys, '_MEIPASS'):
                search_paths.append(Path(sys._MEIPASS) / "slate" / "default_config.json")
                search_paths.append(Path(sys._MEIPASS) / "default_config.json")
            search_paths.append(Path(base_dir) / "slate" / "default_config.json")
            search_paths.append(Path(base_dir) / "default_config.json")
            # One level up, for a layout that keeps the exe in a bin folder.
            search_paths.append(Path(base_dir).parent / "default_config.json")

        # B. Source Directory (Dev Environment fallback). This one is already
        # inside the slate package, which is why it works in a checkout and is
        # exactly what the frozen paths above were missing.
        try:
            source_root = Path(__file__).resolve().parent.parent.parent
            search_paths.append(source_root / "default_config.json")
        except (RuntimeError, ValueError, OSError) as exc:
            logging.debug("GlobalConfig: source root discovery failed (%s)", exc)

        config_loaded = False
        for config_path in search_paths:
            if config_path.exists():
                try:
                    logging.info(f"Loading bundled config from: {config_path}")
                    with open(config_path, 'r') as f:
                        loaded = json.load(f)
                        self.data.update(loaded)
                        logging.info(f"Configuration Loaded. DB_HOST: {self.data.get('db_host')}")
                        config_loaded = True
                        break # Stop after finding the first valid config
                except Exception as e:
                    logging.exception(f"Failed to load config from {config_path}: {e}")

        if not config_loaded:
             logging.warning("No default_config.json found in search paths.")

        # Before anything is read: if an installer has just been run and gave
        # answers newer than the settings on this machine, fold them in. Without
        # this the path somebody types into the installer is read, ranked below
        # the per-machine settings, and ignored - which on any machine that has
        # had Slate before means the installer appears to accept a path and
        # nothing uses it.
        try:
            from slate.core.infra.install_handoff import apply_install_answers
            apply_install_answers()
        except Exception as exc:
            logging.debug("Install answers were not applied: %s", exc)

        self.load()

    def load(self):
        """
        Load configuration, lowest priority first.

        The order matters and it used to be wrong. slate/config.json - the file
        setup.bat writes, and the only one that holds the studio's password - was
        read first, which made it the weakest: a stale copy under LOCALAPPDATA
        overrode the details the installer had just been given. It is read last
        now, so the file the installer and the Settings tab both write is the one
        that wins.
        """
        self.dev_config_path = Path(__file__).resolve().parent.parent.parent / "config.json"

        # 2. Try Client Config Override
        client_configs = []
        if getattr(sys, 'frozen', False):
            client_configs.append(Path(sys._MEIPASS) / "client_config.json")
            client_configs.append(Path(sys.executable).parent / "client_config.json")

        client_configs.append(Path.cwd() / "client_config.json")
        try:
            source_root = Path(__file__).parent.parent.parent.parent
            client_configs.append(source_root / "client_config.json")
        except (RuntimeError, ValueError, OSError) as exc:
            pass

        for p in client_configs:
            if p.exists():
                try:
                    with open(p, 'r') as f:
                        client_data = json.load(f)
                        self.data.update(client_data)
                    break
                except Exception as e:
                    pass

        # 3. Try Legacy RuntimeData Config
        if self.legacy_config_path.exists():
            try:
                with open(self.legacy_config_path, 'r') as f:
                    self.data.update(json.load(f))
            except Exception as e:
                pass

        # 4. Try Machine Config (Local AppData)
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    self.data.update(json.load(f))
            except Exception as e:
                logging.warning("GlobalConfig: could not load local config %s (%s)", self.config_path, e)

        # 5. The local config setup.bat writes - HIGHEST PRIORITY.
        #
        # Last, because it is the one file a person is told to edit and the one
        # the installer writes. Read first, as it used to be, it was the weakest
        # of the four: re-running setup.bat appeared to do nothing because a
        # months-old copy under LOCALAPPDATA was still winning.
        if self.dev_config_path.exists():
            try:
                with open(self.dev_config_path, 'r') as f:
                    self.data.update(json.load(f))
            except Exception as e:
                logging.warning("GlobalConfig: could not load %s (%s)",
                                self.dev_config_path, e)

        # 5. Zero-Config Network Discovery
        if not self.data.get('db_host'):
            try:
                from .network_discovery import discover_server
                logging.info("GlobalConfig: No db_host configured. Attempting UDP Network Discovery...")
                server_ip, db_port = discover_server(timeout=1.5)
                if server_ip:
                    self.data['db_host'] = server_ip
                    self.data['db_port'] = db_port
                    self.save()
                    logging.info(f"GlobalConfig: Successfully auto-discovered Server at {server_ip}:{db_port}!")
                else:
                    logging.info("GlobalConfig: Network Discovery found no server. Falling back to local.")
            except Exception as e:
                logging.warning(f"GlobalConfig: Network Discovery failed: {e}")

    @classmethod
    def get(cls, key, default=None):
        """Get a specific config value."""
        if cls._instance is None:
            cls._instance = GlobalConfig()
        if key in cls._instance.data:
            return cls._instance.data.get(key)
        if default is not None:
            return default
        return cls.DEFAULTS.get(key)

    @classmethod
    def set(cls, key, value):
        """Set a config value and save to disk."""
        if cls._instance is None:
            cls._instance = GlobalConfig()
        cls._instance.data[key] = value
        cls._instance.save()

    def save(self):
        """Save configuration to JSON file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            logging.warning("GlobalConfig: could not save config to %s (%s)", self.config_path, e)


    @classmethod
    def server_root(cls) -> Path:
        """Get the server root path. Warns if using local fallback."""
        path_str = str(cls.get("SERVER_ROOT") or "").strip()

        # Blank is "nobody has said where the studio is", and it needs saying
        # differently from "the drive is unreachable". It also cannot be handed
        # to Path: Path("") is Path("."), which exists - so an unset value used
        # to resolve silently to whatever directory the program was started
        # from, and everything written there looked like it had worked.
        if not path_str:
            if not getattr(cls, "_no_root_warned", False):
                cls._no_root_warned = True
                logging.warning(
                    "No studio folder is configured (SERVER_ROOT). Working "
                    "locally instead - nothing will be shared between machines. "
                    "Set it in Settings.")
            local_root = Path.home() / "RuntimeData" / "Slate_Central"
            local_root.mkdir(parents=True, exist_ok=True)
            return local_root

        path = Path(path_str)

        # Check if path exists
        if not path.exists():
            # Warn once per path, then throttle repeated spam.
            now = time.time()
            last_path = getattr(cls, "_last_network_log_path", None)
            last_ts = getattr(cls, "_last_network_log_ts", 0.0)
            if last_path != str(path) or (now - float(last_ts)) > 120.0:
                logging.critical(f"[ERR] NETWORK DRIVE NOT ACCESSIBLE: {path}")
                logging.critical("[WARN] Using LOCAL FALLBACK - Data will NOT sync across PCs!")
                logging.critical("[TIP] Fix: Map network drive or check server connectivity")
                cls._last_network_log_path = str(path)
                cls._last_network_log_ts = now
            else:
                logging.debug("Network drive still inaccessible: %s", path)

            # Show dialog warning (only once per session)
            if not hasattr(cls, '_network_warning_shown'):
                cls._network_warning_shown = True
                try:
                    # Delayed import to avoid circular dependencies
                    from PySide6.QtWidgets import QMessageBox, QApplication

                    # Only show dialog if QApplication exists
                    if QApplication.instance():
                        msg = QMessageBox()
                        msg.setIcon(QMessageBox.Icon.Warning)
                        msg.setWindowTitle("[WARN] Network Drive Not Found")
                        msg.setText(f"Server path not accessible:\n\n{path}")
                        msg.setInformativeText(
                            "Using LOCAL fallback mode.\n\n"
                            "[WARN] Attendance data will NOT sync across PCs!\n"
                            "[WARN] User database will NOT sync!\n\n"
                            "Contact IT to map the network drive."
                        )
                        msg.setStandardButtons(QMessageBox.Ok)
                        msg.exec()
                except Exception as e:
                    logging.debug(f"Could not show network warning dialog: {e}")

            local_root = Path.home() / "RuntimeData" / "Slate_Central"
            local_root.mkdir(parents=True, exist_ok=True)
            return local_root

        # Reset throttle state once drive becomes available again.
        cls._last_network_log_path = None
        cls._last_network_log_ts = 0.0
        return path

    @classmethod
    def local_cache_dir(cls) -> Path:
        """Get the absolute path to the local cache directory."""
        path = Path(os.getenv('LOCALAPPDATA')) / "Slate" / "Cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def central_thumbnails_dir(cls) -> Path:
        """Get the absolute path to the central thumbnails directory."""
        path = cls.server_root() / "Thumbnails"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logging.debug("GlobalConfig: could not create central thumbnails dir %s (%s)", path, exc)
        return path

    @classmethod
    def is_developer(cls) -> bool:
        """Check if user is allowed to Ingest/Delete assets."""
        if cls.get("DEVELOPER_MODE", False):
            return True

        # Auto-detect Developer Machine (CAPINT)
        try:
             import socket
             import os
             if socket.gethostname().upper() == "CAPINT":
                 return True
             # Also allow if user is 'capadmin' (just in case hostname varies but user is consistent)
             if os.getenv("USERNAME", "").lower() == "capadmin":
                 return True
        except Exception as exc:
            logging.debug("GlobalConfig: developer auto-detect failed (%s)", exc)

        return False

    @classmethod
    def abstract_path(cls, path: str) -> str:
        """
        Convert an absolute path to an abstract path using environment variables/config.
        Example: "X:/Extra/Slate_Central/Assets/foo.mov" -> "$SERVER/Assets/foo.mov"
        """
        if not path:
            return ""
        try:
            p = str(Path(path)).replace("\\", "/") # Normalize
            server_root = str(cls.server_root()).replace("\\", "/")

            if p.lower().startswith(server_root.lower()):
                return p.replace(server_root, "$SERVER", 1)

            return p
        except Exception as exc:
            logging.debug("GlobalConfig: abstract_path failed for %s (%s)", path, exc)
            return path

    @classmethod
    def resolve_path(cls, path: str) -> str:
        """
        Convert an abstract path to an absolute path.
        Example: "$SERVER/Assets/foo.mov" -> "X:/Extra/Slate_Central/Assets/foo.mov"
        """
        if not path:
            return ""
        try:
            p = str(path).replace("\\", "/")
            if "$SERVER" in p:
                server_root = str(cls.server_root()).replace("\\", "/")
                return p.replace("$SERVER", server_root, 1)
            return str(Path(p).resolve())
        except Exception as exc:
            logging.debug("GlobalConfig: resolve_path failed for %s (%s)", path, exc)
            return path

    @classmethod
    def get_db_mode(cls) -> str:
        """Normalized db mode. Supported: sqlite, postgres."""
        raw = str(cls.get("db_mode", "sqlite") or "sqlite").strip().lower()
        if raw in ("postgres", "postgresql", "pg"):
            return "postgres"
        return "sqlite"

    @classmethod
    def allow_db_fallback(cls) -> bool:
        """Whether postgres mode may fallback to sqlite when unavailable."""
        raw = cls.get("allow_db_fallback", True)
        if isinstance(raw, bool):
            return raw
        text = str(raw or "").strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        return True

    @classmethod
    def exr_loading_enabled(cls) -> bool:
        """
        EXR loading switch with environment override.
        Env override: SLATE_ENABLE_EXR_LOADING=1|0
        """
        env = str(os.getenv("SLATE_ENABLE_EXR_LOADING", "") or "").strip().lower()
        if env in ("1", "true", "yes", "on"):
            return True
        if env in ("0", "false", "no", "off"):
            return False
        raw = cls.get("enable_exr_loading", False)
        if isinstance(raw, bool):
            return raw
        text = str(raw or "").strip().lower()
        return text in ("1", "true", "yes", "on")
