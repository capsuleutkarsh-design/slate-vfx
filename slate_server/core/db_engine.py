import os
import sys
import time
import logging
import subprocess
import zipfile
import urllib.request
from pathlib import Path
import atexit
import threading
import shutil

class DatabaseEngine:
    """
    Manages the embedded PostgreSQL instance.
    Handles initializing the cluster and starting/stopping.
    Assumes PostgreSQL binaries are bundled in the `bin/pgsql` directory.
    """
    
    def __init__(self, data_dir: str, port: int = 5440):
        self.data_dir = Path(data_dir)
        self.port = port
        
        # Resolve the bundled bin directory
        # When running in dev: slate_server/bin/pgsql/bin
        # When running compiled: _MEIPASS/slate_server/bin/pgsql/bin
        base_dir = Path(__file__).parent.parent
        if getattr(sys, 'frozen', False):
            base_dir = Path(getattr(sys, '_MEIPASS', sys.executable)) / "slate_server"
            
        self.bin_dir = base_dir / "bin" / "pgsql" / "bin"
        
        # Ensure base directories exist
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        
    def is_installed(self) -> bool:
        """Check if Postgres binaries exist"""
        return (self.bin_dir / "postgres.exe").exists()

    def is_initialized(self) -> bool:
        """Check if the data directory has been initialized"""
        return (self.data_dir / "PG_VERSION").exists()

    def is_ready(self) -> bool:
        """Checks if PostgreSQL is currently accepting connections on self.port."""
        if not (self.data_dir / "postmaster.pid").exists():
            return False

        pg_isready_exe = self.bin_dir / "pg_isready.exe"
        if not pg_isready_exe.exists():
            return False
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            res = subprocess.run(
                [str(pg_isready_exe), "-h", "127.0.0.1", "-p", str(self.port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                creationflags=creationflags
            )
            return res.returncode == 0
        except Exception:
            return False

    def _clean_stale_pid_file(self):
        """Removes postmaster.pid if the recorded process is no longer alive."""
        pid_file = self.data_dir / "postmaster.pid"
        if not pid_file.exists():
            return
            
        if self.is_ready():
            return  # Server is actually running and responding

        pid = None
        try:
            with open(pid_file, 'r') as f:
                first_line = f.readline().strip()
                if first_line:
                    pid = int(first_line)
        except Exception:
            pid = None

        is_alive = False
        if pid:
            if sys.platform == 'win32':
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if h:
                        kernel32.CloseHandle(h)
                        is_alive = True
                except Exception:
                    is_alive = False
            else:
                try:
                    os.kill(pid, 0)
                    is_alive = True
                except OSError:
                    is_alive = False

        if not is_alive:
            try:
                pid_file.unlink(missing_ok=True)
                logging.info("Removed stale postmaster.pid file.")
            except Exception as e:
                logging.warning(f"Could not remove stale postmaster.pid: {e}")

    def initialize_database(self, progress_callback=None):
        """Runs initdb.exe to create a new cluster."""
        if self.is_initialized():
            return True
            
        if progress_callback:
            progress_callback("Initializing Database Cluster...")

        initdb_exe = str(self.bin_dir / "initdb.exe")
        cmd = [
            initdb_exe,
            "-D", str(self.data_dir),
            "-U", "postgres",
            "-A", "trust", # Trust local connections
            "-E", "utf8"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self._configure_network_access()
            return True
        except subprocess.CalledProcessError as e:
            raise Exception(f"initdb failed:\n{e.stderr}")

    # Studio machines live on private networks. These are the only addresses
    # allowed in, and every one of them still has to give a password.
    STUDIO_NETWORKS = ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")

    def _configure_network_access(self):
        """
        Write the database's network and access settings.

        This used to append two rules meaning "let anyone in, from any address
        on the internet, without a password", which is what "trust" means. Every
        client also logged in as the postgres superuser, so anybody who could
        reach the port was a database administrator. Those rules are gone: the
        studio's own private networks, and a password, are now required.
        """
        conf_path = self.data_dir / "postgresql.conf"
        hba_path = self.data_dir / "pg_hba.conf"

        if conf_path.exists():
            with open(conf_path, "a", encoding="utf-8") as f:
                f.write("\n# --- UT CENTRAL SERVER CONFIG ---\n")
                f.write("listen_addresses = '*'\n")
                f.write(f"port = {self.port}\n")
                # Passwords are stored and sent as one-way hashes, never in
                # the clear.
                f.write("password_encryption = scram-sha-256\n")
                # Connections held back so an administrator can always get in
                # during an incident. These only mean anything because the
                # software no longer logs in as a superuser.
                f.write("superuser_reserved_connections = 5\n")

        if hba_path.exists():
            rules = [
                "",
                "# --- UT CENTRAL SERVER CONFIG ---",
                "# Studio networks only, and a password every time.",
                "# Do NOT add a 0.0.0.0/0 rule here, and never use 'trust':",
                "# 'trust' means the password is not checked at all.",
                "local   all   all                    scram-sha-256",
                "host    all   all   127.0.0.1/32     scram-sha-256",
                "host    all   all   ::1/128          scram-sha-256",
            ]
            for network in self.STUDIO_NETWORKS:
                rules.append(f"host    all   all   {network:<16} scram-sha-256")
            with open(hba_path, "a", encoding="utf-8") as f:
                f.write("\n".join(rules) + "\n")

    def _ensure_pg_directories(self):
        """Ensures directories required by PostgreSQL exist (Git does not track empty folders)."""
        required_dirs = [
            "pg_commit_ts",
            "pg_dynshmem",
            "pg_logical/mappings",
            "pg_logical/snapshots",
            "pg_notify",
            "pg_replslot",
            "pg_serial",
            "pg_snapshots",
            "pg_stat",
            "pg_stat_tmp",
            "pg_tblspc",
            "pg_twophase",
            "pg_wal/archive_status",
        ]
        for rel_dir in required_dirs:
            p = self.data_dir / rel_dir
            p.mkdir(parents=True, exist_ok=True)

    def _ensure_slate_database(self):
        """
        Create the studio's database if this cluster does not have it yet.

        Worth being careful about, because the failure is silent in both
        directions: createdb says nothing useful when the database is already
        there, and the exception handler below discards everything. So if this
        is ever pointed at the wrong name it will not report a problem - it will
        quietly manufacture an empty database and let every screen show zero.
        """
        from slate_server.core.db_credentials import database_name

        createdb_exe = str(self.bin_dir / "createdb.exe")
        if not Path(createdb_exe).exists():
            return
        cmd_createdb = [
            createdb_exe,
            "-h", "127.0.0.1",
            "-U", "postgres",
            "-p", str(self.port),
            database_name(),
        ]
        try:
            from slate_server.core.db_credentials import env_with_password

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.run(
                cmd_createdb,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creationflags,
                # The database asks for a password now; without this the tool
                # stops to prompt for one, which nothing is there to answer.
                env=env_with_password(),
            )
        except Exception:
            pass

    def start(self, progress_callback=None):
        """Starts the PostgreSQL server using pg_ctl."""
        if not self.is_installed():
            raise Exception(f"PostgreSQL binaries not found at {self.bin_dir}")

        # If already running and responsive, return immediately
        if self.is_ready():
            if progress_callback:
                progress_callback("Database server already running.")
            self._ensure_slate_database()
            return True
            
        is_first_run = not self.is_initialized()
        if is_first_run:
            self.initialize_database(progress_callback)
        else:
            self._ensure_pg_directories()
            self._update_port_in_conf()
            self._clean_stale_pid_file()
            
        if progress_callback:
            progress_callback("Starting Database Server...")

        pg_ctl_exe = str(self.bin_dir / "pg_ctl.exe")
        log_file = str(self.data_dir.parent / "pg_server.log")
        
        cmd = [
            pg_ctl_exe,
            "-D", str(self.data_dir),
            "-l", log_file,
            "start"
        ]
        
        try:
            # Use CREATE_NO_WINDOW without DETACHED_PROCESS to avoid Windows handle hangs
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=creationflags
            )
        except subprocess.TimeoutExpired:
            logging.warning("pg_ctl start timed out after 10s; polling readiness...")
        except Exception as e:
            logging.debug(f"pg_ctl start returned notice: {e}")

        if progress_callback:
            progress_callback("Waiting for database cluster...")

        # Fast readiness polling: check every 200ms up to 6 seconds
        ready = False
        for _ in range(30):
            if self.is_ready():
                ready = True
                break
            time.sleep(0.2)

        if not ready:
            raise Exception(f"Failed to start server. Check log at {log_file}")

        # Ensure slate database exists
        self._ensure_slate_database()
        try:
            atexit.register(self.stop)
        except Exception:
            pass
        return True

    def stop(self, progress_callback=None):
        """Gracefully stops the PostgreSQL server, with fallback force-terminate if needed."""
        if not self.is_installed() or not self.is_initialized():
            return True

        if not self.is_ready():
            self._clean_stale_pid_file()
            return True
            
        if progress_callback:
            progress_callback("Stopping Database Server...")

        pg_ctl_exe = str(self.bin_dir / "pg_ctl.exe")
        log_file = str(self.data_dir.parent / "pg_server.log")
        
        cmd = [
            pg_ctl_exe,
            "-D", str(self.data_dir),
            "stop",
            "-m", "fast"
        ]
        
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=creationflags
            )
        except Exception as e:
            logging.debug(f"pg_ctl stop notice: {e}")

        # Wait for connections to close
        for _ in range(25):
            if not self.is_ready():
                self._clean_stale_pid_file()
                return True
            time.sleep(0.2)

        # Fallback force-kill if graceful stop timed out
        pid_file = self.data_dir / "postmaster.pid"
        if pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    pid_line = f.readline().strip()
                if pid_line:
                    pid = int(pid_line)
                    if sys.platform == 'win32':
                        creationflags = subprocess.CREATE_NO_WINDOW
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=creationflags
                        )
                    else:
                        os.kill(pid, 9)
            except Exception as exc:
                logging.debug("PostgreSQL fallback kill notice: %s", exc)
            self._clean_stale_pid_file()

        return not self.is_ready()

    def _update_port_in_conf(self):
        import re
        conf_path = self.data_dir / "postgresql.conf"
        if conf_path.exists():
            with open(conf_path, 'r') as f:
                content = f.read()
            content = re.sub(r"port\s*=\s*\d+", f"port = {self.port}", content)
            with open(conf_path, 'w') as f:
                f.write(content)
