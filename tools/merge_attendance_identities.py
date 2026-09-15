"""
Fold attendance written under a display name into the person's login.

Two screens recorded attendance for the same person under two different keys.
The Attendance tab used the login (``EMP0007``); the Home tab used the name on
screen (``Priya Sharma``). ``CentralAttendance`` lower-cases whatever it is
given, so the studio ended up with ``emp0007`` and ``priya sharma`` as separate
people - and each screen reported the other's punches as missing. Home now uses
the login too, which stops it happening again; this repairs what is already
there.

For every attendance row whose user_id matches somebody's display name rather
than their username, the row is re-keyed to the username. Where that produces
two rows for one person on one day, they are merged: the earliest punch-in and
the latest punch-out, which is the pair that describes the day the person
actually worked.

Read-only by default. It prints what it would do and changes nothing until you
pass --apply. Run it on a copy first and check the counts.

    runtime\\python\\python.exe tools\\merge_attendance_identities.py
    runtime\\python\\python.exe tools\\merge_attendance_identities.py --apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slate.core.infra.database_manager import database_manager


def _rows(sql, params=None):
    out = database_manager.execute_query(sql, params, fetch="all") or []
    return [dict(r) for r in out]


def build_identity_map():
    """display name (lower) -> username, for people whose two differ."""
    mapping, known = {}, set()
    for row in _rows("SELECT username, display_name FROM ut_users"):
        username = str(row.get("username") or "").strip()
        if not username:
            continue
        known.add(username.lower())
        display = str(row.get("display_name") or "").strip()
        if display and display.lower() != username.lower():
            mapping[display.lower()] = username
    return mapping, known


def main(apply_changes: bool) -> int:
    mapping, known = build_identity_map()
    if not mapping:
        print("Every user's display name matches their username, so there is "
              "nothing that could have been mis-keyed.")
        return 0

    stray = _rows(
        "SELECT DISTINCT user_id FROM attendance_log ORDER BY user_id")
    todo = []
    for row in stray:
        uid = str(row.get("user_id") or "").strip()
        if not uid or uid.lower() in known:
            continue
        target = mapping.get(uid.lower())
        if target:
            todo.append((uid, target))

    unmatched = [str(r.get("user_id")) for r in stray
                 if str(r.get("user_id") or "").strip()
                 and str(r.get("user_id")).lower() not in known
                 and str(r.get("user_id")).lower() not in mapping]

    print("%d attendance identity(ies) to fold in." % len(todo))
    if not todo and not unmatched:
        print("Nothing to do.")
        return 0

    moved = merged = 0
    for source, target in todo:
        days = _rows(
            "SELECT day_date, punch_in, punch_out FROM attendance_log "
            "WHERE user_id = %s ORDER BY day_date", (source,))
        print("  %-24s -> %-16s %d day(s)" % (source, target, len(days)))

        for day in days:
            day_date = day.get("day_date")
            existing = database_manager.execute_query(
                "SELECT punch_in, punch_out FROM attendance_log "
                "WHERE LOWER(user_id) = LOWER(%s) AND day_date = %s",
                (target, day_date), fetch="one")

            if not existing:
                if apply_changes:
                    database_manager.execute_update(
                        "UPDATE attendance_log SET user_id = %s "
                        "WHERE user_id = %s AND day_date = %s",
                        (target.lower(), source, day_date))
                moved += 1
                continue

            # Both keys have a row for this day. Keep the widest span: the
            # earliest arrival and the latest departure. Times are stored
            # zero-padded, so comparing them as text orders them correctly.
            existing = dict(existing)
            def pick(a, b, latest):
                a, b = (str(a) if a else ""), (str(b) if b else "")
                if not a:
                    return b or None
                if not b:
                    return a or None
                return (max(a, b) if latest else min(a, b))

            punch_in = pick(existing.get("punch_in"), day.get("punch_in"), latest=False)
            punch_out = pick(existing.get("punch_out"), day.get("punch_out"), latest=True)

            print("      %s merged (in %s, out %s)"
                  % (day_date, punch_in or "-", punch_out or "-"))
            if apply_changes:
                database_manager.execute_update(
                    "UPDATE attendance_log SET punch_in = %s, punch_out = %s "
                    "WHERE LOWER(user_id) = LOWER(%s) AND day_date = %s",
                    (punch_in, punch_out, target, day_date))
                database_manager.execute_update(
                    "DELETE FROM attendance_log WHERE user_id = %s AND day_date = %s",
                    (source, day_date))
            merged += 1

    print()
    verb = "Re-keyed" if apply_changes else "Would re-key"
    print("%s %d day(s) and merged %d duplicate day(s)." % (verb, moved, merged))
    if not apply_changes:
        print("Nothing has been changed. Re-run with --apply to write it.")

    if unmatched:
        print()
        print("These attendance keys match no username and no display name. "
              "They may be people who have since left, or a typo:")
        for uid in unmatched:
            print("  %s" % uid)
        print("They have been left exactly as they are - guessing who they "
              "were would be worse than leaving them.")
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
