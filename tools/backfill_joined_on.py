"""
Give everybody who predates the joining-date field a joining date.

Leave accrual counts completed months since ``ut_users.joined_on``. Nothing in
the application ever wrote that column, so it was null for every person in the
studio and ``accrued_by`` fell back to 1 January of the current year. That
over-credits anybody who joined part way through a year - which is exactly the
mistake the accrual rule was written to avoid.

Now that the Users & Roles tab can record it, the people already in the
database still need a value. The best evidence available is the studio's own
history, in this order:

    1. the earliest day they punched in       attendance_log
    2. the earliest leave they ever asked for leave_requests
    3. nothing - reported, and left for a human to fill in

Read-only by default. It prints what it would do and changes nothing until you
pass --apply.

    runtime\\python\\python.exe tools\\backfill_joined_on.py
    runtime\\python\\python.exe tools\\backfill_joined_on.py --apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slate.core.infra.database_manager import database_manager


def _value(row, key):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[0]


def earliest_attendance(username):
    row = database_manager.execute_query(
        "SELECT MIN(day_date) AS first_day FROM attendance_log "
        "WHERE LOWER(user_id) = LOWER(%s)", (username,), fetch="one")
    return _value(row, "first_day")


def earliest_leave(username):
    row = database_manager.execute_query(
        "SELECT MIN(start_date) AS first_day FROM leave_requests "
        "WHERE LOWER(user_id) = LOWER(%s)", (username,), fetch="one")
    return _value(row, "first_day")


def main(apply_changes: bool) -> int:
    rows = database_manager.execute_query(
        "SELECT username, joined_on FROM ut_users ORDER BY username",
        fetch="all") or []

    missing = [dict(r)["username"] for r in rows
               if not dict(r).get("joined_on")]
    already = len(rows) - len(missing)

    print("%d user(s) in the database, %d already have a joining date."
          % (len(rows), already))
    if not missing:
        print("Nothing to do.")
        return 0

    filled, unknown = 0, []
    for username in missing:
        source, day = "", None

        day = earliest_attendance(username)
        if day:
            source = "first attendance punch"
        else:
            day = earliest_leave(username)
            if day:
                source = "first leave request"

        if not day:
            unknown.append(username)
            print("  %-20s no evidence at all - set it by hand on the "
                  "Users & Roles tab" % username)
            continue

        day = str(day)[:10]
        print("  %-20s %s   (%s)" % (username, day, source))
        if apply_changes:
            ok = database_manager.execute_update(
                "UPDATE ut_users SET joined_on = %s WHERE LOWER(username) = LOWER(%s)",
                (day, username))
            if not ok:
                print("      could not write it - left unchanged")
                continue
        filled += 1

    print()
    if apply_changes:
        print("Wrote %d joining date(s). %d still unknown." % (filled, len(unknown)))
    else:
        print("Would write %d joining date(s). %d would stay unknown."
              % (filled, len(unknown)))
        print("Nothing has been changed. Re-run with --apply to write them.")

    if unknown:
        print()
        print("These people have no attendance and no leave history, so the "
              "database cannot say when they started:")
        for name in unknown:
            print("  %s" % name)
        print("Until somebody records a date, their leave accrues from "
              "1 January, which credits them too much.")
    return 0


def _run(entry, apply_changes: bool) -> int:
    """
    Run the job, and say plainly when the database is not there.

    These are run by hand on the server, often by somebody who is not going to
    read a psycopg2 traceback. An unreachable database is the commonest way this
    ends and it is not a bug in the script.
    """
    try:
        return entry(apply_changes)
    except KeyboardInterrupt:
        print("\nStopped. Nothing further was changed.")
        return 1
    except Exception as exc:
        name = type(exc).__name__
        if "Unavailable" in name or "Circuit" in name or "Operational" in name:
            print()
            print("The database did not answer, so nothing was read or changed.")
            print("Start Slate Server on this machine, or check the host and "
                  "port in slate/config.json, and run this again.")
            return 1
        raise


if __name__ == "__main__":
    sys.exit(_run(main, "--apply" in sys.argv))
