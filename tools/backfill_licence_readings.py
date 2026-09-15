"""
Tie existing licence readings to the licence they were taken against.

Readings used to be matched to a purchase by software name. Two contracts for
the same product - a studio licence and a project one - therefore shared a
single peak, and both were reported as over-subscribed on the strength of the
other's usage. Readings now carry the licence id.

The readings already in the database have no id. This fills it in wherever the
answer is certain: exactly one licence with that name. Where a name is held by
two licences there is no way to tell from the reading which one it belongs to,
so those are left alone and listed - the compliance screen still counts them
through the name fallback, which is the behaviour they already had.

Read-only by default.

    runtime\\python\\python.exe tools\\backfill_licence_readings.py
    runtime\\python\\python.exe tools\\backfill_licence_readings.py --apply
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slate.core.infra.database_manager import database_manager


def main(apply_changes: bool) -> int:
    licences = [dict(r) for r in (database_manager.execute_query(
        "SELECT id, software_name FROM software_licenses", fetch="all") or [])]

    by_name = defaultdict(list)
    for row in licences:
        name = str(row.get("software_name") or "").strip().lower()
        if name:
            by_name[name].append(row.get("id"))

    orphans = [dict(r) for r in (database_manager.execute_query(
        "SELECT DISTINCT software_name FROM licence_readings "
        "WHERE licence_id IS NULL", fetch="all") or [])]

    if not orphans:
        print("Every reading already names the licence it was taken against.")
        return 0

    print("%d product name(s) with readings that predate the licence id."
          % len(orphans))

    certain, ambiguous, unknown = 0, [], []
    for row in orphans:
        name = str(row.get("software_name") or "").strip()
        ids = by_name.get(name.lower(), [])

        if len(ids) == 1:
            count = database_manager.execute_query(
                "SELECT COUNT(*) AS c FROM licence_readings "
                "WHERE licence_id IS NULL AND software_name = %s",
                (name,), fetch="one")
            count = dict(count).get("c", 0) if count else 0
            print("  %-28s -> licence %-6s %d reading(s)" % (name, ids[0], count))
            if apply_changes:
                database_manager.execute_update(
                    "UPDATE licence_readings SET licence_id = %s "
                    "WHERE licence_id IS NULL AND software_name = %s",
                    (ids[0], name))
            certain += count
        elif len(ids) > 1:
            ambiguous.append((name, ids))
        else:
            unknown.append(name)

    print()
    verb = "Tied" if apply_changes else "Would tie"
    print("%s %d reading(s) to a licence." % (verb, certain))
    if not apply_changes:
        print("Nothing has been changed. Re-run with --apply to write it.")

    if ambiguous:
        print()
        print("These product names are held by more than one licence, so a "
              "reading cannot be attributed from its name alone:")
        for name, ids in ambiguous:
            print("  %-28s licences %s" % (name, ", ".join(str(i) for i in ids)))
        print("Left as they are. They still count through the name fallback, "
              "which is exactly what they did before - take a fresh reading "
              "against each licence to separate them properly.")

    if unknown:
        print()
        print("These have readings but no licence at all, so the purchase was "
              "deleted and the readings outlived it:")
        for name in unknown:
            print("  %s" % name)
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
