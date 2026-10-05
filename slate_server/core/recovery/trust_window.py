"""
The loopback trust window: how the recovery tool gets in without any password.

    with TrustWindow(layout, reason="reset admin password") as window:
        conn = window.connect()          # postgres, from this PC, no password
        ...
    # pg_hba.conf is exactly what it was before, and has been reloaded.

While it is open, pg_hba.conf holds only this:

    # Written by Slate Central Server.
    # TEMPORARY ...
    host  all  postgres  127.0.0.1/32  trust
    host  all  postgres  ::1/128       trust

- only the superuser, only from this PC (loopback), nothing else at all. New
connections from workstations are refused for the few seconds it is open;
connections already made carry on.

If the database is not running, it is started just for the repair with
listen_addresses=127.0.0.1, so it cannot be reached from the network at all,
and stopped again afterwards.

Closing is guaranteed three ways:
    1. __exit__ / close(), in a finally;
    2. a watchdog timer that closes it after max_seconds whatever is happening;
    3. the marker file (marker.py): if this process dies, the next start of the
       server or of the recovery tool puts the original rules back.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from . import marker

logger = logging.getLogger(__name__)

HBA_SIGNATURE = "# Written by Slate Central Server."
SUPERUSER = "postgres"


class TrustWindowError(Exception):
    """The window could not be opened (and nothing has been left changed)."""


def trust_rules() -> str:
    return "\n".join([
        HBA_SIGNATURE,
        "# TEMPORARY - written by the Slate recovery tool. Only the superuser, only",
        "# from this PC, for a few seconds. The original file is in",
        "# slate_recovery\\trust_window.json and is put back automatically.",
        "host    all   %s   127.0.0.1/32     trust" % SUPERUSER,
        "host    all   %s   ::1/128          trust" % SUPERUSER,
        "",
    ])


def _flags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class TrustWindow:
    def __init__(self, layout, reason: str = "recovery", max_seconds: int = 120,
                 say=None):
        self.layout = layout
        self.reason = reason
        self.max_seconds = int(max_seconds)
        self.say = say or (lambda message: None)
        self.engine = layout.engine()
        self.started_here = False
        self.opened = False
        self.closed = False
        self.expired = False
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.RLock()
        self._connections: List = []

    # ------------------------------------------------------------ running
    def _pg_ctl(self, *args, timeout=90):
        """
        pg_ctl with no pipes. The server it starts inherits any handle it is
        given, so capturing its output makes this wait for ever (the same
        reason DatabaseEngine.start() uses DEVNULL). Output goes to the -l log.
        """
        exe = str(self.layout.bin_dir / "pg_ctl.exe")
        if not os.path.exists(exe):
            exe = str(self.layout.bin_dir / "pg_ctl")
        return subprocess.run([exe, "-D", str(self.layout.data_dir)] + list(args),
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout,
                              creationflags=_flags())

    def _ensure_running(self):
        if self.engine.is_ready():
            return
        self.engine._clean_stale_pid_file()
        from slate_server.core.db_engine import port_is_free
        if not port_is_free(self.layout.port):
            raise TrustWindowError("Port %d is held by another program, so the database "
                                   "cannot be started for the repair." % self.layout.port)
        self.say("The database is not running; starting it on this PC only, for the repair.")
        log = self.layout.recovery_dir / "recovery_postgres.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        result = self._pg_ctl("-w", "-t", "60", "-l", str(log), "-o",
                              "-p %d -c listen_addresses=127.0.0.1" % self.layout.port,
                              "start")
        for _ in range(50):
            if self.engine.is_ready():
                self.started_here = True
                return
            time.sleep(0.2)
        raise TrustWindowError("The database would not start for the repair (pg_ctl said %s). "
                               "See %s" % (result.returncode, log))

    def _reload(self):
        if self.engine.is_ready():
            self.engine._reload_configuration()

    def _can_connect(self) -> bool:
        try:
            conn = self._raw_connect("postgres")
        except Exception as exc:
            logger.debug("Trust window not usable yet: %s", exc)
            return False
        conn.close()
        return True

    def _raw_connect(self, dbname):
        import psycopg2
        # No password: the temporary rule is "trust", for postgres from here only.
        return psycopg2.connect(host="127.0.0.1", port=int(self.layout.port),
                                user=SUPERUSER, dbname=dbname, connect_timeout=5,
                                application_name="Slate recovery")

    # ------------------------------------------------------------- window
    def open(self):
        with self._lock:
            if self.opened:
                return self
            marker.close_stale_window(self.layout.data_dir, reload=self._reload, say=self.say)
            self._ensure_running()
            hba = self.layout.pg_hba
            try:
                original = hba.read_text(encoding="utf-8")
            except OSError as exc:
                self._stop_if_started()
                raise TrustWindowError("Could not read %s: %s" % (hba, exc))
            try:
                marker.open_marker(self.layout.data_dir, original, self.reason, self.max_seconds)
            except OSError as exc:
                self._stop_if_started()
                raise TrustWindowError("Could not record the current access rules, so "
                                       "they were not changed: %s" % exc)
            self.opened = True
            # From here on, everything ends in close().
            try:
                with open(hba, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(trust_rules())
                self._timer = threading.Timer(self.max_seconds, self._expire)
                self._timer.daemon = True
                self._timer.start()
                self._reload()
                deadline = time.monotonic() + 10
                while not self._can_connect():
                    if time.monotonic() > deadline:
                        raise TrustWindowError(
                            "The database did not let the recovery tool in even with the "
                            "temporary rule. Nothing has been changed.")
                    time.sleep(0.3)
                logger.warning("Trust window opened on %s (%s), at most %d s.",
                               self.layout.data_dir, self.reason, self.max_seconds)
                self.say("Opened a temporary way in, from this PC only.")
            except Exception:
                self.close()
                raise
            return self

    def connect(self, dbname: Optional[str] = None):
        with self._lock:
            if not self.opened or self.closed:
                raise TrustWindowError("The trust window is closed.")
            if dbname is None:
                from slate_server.core.db_credentials import database_name
                dbname = database_name()
            try:
                conn = self._raw_connect(dbname)
            except Exception as exc:
                if "does not exist" not in str(exc) or dbname == "postgres":
                    raise
                # The settings name a database this cluster does not have (they
                # may be the thing that is broken). Passwords live in the
                # cluster, not in a database, so those repairs still work.
                self.say("The database %s is not in this cluster; working in the "
                         "cluster's own 'postgres' database instead." % dbname)
                conn = self._raw_connect("postgres")
            conn.autocommit = True
            self._connections.append(conn)
            return conn

    def _expire(self):
        logger.error("The trust window reached its %d s limit and was closed.",
                     self.max_seconds)
        self.expired = True
        self.close()

    def _stop_if_started(self):
        if self.started_here:
            try:
                self._pg_ctl("-m", "fast", "-w", "-t", "30", "stop", timeout=60)
            except Exception as exc:
                logger.error("Could not stop the database started for the repair: %s", exc)
            self.started_here = False

    def close(self):
        with self._lock:
            if self.closed or not self.opened:
                if not self.opened:
                    self._stop_if_started()
                return
            self.closed = True
            if self._timer is not None:
                self._timer.cancel()
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
            restored = marker.restore_original(self.layout.data_dir)
            try:
                self._reload()
            except Exception as exc:
                logger.error("Reload after closing the trust window failed: %s", exc)
            if restored:
                marker.clear_marker(self.layout.data_dir)
                logger.warning("Trust window closed; access rules restored.")
                self.say("Closed the temporary way in; the access rules are as they were.")
            else:
                logger.error("The trust window could not be closed cleanly; the marker is "
                             "kept so the next start closes it.")
            self._stop_if_started()

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False
