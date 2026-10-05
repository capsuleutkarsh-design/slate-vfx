"""
Where everything is on the server PC - found even when the settings are broken.

The recovery tool cannot assume the Slate Server window works, so it finds the
database on its own, in this order:

    1. a folder given on the command line (--data-dir)
    2. SLATE_DB_PATH
    3. slate_server_config.json, if it can be read
    4. slate_server_last_good.json - written at every successful start
    5. <server home>\\LocalDatabase

The port is found the same way, ending with the port line PostgreSQL's own
postgresql.conf carries (the server keeps it there), then 5440.

Everything the recovery tool keeps lives beside the database, in
<data folder's parent>\\slate_recovery, readable only by administrators and the
server's own account:

    recovery_key.json        the Recovery Key's scrypt hash (never the key)
    key_attempts.json        wrong-key count, for slowing guesses down
    trust_window.json        present only while a trust window is open
    security_switches.json   switches forced off on this PC
    snapshots\\NNNN_...       automatic copies taken before a security change
    recovery.log             what the recovery tool did (no passwords, no key)
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from slate_server.core import server_home as home

logger = logging.getLogger(__name__)

RECOVERY_DIR_NAME = "slate_recovery"


class NotServerPC(Exception):
    """The recovery tool was started somewhere other than the database's own PC."""


def bundled_bin_dir() -> Path:
    base = Path(__file__).resolve().parents[2]            # slate_server
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", sys.executable)) / "slate_server"
    return base / "bin" / "pgsql" / "bin"


@dataclass
class ServerLayout:
    data_dir: Path
    port: int = 5440
    pooler_port: int = 6432
    server_home: Optional[Path] = None
    settings_path: Optional[Path] = None
    credentials_path: Optional[Path] = None
    bin_dir: Path = field(default_factory=bundled_bin_dir)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self):
        # This database's passwords are kept beside it (db_credentials.store).
        from slate_server.core import db_credentials
        db_credentials.use_data_dir(self.data_dir)

    # ------------------------------------------------------------ places
    @property
    def secrets_file(self) -> Path:
        from slate_server.core.db_credentials import SECRETS_NAME
        return self.recovery_dir / SECRETS_NAME

    @property
    def recovery_dir(self) -> Path:
        return self.data_dir.parent / RECOVERY_DIR_NAME

    @property
    def key_file(self) -> Path:
        return self.recovery_dir / "recovery_key.json"

    @property
    def attempts_file(self) -> Path:
        return self.recovery_dir / "key_attempts.json"

    @property
    def marker_file(self) -> Path:
        return self.recovery_dir / "trust_window.json"

    @property
    def switches_file(self) -> Path:
        return self.recovery_dir / "security_switches.json"

    @property
    def snapshots_dir(self) -> Path:
        return self.recovery_dir / "snapshots"

    @property
    def log_file(self) -> Path:
        return self.recovery_dir / "recovery.log"

    @property
    def state_file(self) -> Path:
        return self.recovery_dir / "server_state.json"

    @property
    def pg_hba(self) -> Path:
        return self.data_dir / "pg_hba.conf"

    @property
    def pg_conf(self) -> Path:
        return self.data_dir / "postgresql.conf"

    @property
    def pgbouncer_dir(self) -> Path:
        return self.data_dir.parent / "pgbouncer"

    @property
    def pg_log(self) -> Path:
        return self.data_dir.parent / "pg_server.log"

    def engine(self):
        """A DatabaseEngine for this cluster (helpers only - nothing is started)."""
        from slate_server.core.db_engine import DatabaseEngine
        engine = DatabaseEngine(str(self.data_dir), port=int(self.port))
        engine.bin_dir = self.bin_dir
        return engine


def _port_from_conf(conf: Path) -> Optional[int]:
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = None
    for line in text.splitlines():
        match = re.match(r"\s*port\s*=\s*(\d+)", line)
        if match:
            found = int(match.group(1))           # the last one wins, as in PostgreSQL
    return found


def credentials_file(key: str = "db_password") -> Path:
    """
    The settings file the server's database password should be written to: the
    strongest file the server reads that already carries it, otherwise the one
    the Settings screen writes.
    """
    from slate_server.core import db_credentials
    best = None
    for path in db_credentials._config_layers():
        try:
            if path.is_file():
                import json
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict) and data.get(key) not in (None, ""):
                    best = path
        except Exception:
            continue
    if best is not None:
        return best
    from slate.core.infra.local_secrets import local_config_path
    return local_config_path()


def find_layout(data_dir=None, port=None, server_home_dir=None,
                credentials_path=None) -> ServerLayout:
    notes = []
    root = Path(server_home_dir or home.server_home())
    settings_file = Path(home.settings_path(str(root)))
    settings = {}
    try:
        settings = home.read_settings(settings_file)
        if not settings_file.exists():
            notes.append("No server settings file at %s." % settings_file)
    except Exception as exc:
        notes.append("The server settings file %s cannot be read (%s)." % (settings_file, exc))
    last = home.last_good(str(root))

    chosen = (data_dir or os.environ.get("SLATE_DB_PATH") or settings.get("db_path")
              or last.get("db_path") or home.default_data_dir(str(root)))
    folder = Path(str(chosen))
    found_port = (port or settings.get("port") or last.get("port")
                  or _port_from_conf(folder / "postgresql.conf") or 5440)
    pooler = settings.get("pooler_port") or last.get("pooler_port") or 6432
    try:
        creds = Path(credentials_path) if credentials_path else credentials_file()
    except Exception:
        creds = None
    return ServerLayout(data_dir=folder, port=int(found_port), pooler_port=int(pooler),
                        server_home=root, settings_path=settings_file,
                        credentials_path=creds, notes=notes)


# ------------------------------------------------------------ the server PC

def _is_network_path(path: Path) -> bool:
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    if sys.platform != "win32":
        return False
    drive = os.path.splitdrive(os.path.abspath(text))[0]
    if not drive:
        return False
    try:
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
    except Exception:
        return False


def server_pc_problem(layout: ServerLayout) -> str:
    """'' on the database's own PC, run by somebody who can use its folder; else why not."""
    data = layout.data_dir
    if _is_network_path(data):
        return ("%s is on a network drive. The recovery tool only runs on the PC that "
                "holds the database, against its local folder." % data)
    if not (data / "PG_VERSION").exists():
        return ("There is no Slate database in %s. Run this on the server PC, or give "
                "the database folder with --data-dir." % data)
    if not (layout.bin_dir / "pg_ctl.exe").exists() and not (layout.bin_dir / "pg_ctl").exists():
        return "The database programs are not on this PC (%s)." % layout.bin_dir
    hba = layout.pg_hba
    if not hba.exists():
        return "%s is missing." % hba
    if not (os.access(hba, os.R_OK) and os.access(hba, os.W_OK)):
        return ("This Windows account cannot read and change %s. Sign in to the server "
                "PC as the account that runs Slate Server, or as an administrator." % hba)
    return ""


def assert_server_pc(layout: ServerLayout) -> None:
    problem = server_pc_problem(layout)
    if problem:
        raise NotServerPC(problem)


def is_windows_admin() -> bool:
    """Whether this process holds Windows administrator rights (elevated)."""
    if sys.platform != "win32":
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
