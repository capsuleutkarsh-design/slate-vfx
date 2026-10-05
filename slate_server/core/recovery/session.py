"""
One recovery session: unlock with the Recovery Key, then do one thing at a time.

    session = RecoverySession(find_layout(), say=print)
    session.unlock(key)                    # server PC + Recovery Key, or raises
    session.reset_password("admin", new)   # snapshot -> trust window -> change -> closed

Both the command line (cli.py) and the window (the Recovery page) drive this,
so they behave identically. Every action that changes something:

    1. checks this is the server PC and the session is unlocked;
    2. closes any trust window an earlier, interrupted recovery left open;
    3. takes a snapshot (snapshots.before_security_change) so it can be undone;
    4. opens the loopback trust window, does the one change, and closes it;
    5. writes one line to slate_recovery\\recovery.log - what, when, by whom,
       and whether it worked. Never a password, never the key.

An unlocked session stays unlocked for ten minutes, then asks for the key again.
"""

from __future__ import annotations

import getpass
import logging
import time
from datetime import datetime
from typing import Callable, Iterable, List, Optional

from . import actions, key as recovery_key, marker, snapshots
from .layout import ServerLayout, assert_server_pc
from .trust_window import TrustWindow

logger = logging.getLogger(__name__)

UNLOCK_SECONDS = 600


class Locked(Exception):
    """The session needs the Recovery Key first."""


class RecoverySession:
    def __init__(self, layout: ServerLayout, say: Optional[Callable[[str], None]] = None,
                 window_seconds: int = 120):
        self.layout = layout
        self.say = say or (lambda message: None)
        self.window_seconds = int(window_seconds)
        self._unlocked_until = 0.0

    # ------------------------------------------------------------ unlocking
    @property
    def unlocked(self) -> bool:
        return time.monotonic() < self._unlocked_until

    def unlock(self, key: str) -> None:
        """Server PC + Recovery Key. Raises NotServerPC, KeyMissing, TooManyAttempts or WrongKey."""
        assert_server_pc(self.layout)
        try:
            recovery_key.verify(self.layout, key)
        except Exception as exc:
            self._log("unlock", False, exc.__class__.__name__)
            raise
        self._unlocked_until = time.monotonic() + UNLOCK_SECONDS
        self._log("unlock", True)
        self.close_leftover_window()

    def lock(self) -> None:
        self._unlocked_until = 0.0

    def _need_unlocked(self):
        assert_server_pc(self.layout)
        if not self.unlocked:
            raise Locked("Enter the Recovery Key first.")

    # ---------------------------------------------------------------- record
    def _log(self, action: str, ok: bool, detail: str = "") -> None:
        try:
            from . import fs
            fs.ensure_private_dir(self.layout.recovery_dir)
            try:
                who = getpass.getuser()
            except Exception:
                who = "?"
            line = "%s  %-24s %-4s by %s%s\n" % (
                datetime.now().isoformat(timespec="seconds"), action,
                "OK" if ok else "FAIL", who, ("  (%s)" % detail) if detail else "")
            with open(self.layout.log_file, "a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception as exc:
            logger.debug("Recovery log not written: %s", exc)

    # ---------------------------------------------------------------- window
    def close_leftover_window(self) -> bool:
        engine = self.layout.engine()
        closed = marker.close_stale_window(
            self.layout.data_dir,
            reload=engine._reload_configuration if engine.is_ready() else None,
            say=self.say)
        if closed:
            self._log("closed leftover window", True)
        return closed

    def _in_window(self, name: str, work: Callable, snapshot: bool = True) -> List[str]:
        self._need_unlocked()
        lines: List[str] = []
        try:
            with TrustWindow(self.layout, reason=name, max_seconds=self.window_seconds,
                             say=self.say) as window:
                conn = window.connect()
                if snapshot:
                    # Worth having, never worth being stuck for: a snapshot
                    # that cannot be written does not stop the repair.
                    try:
                        from slate.core.security.dbapi import ConnectionDB
                        folder = snapshots.before_security_change(
                            "recovery-" + name, self.layout,
                            db=ConnectionDB(window.connect()), dump_login=("postgres", None))
                        lines.append("Kept a snapshot first: %s" % folder.name)
                    except Exception as exc:
                        logger.warning("Snapshot before %s failed: %s", name, exc)
                        lines.append("A snapshot could not be kept first (%s); carrying on."
                                     % (str(exc).splitlines()[0] if str(exc) else exc))
                lines += list(work(conn) or [])
        except Exception as exc:
            self._log(name, False, str(exc).splitlines()[0][:200] if str(exc) else
                      exc.__class__.__name__)
            raise
        self._log(name, True)
        for line in lines:
            self.say(line)
        return lines

    # ---------------------------------------------------------------- actions
    def reset_password(self, username: str, new_password: str) -> List[str]:
        return self._in_window("reset-password",
                               lambda conn: actions.reset_account(conn, username, new_password))

    def restore_admin(self, username: str, new_password: str) -> List[str]:
        from slate_server.core.db_credentials import application_user
        return self._in_window("restore-admin", lambda conn: actions.restore_admin(
            conn, username, new_password, app_role=application_user()))

    def set_app_password(self, new_password: str) -> List[str]:
        return self._in_window("set-app-password", lambda conn: actions.set_app_password(
            conn, new_password, self.layout))

    def set_superuser_password(self, new_password: str) -> List[str]:
        return self._in_window("set-superuser-password",
                               lambda conn: actions.set_superuser_password(
                                   conn, new_password, self.layout))

    def switches_off(self, names: Optional[Iterable[str]] = None) -> List[str]:
        """Always writes the local file; reaches the database too when it can."""
        self._need_unlocked()
        names = list(names) if names is not None else None
        try:
            return self._in_window("switches-off",
                                   lambda conn: actions.switches_off(self.layout, names, conn),
                                   snapshot=False)
        except Exception as exc:
            lines = actions.switches_off(self.layout, names, None)
            lines.append("(The database could not be reached: %s)" % str(exc).splitlines()[0])
            self._log("switches-off (file only)", True)
            for line in lines:
                self.say(line)
            return lines

    def restore_latest_snapshot(self, accounts: bool = False) -> List[str]:
        """
        Files first, with no database needed (a broken postgresql.conf may be
        the very thing stopping it); accounts afterwards, through the window.
        """
        self._need_unlocked()
        folder = snapshots.latest_snapshot(self.layout)
        if folder is None:
            raise FileNotFoundError("There is no snapshot to restore yet.")
        lines = ["Restoring snapshot %s." % folder.name]
        lines += snapshots.restore_snapshot(self.layout, folder, accounts=False)
        self._log("restore-snapshot " + folder.name.split("_", 1)[0], True)
        if accounts:
            def merge(conn):
                from slate.core.security.dbapi import ConnectionDB
                return snapshots.restore_accounts(folder, ConnectionDB(conn))
            lines += self._in_window("restore-accounts", merge, snapshot=False)
        for line in lines:
            self.say(line)
        return lines

    def new_key(self, old_key: Optional[str] = None, as_admin: bool = False) -> str:
        assert_server_pc(self.layout)
        made = recovery_key.replace_key(self.layout, old_key=old_key, as_admin=as_admin)
        self._log("new-recovery-key", True, "old key" if old_key else "Windows administrator")
        return made
