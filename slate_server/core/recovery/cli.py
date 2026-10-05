"""
Recover Slate - command line.

    Recover Slate.bat                      the window (no arguments)
    Recover Slate.bat health               what is wrong, in plain words (no key needed)
    Recover Slate.bat reset-password admin
    Recover Slate.bat restore-admin [admin]
    Recover Slate.bat set-app-password
    Recover Slate.bat set-superuser-password
    Recover Slate.bat switches-off [name ...]          (no names: every switch)
    Recover Slate.bat restore-snapshot [--accounts] [--list]
    Recover Slate.bat stop-leftover-pool
    Recover Slate.bat close-window
    Recover Slate.bat init-key                         (only if there is none yet)
    Recover Slate.bat new-key [--as-admin]

Options for all of them: --data-dir <the database folder>, --port <n>.

The Recovery Key and new passwords are always asked for at the prompt (they are
never taken from the command line, where other programs could read them) and
are never printed back or logged.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from typing import List, Optional

from . import health, key as recovery_key, snapshots
from .layout import NotServerPC, find_layout, server_pc_problem
from .session import RecoverySession


def _ask_key(prompt="Recovery Key: ") -> str:
    return getpass.getpass(prompt)


def _ask_new_password(what: str) -> str:
    first = getpass.getpass("New %s: " % what)
    second = getpass.getpass("Type it again: ")
    if first != second:
        raise SystemExit("The two did not match. Nothing was changed.")
    return first


def show_key(key: str, out=print) -> None:
    out("")
    out("=" * 64)
    out("  SLATE RECOVERY KEY")
    out("")
    out("      %s" % key)
    out("")
    out("  Print this or write it down, and keep it somewhere safe that is")
    out("  not this PC (a locked drawer, the studio safe, a password manager).")
    out("  With it, and this server PC, anyone can get back into Slate when")
    out("  nobody can sign in. It is shown only now; Slate keeps only a")
    out("  one-way fingerprint of it. Lost it? Make a new one by running")
    out("  Recover Slate as a Windows administrator on this PC.")
    out("=" * 64)
    out("")


def _session(args, say) -> RecoverySession:
    layout = find_layout(data_dir=args.data_dir, port=args.port)
    session = RecoverySession(layout, say=say)
    problem = server_pc_problem(layout)
    if problem:
        raise NotServerPC(problem)
    return session


def _unlock(session: RecoverySession) -> None:
    for _ in range(3):
        try:
            session.unlock(_ask_key())
            return
        except recovery_key.WrongKey as exc:
            print(exc)
        except recovery_key.TooManyAttempts as exc:
            raise SystemExit(str(exc))
    raise SystemExit("Not unlocked.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="Recover Slate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=None, help="the database folder")
    parser.add_argument("--port", type=int, default=None)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("health")
    sub.add_parser("init-key")
    new_key = sub.add_parser("new-key")
    new_key.add_argument("--as-admin", action="store_true",
                         help="the old key is lost: prove Windows admin rights instead")
    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    admin = sub.add_parser("restore-admin")
    admin.add_argument("username", nargs="?", default="admin")
    sub.add_parser("set-app-password")
    sub.add_parser("set-superuser-password")
    off = sub.add_parser("switches-off")
    off.add_argument("names", nargs="*")
    restore = sub.add_parser("restore-snapshot")
    restore.add_argument("--accounts", action="store_true",
                         help="also merge the users and roles back")
    restore.add_argument("--list", action="store_true")
    sub.add_parser("stop-leftover-pool")
    sub.add_parser("close-window")
    args = parser.parse_args(argv)

    if not args.command:
        from slate_server.gui.recovery_window import run_window
        return run_window(data_dir=args.data_dir, port=args.port)

    try:
        if args.command == "health":
            layout = find_layout(data_dir=args.data_dir, port=args.port)
            print(health.report(health.health_check(layout)))
            return 0

        session = _session(args, print)
        layout = session.layout

        if args.command == "init-key":
            made = recovery_key.create_first_key(layout)
            if made is None:
                print("This server already has a Recovery Key. Use new-key to replace it.")
                return 1
            show_key(made)
            return 0
        if args.command == "new-key":
            old = None if args.as_admin else _ask_key("Current Recovery Key: ")
            show_key(session.new_key(old_key=old, as_admin=args.as_admin))
            return 0
        if args.command == "close-window":
            print("Closed an interrupted recovery's way in." if session.close_leftover_window()
                  else "No way in was left open.")
            return 0
        if args.command == "stop-leftover-pool":
            print(health.stop_leftover_pool(layout))
            return 0
        if args.command == "restore-snapshot" and args.list:
            for folder in snapshots.list_snapshots(layout):
                manifest = snapshots.read_manifest(folder)
                print("%s   %s   before: %s" % (folder.name.split("_", 1)[0],
                                               manifest.get("created_at", "?"),
                                               manifest.get("name", "?")))
            return 0

        _unlock(session)
        if args.command == "reset-password":
            session.reset_password(args.username, _ask_new_password("password for %s"
                                                                    % args.username))
        elif args.command == "restore-admin":
            session.restore_admin(args.username, _ask_new_password("password for %s"
                                                                   % args.username))
        elif args.command == "set-app-password":
            session.set_app_password(_ask_new_password("database app password"))
        elif args.command == "set-superuser-password":
            session.set_superuser_password(_ask_new_password("superuser password"))
        elif args.command == "switches-off":
            session.switches_off(args.names or None)
        elif args.command == "restore-snapshot":
            session.restore_latest_snapshot(accounts=args.accounts)
        return 0
    except NotServerPC as exc:
        print("Recover Slate only runs on the server PC.\n%s" % exc)
        return 1
    except (recovery_key.KeyMissing, PermissionError, FileNotFoundError) as exc:
        print(exc)
        return 1
    except Exception as exc:
        from .actions import RecoveryRefused
        if isinstance(exc, RecoveryRefused):
            print("Not done: %s" % exc)
            return 1
        print("Something went wrong and nothing further was changed: %s" % exc)
        return 1


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
