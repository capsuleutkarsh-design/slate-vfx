"""
PgBouncer: the receptionist in front of the database.

Every copy of Slate keeps its own connections open to PostgreSQL. That is fine
for fifteen people and impossible for a hundred and fifty: the database accepts
100 connections, and 150 machines want at least 150 before anyone does any work.

PgBouncer takes all those client connections itself and shares a much smaller
set of real database connections between them. Nothing changes for the people
using the software.

    150 copies of Slate
            |
        PgBouncer          accepts everyone, port 6432
            |
        PostgreSQL         ~40 connections in use, limit 100

This module manages it the same way DatabaseEngine manages PostgreSQL: it
writes the configuration, starts it, stops it, and reports whether it is there.
If the PgBouncer program is not installed, everything here reports "not
installed" and the server carries on exactly as before - clients then talk to
PostgreSQL directly, which is what they did until now.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional


logger = logging.getLogger(__name__)

# Where clients connect. PostgreSQL keeps its own port and is moved behind
# the loopback interface, so the only way in from the network is through here.
DEFAULT_LISTEN_PORT = 6432

# How many real database connections the pool may hold. Deliberately well under
# PostgreSQL's own limit of 100, leaving room for the server's own tools and
# the connections reserved for an administrator.
DEFAULT_POOL_SIZE = 40
RESERVE_POOL_SIZE = 10

# How many clients may be connected at once. 150 machines at 2 connections
# each is 300; this leaves generous headroom.
MAX_CLIENT_CONN = 600


class PgBouncerEngine:
    """Manages the bundled PgBouncer instance."""

    def __init__(self, data_dir: str, db_port: int = 5440,
                 listen_port: int = DEFAULT_LISTEN_PORT,
                 dbname: str = "slate", db_user: str = "ut_vfx_app",
                 db_password: str = ""):
        self.data_dir = Path(data_dir)
        self.db_port = int(db_port)
        self.listen_port = int(listen_port)
        self.dbname = dbname
        self.db_user = db_user
        self.db_password = db_password

        base_dir = Path(__file__).parent.parent
        if getattr(sys, "frozen", False):
            base_dir = Path(getattr(sys, "_MEIPASS", sys.executable)) / "slate_server"

        self.bin_dir = base_dir / "bin" / "pgbouncer"
        self.exe = self.bin_dir / "pgbouncer.exe"

        # Configuration and logs live beside the database, not in the program
        # folder, so an update to the software never overwrites them.
        self.conf_dir = self.data_dir.parent / "pgbouncer"
        self.ini_path = self.conf_dir / "pgbouncer.ini"
        self.userlist_path = self.conf_dir / "userlist.txt"
        self.log_path = self.conf_dir / "pgbouncer.log"
        self.pid_path = self.conf_dir / "pgbouncer.pid"

        self._process: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_installed(self) -> bool:
        """Whether the PgBouncer program is present."""
        return self.exe.exists()

    def is_ready(self) -> bool:
        """Whether PgBouncer is accepting connections on its port."""
        return self._port_answers(self.listen_port)

    @staticmethod
    def _port_answers(port: int, host: str = "127.0.0.1") -> bool:
        import socket
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    def status_text(self) -> str:
        """One line a person can read on the server dashboard."""
        if not self.is_installed():
            return ("Not installed - clients connect straight to the database. "
                    "Fine up to about 40 people.")
        if self.is_ready():
            return f"Running on port {self.listen_port} - pooling connections."
        return "Installed but not running."

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _scram_verifiers(self, psql_exe: Path) -> dict:
        """
        The stored password verifiers, read out of PostgreSQL.

        PgBouncer checks a client's password against these. Taking them from
        the database means there is no second copy of anyone's password to keep
        in step, and no plain-text password in the user list.
        """
        if not Path(psql_exe).exists():
            return {}

        import os
        env = dict(os.environ)
        if self.db_password:
            env["PGPASSWORD"] = self.db_password

        cmd = [
            str(psql_exe), "-h", "127.0.0.1", "-p", str(self.db_port),
            "-U", "postgres", "-d", self.dbname, "-tAF", "\x1f", "-c",
            "SELECT rolname, rolpassword FROM pg_authid "
            "WHERE rolpassword IS NOT NULL AND rolcanlogin",
        ]
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                 env=env, creationflags=creationflags)
        except Exception as exc:
            logger.warning("Could not read password verifiers: %s", exc)
            return {}

        verifiers = {}
        for line in (out.stdout or "").splitlines():
            if "\x1f" not in line:
                continue
            name, secret = line.split("\x1f", 1)
            name, secret = name.strip(), secret.strip()
            if name and secret:
                verifiers[name] = secret
        return verifiers

    def write_config(self, psql_exe: Optional[Path] = None) -> bool:
        """Write pgbouncer.ini and userlist.txt. Returns False if it could not."""
        try:
            self.conf_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Could not create %s: %s", self.conf_dir, exc)
            return False

        verifiers = self._scram_verifiers(psql_exe) if psql_exe else {}
        if not verifiers:
            logger.warning(
                "No password verifiers were read from the database, so PgBouncer "
                "would refuse every client. Not writing a user list."
            )
            return False

        lines = [
            "; Written by Slate Central Server. Edits are overwritten on restart.",
            ";",
            "; This file lets PgBouncer check a client's password. The values are",
            "; the same one-way verifiers PostgreSQL stores - they cannot be turned",
            "; back into passwords.",
            "",
        ]
        for name, secret in sorted(verifiers.items()):
            lines.append('"%s" "%s"' % (name, secret))
        self.userlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ini = self._render_ini()
        self.ini_path.write_text(ini, encoding="utf-8")
        self._restrict_permissions(self.ini_path)
        self._restrict_permissions(self.userlist_path)
        return True

    def _render_ini(self) -> str:
        # PgBouncer wants forward slashes even on Windows.
        def p(path: Path) -> str:
            return str(path).replace("\\", "/")

        password_clause = (
            f" password={self.db_password}" if self.db_password else ""
        )

        return f"""; PgBouncer - connection pooling for Slate
