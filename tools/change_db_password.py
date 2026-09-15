"""
Change the password the database itself holds, to match the settings.

The settings files and the database are two separate copies of one fact, and
editing a settings file changes only one of them. A cluster that already exists
keeps whatever password it was created with, so the next start authenticates
with the new one, is refused, and reports a database that will not accept the
server's own password.

The server fixes this by itself on a cluster it created and locked - it can let
itself back in through the access rules it wrote. It deliberately will not do
that to a cluster secured by someone else's pg_hba.conf, because the repair
resets passwords and that is not a thing to do to another administrator's
decision. This script is that case, done on purpose and by hand.

    python tools/change_db_password.py --old "OldPassword"

Nothing is changed without --apply. The database has to be running.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def connect(port: int, password: str, timeout: int = 5):
    import psycopg2
    return psycopg2.connect(host="127.0.0.1", port=port, dbname="postgres",
                            user="postgres", password=password,
                            connect_timeout=timeout,
                            application_name="Slate password change")


def main() -> int:
    from slate_server.core.db_credentials import (
        admin_password, admin_user, application_user, settings_sources)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", default="",
                        help="the password the database has now")
    parser.add_argument("--port", type=int, default=5440)
    parser.add_argument("--apply", action="store_true",
                        help="actually change it; without this nothing is written")
    args = parser.parse_args()

    wanted = admin_password()
    if not wanted:
        print("No password is configured, so there is nothing to change the "
              "database to. Set one in Slate Server's Settings first.")
        return 2

    print("Settings read from:")
    for source in settings_sources():
        print("   ", source)
    print("Port:     %d" % args.port)
    print("Accounts: %s (administrator), %s (the software)"
          % (admin_user(), application_user()))
    print()

    # Already right? Then this has run, or the cluster is new.
    try:
        connect(args.port, wanted).close()
        print("The database already accepts the configured password. "
              "Nothing to do.")
        return 0
    except Exception as exc:
        first = str(exc).strip().splitlines()
        print("The configured password is refused: %s"
              % (first[0] if first else exc))

    if not args.old:
        print()
        print("Run again with --old \"<the password the database has now>\".")
        print("If nobody knows it, the database cannot be opened and the way "
              "back is a backup, or starting over with an empty cluster.")
        return 1

    try:
        conn = connect(args.port, args.old)
    except Exception as exc:
        first = str(exc).strip().splitlines()
        print("The old password is refused too: %s"
              % (first[0] if first else exc))
        print("Nothing has been changed.")
        return 1

    print("The old password works. Both accounts would be set to the "
          "configured one.")
    if not args.apply:
        print()
        print("Nothing written. Run again with --apply to change it.")
        conn.close()
        return 0

    from psycopg2 import sql
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for role in (admin_user(), application_user()):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if not cur.fetchone():
                    print("   %-14s does not exist here, skipped" % role)
                    continue
                cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(wanted)))
                print("   %-14s changed" % role)
    finally:
        conn.close()

    print()
    print("Done. Restart Slate Server so the connection pool picks up new "
          "verifiers - it copies them from the database and the old ones no "
          "longer match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
