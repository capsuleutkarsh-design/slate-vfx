import logging
import logging.handlers
import sys
import os
from pathlib import Path
import socket

# Try to import GlobalConfig, but handle import loops
try:
    from .global_config import GlobalConfig
except ImportError:
    GlobalConfig = None

class NetworkLogHandler(logging.FileHandler):
    """
    Non-blocking log handler that writes to the network.
    Fail-safe: If network write fails, it just logs an internal error to console 
    and stops trying for this session to avoid app freeze.
    """
    def __init__(self, filename, mode='a', encoding='utf-8', delay=False):
        self.disabled = False
        try:
            super().__init__(filename, mode, encoding, delay)
        except Exception as e:
            # Downgrade to warning without traceback to avoid spamming the console on startup
            # when the network drive is unavailable.
            logging.warning(f"Network Log Init Failed: {e} (Continuing with local logs)")
            self.disabled = True
            raise

    def emit(self, record):
        if self.disabled: return
        try:
            super().emit(record)
        except Exception:
            self.handleError(record)
            self.disabled = True # Disable on first error

def setup_logging(app_name="App"):
    """
    Configures Hybrid Logging:
    1. Console (Standard Output)
    2. Local File (%LOCALAPPDATA%/Slate/Logs/active.log)
    3. Network File (X:/Extra/UT_Central/Logs/[Hostname]_[User].log)
    """
    
    # 1. Define Log Level
    log_level = logging.INFO
    
    # 2. Local Path
    local_dir = Path(os.getenv('LOCALAPPDATA')) / "Slate" / "Logs"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_file = local_dir / "latest.log"
    
    # 3. Network Path
    network_file = None
    if GlobalConfig:
        try:
            server_root = GlobalConfig.server_root()
            net_log_dir = server_root / "Logs"
            try:
                net_log_dir.mkdir(parents=True, exist_ok=True)
                hostname = socket.gethostname()
                username = os.getenv('USERNAME', 'Unknown')
                # Filename: [HOSTNAME]_[USERNAME].log
                network_file = net_log_dir / f"{hostname}_{username}.log"
            except OSError as e:
                logging.debug("Could not create Network Logs directory: %s", e)
        except Exception as exc:
            logging.debug("Network log path unavailable, falling back to local logging: %s", exc)

    # 4. Handlers
    handlers = []
    
    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    handlers.append(console)
    
    # Local File (Rotating)
    try:
        from logging.handlers import RotatingFileHandler
        local_handler = RotatingFileHandler(local_file, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
        local_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
        handlers.append(local_handler)
    except Exception as e:
        logging.exception(f"Local logging failed: {e}")

    # Network File (Fail-safe)
    if network_file:
        try:
            net_handler = NetworkLogHandler(network_file)
            net_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
            handlers.append(net_handler)
            logging.info(f"Network Logging Active: {network_file}")
        except Exception as e:
            logging.info(f"Network logging skipped: {e}")

    # 5. Apply Config
    # Force UTF-8 for console on Windows to prevent 'charmap' codec errors
    if sys.platform == "win32":
        try:
            # Python 3.7+
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8')
            # Fallback for older python or wrapped streams
            elif hasattr(sys.stdout, 'buffer'):
                import io
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except Exception:
            # If standard streams are replaced/mocked (e.g. frozen app), just warn
            pass

    # Remove existing handlers to avoid duplicates on reload
    root_log = logging.getLogger()
    if root_log.handlers:
        for h in root_log.handlers: 
            root_log.removeHandler(h)
            
    logging.basicConfig(
        level=log_level,
        handlers=handlers,
        force=True # Py3.8+
    )
    
    logging.info(f"Logging Initialized: {app_name}")
    if network_file: logging.info(f"Remote Log Mirror: {network_file}")