; Written by Slate Central Server. Edits here are overwritten on restart;
; change the server settings instead.
;
; What this does: every copy of Slate connects here instead of straight to
; PostgreSQL. PgBouncer keeps a small number of real database connections and
; shares them out, so 150 people do not need 150 connections.

[databases]
; Clients ask for "{self.dbname}"; PgBouncer reaches the real database on the
; loopback interface, where only this machine can reach it.
{self.dbname} = host=127.0.0.1 port={self.db_port} dbname={self.dbname} user={self.db_user}{password_clause}

[pgbouncer]
listen_addr = *
listen_port = {self.listen_port}

; Clients prove who they are against the verifiers copied from PostgreSQL.
auth_type = scram-sha-256
auth_file = {p(self.userlist_path)}

; Transaction pooling: a real database connection is handed back as soon as
; each piece of work finishes, which is what makes the sharing effective. It
; suits this software because its database work is short and self-contained.
pool_mode = transaction

; In transaction pooling the reset query is unnecessary - each transaction
; ends cleanly on its own - and running one would cost a round trip every time.
server_reset_query =

; Clients may connect this many times in total...
max_client_conn = {MAX_CLIENT_CONN}
; ...while this many real database connections are actually used.
default_pool_size = {DEFAULT_POOL_SIZE}
reserve_pool_size = {RESERVE_POOL_SIZE}
reserve_pool_timeout = 3

; Let go of database connections that have been sitting unused.
server_idle_timeout = 300
server_lifetime = 3600

; Some client libraries send settings PgBouncer does not need to police.
ignore_startup_parameters = extra_float_digits

; Who may look at PgBouncer's own statistics.
admin_users = postgres
stats_users = postgres, {self.db_user}

logfile = {p(self.log_path)}
pidfile = {p(self.pid_path)}
"""

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        """Keep the configuration readable only by administrators."""
        if sys.platform != "win32":
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return
        try:
            import getpass

            # The account running the server has to keep access: PgBouncer runs
            # as that account and must read its own configuration. Granting
            # only SYSTEM and Administrators locks out the very process that
            # needs the file, and PgBouncer then exits without saying why.
            grants = ["/grant:r", "SYSTEM:(F)", "/grant:r", "Administrators:(F)"]
            try:
                grants += ["/grant:r", f"{getpass.getuser()}:(R,W)"]
            except Exception:
                pass

            subprocess.run(
                ["icacls", str(path), "/inheritance:r"] + grants,
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            logger.debug("Could not tighten permissions on %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    def start(self, progress_callback: Optional[Callable[[str], None]] = None,
              psql_exe: Optional[Path] = None) -> bool:
        """
        Start PgBouncer. Returns False if it is not installed or would not start.

        A failure here is never fatal: the server keeps running and clients
        fall back to talking to the database directly.
        """
        def say(message):
            logger.info(message)
            if progress_callback:
                progress_callback(message)

        if not self.is_installed():
            say("PgBouncer is not installed - clients will connect directly.")
            return False

        if self.is_ready():
            say("PgBouncer is already running.")
            return True

        if not self.write_config(psql_exe=psql_exe):
            say("PgBouncer could not be configured - clients will connect directly.")
            return False

        say("Starting PgBouncer...")
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._process = subprocess.Popen(
                [str(self.exe), "-q", str(self.ini_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as exc:
            logger.exception("PgBouncer would not start: %s", exc)
            say("PgBouncer would not start - clients will connect directly.")
            return False

        # Give it a moment to bind its port.
        for _ in range(20):
            if self.is_ready():
                say(f"PgBouncer is pooling connections on port {self.listen_port}.")
                return True
            if self._process.poll() is not None:
                break
            time.sleep(0.25)

        say("PgBouncer did not come up - clients will connect directly. "
            f"See {self.log_path}")
        return False

    def ensure_running(self, psql_exe: Optional[Path] = None) -> bool:
        """
        Bring PgBouncer back if it has stopped. Safe to call repeatedly.

        The server calls this from the same timer that refreshes its dashboard,
        so a pooler that falls over is back within seconds instead of leaving
        every workstation to connect straight to the database - which is the
        very thing it exists to prevent.
        """
        if not self.is_installed():
            return False
        if self.is_ready():
            return True

        logger.warning("PgBouncer stopped answering; starting it again.")
        return self.start(psql_exe=psql_exe)

    def stop(self, progress_callback: Optional[Callable[[str], None]] = None) -> None:
        """Stop PgBouncer, if this server started it."""
        if self._process is None:
            return
        if progress_callback:
            progress_callback("Stopping PgBouncer...")
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        except Exception as exc:
            logger.debug("PgBouncer stop: %s", exc)
        finally:
            self._process = None
