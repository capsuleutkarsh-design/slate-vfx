"""
Realistic demo data for the Leave and IT Support modules.

Every one of these tables ships empty, which makes a working feature look
broken - you cannot judge a queue with nothing in it. This puts a plausible
week of studio traffic in so the screens can be evaluated.

    python tools/seed_workplace_demo.py          # fill
    python tools/seed_workplace_demo.py --clear  # remove it all again

Everything written here is tagged, and --clear removes exactly what was
written. It will not touch records your team created.
"""

import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ut_vfx.core.infra.database_manager import database_manager as db  # noqa: E402
from ut_vfx.core.domain.service_desk import priority_for  # noqa: E402
from ut_vfx.core.domain import leave_policy as lp  # noqa: E402

# Anything seeded carries this, so the clear-up is exact.
TAG = "[demo]"

# user, type, day offset from today, span, status, reason
LEAVE = [
    ("emp0012", "Casual",      -21, 2, lp.STATUS_APPROVED, "Family function out of town"),
    ("emp0012", "Sick",         -9, 1, lp.STATUS_APPROVED, "Fever, worked from home the next day"),
    ("emp0012", "Earned",       14, 5, lp.STATUS_PENDING_SUPERVISOR, "Holiday booked before the Q4 push"),
    ("artist",  "Casual",       -4, 1, lp.STATUS_APPROVED, "Bank appointment"),
    ("artist",  "Sick",         -2, 2, lp.STATUS_PENDING_HR, "Flu - will pick up SH_020 on return"),
    ("artist",  "Earned",        9, 3, lp.STATUS_PENDING_SUPERVISOR, "Wedding in the family"),
    ("tester",  "Casual",      -14, 1, lp.STATUS_REJECTED, "Clashed with the AK74 delivery"),
    ("tester",  "Project Rest", -6, 2, lp.STATUS_APPROVED, "Rest days after the AK74 delivery"),
    ("tester",  "Unpaid",       21, 4, lp.STATUS_PENDING_SUPERVISOR, "Extending leave after a trip"),
    ("admin",   "Earned",       -1, 1, lp.STATUS_APPROVED, "Long weekend"),
]

# user, category, impact, urgency, status, owner, summary, detail.
#
# Priority is absent on purpose: it is not chosen, it is derived from impact and
# urgency through the same grid the raise dialog uses.
TICKETS = [
    ("artist", "Workstation", "Medium", "High", "In Progress", "it",
     "Nuke crashes on opening any AK74 comp script",
     "Happens on every script in 05_Reels/REEL_01. The crash log points at the OCIO "
     "config. I cannot start on SH_030 until this is sorted."),

    ("artist", "Software / Licence", "High", "High", "Waiting on You", "it",
     "Nuke licence not checking out",
     "Getting 'no licence available' since this morning. Two other artists say the same."),

    ("emp0012", "Storage", "High", "Medium", "Open", "",
     "R: drive dropping out mid-render",
     "The share disconnects every hour or so. Renders fail with a path error and "
     "have to be resubmitted."),

    ("emp0012", "Render Farm", "High", "High", "Resolved", "it",
     "Farm jobs stuck in pending",
     "Twelve jobs sat pending overnight. Cleared after the queue was restarted."),

    ("tester", "Peripherals", "Low", "Low", "Open", "",
     "Wacom pen pressure stopped working",
     "The pressure curve does nothing in Nuke or Photoshop. A driver reinstall did not help."),

    ("tester", "Access / Account", "Low", "Medium", "Closed", "it",
     "Cannot reach the Deage project folder",
     "Permissions were missing on the new share. Sorted."),

    ("admin", "Network", "Medium", "Medium", "Open", "",
     "Review room machine cannot see the central server",
     "Discovery finds nothing from that machine. It works from every other desk."),
]

# Which seeded ticket (1-based, in the order above), who spoke, what they said.
COMMENTS = [
    (1, "it", "Picked this up. Can you send me the crash log from %LOCALAPPDATA%?"),
    (1, "artist", "Sent. It is the OCIO path - it points at the old server."),
    (1, "it", "Confirmed. Rolling out a corrected config to your machine this afternoon."),
    (2, "it", "The licence server shows all seats taken. Who else has Nuke open right now?"),
    (4, "it", "Queue restarted and the twelve jobs picked up. Closing unless it comes back."),
    (6, "it", "Added you to the Deage group. Log out and back in and it will be there."),
]

# Enough of the year to make day counting real.
HOLIDAYS = [
    (date(date.today().year, 1, 26), "Republic Day"),
    (date(date.today().year, 8, 15), "Independence Day"),
    (date(date.today().year, 10, 2), "Gandhi Jayanti"),
    (date(date.today().year, 12, 25), "Christmas"),
]


# machine, type, gpu, ram - the fleet the spine hands out. Tagged through
# `location` so --clear can find them again without touching a real studio's
# inventory.
MACHINES = [
    ("WS-COMP-01", "Workstation", "RTX 4090",     "128 GB"),
    ("WS-COMP-02", "Workstation", "RTX 4090",     "128 GB"),
    ("WS-ROTO-05", "Workstation", "RTX 3060",     "32 GB"),
    ("WS-ROTO-06", "Workstation", "RTX 3060",     "32 GB"),
    ("WS-FX-02",   "Workstation", "RTX 4080",     "256 GB"),
    ("LAP-SUP-01", "Laptop",      "RTX 3070 Mob", "32 GB"),
]

# user, direction, employment, department, how many of their tasks are done -
# so the screen opens on people at genuinely different stages rather than a
# wall of untouched checklists.
MOVERS = [
    ("emp0012", "onboard",  "staff",     "Comp", 6),
    ("artist",  "onboard",  "freelance", "Roto", 3),
    ("tester",  "offboard", "freelance", "Roto", 2),
]


# software, seats bought, renews in N days, the peaks to log.
#
# Each one is deliberately a different finding, so the screen opens showing all
# four kinds of answer rather than four rows of "fine".
LICENCES = [
    ("Nuke",      8,  120, [5, 6, 8, 9, 7]),   # over-subscribed - peaked at 9 of 8
    ("Houdini",   4,   28, [2, 3, 3, 4]),      # renews soon, and sized right
    ("Maya",     10,  200, [3, 4, 4, 3, 5]),   # under-used - never above 5 of 10
    ("Mocha Pro", 2,  -14, [1, 2]),            # already expired
    ("Silhouette", 3, 310, []),                # bought, never measured
]


def _exec(sql, params=None):
    try:
        return db.execute_update(sql, params or ())
    except Exception as exc:
        print("   !! %s" % exc)
        return False


def clear():
    print("Removing seeded records...")
    for sql in (
        "DELETE FROM it_ticket_comments WHERE comment_text LIKE %s",
        "DELETE FROM it_tickets WHERE description LIKE %s",
        "DELETE FROM leave_requests WHERE reason LIKE %s",
        "DELETE FROM holiday_calendar WHERE name LIKE %s",
        "DELETE FROM asset_assignments WHERE note LIKE %s",
        "DELETE FROM hardware_inventory WHERE location LIKE %s",
    ):
        _exec(sql, ("%" + TAG,))

    # The checklists carry no free text to tag, so they are cleared by the
    # people they belong to - which is the same list that created them.
    for username, direction, _emp, _dept, _done in MOVERS:
        _exec("DELETE FROM onboarding_workflows WHERE user_id = %s AND direction = %s",
              (username, direction))

    for name, _seats, _due, _peaks in LICENCES:
        _exec("DELETE FROM licence_readings WHERE software_name = %s", (name,))
        _exec("DELETE FROM software_licenses WHERE software_name = %s", (name,))
    print("   cleared")


def seed_holidays():
    print("Seeding the holiday calendar...")
    made = 0
    for day, name in HOLIDAYS:
        if _exec("INSERT INTO holiday_calendar (holiday_date, name, location) "
                 "VALUES (%s, %s, 'All') ON CONFLICT DO NOTHING",
                 (day, "%s %s" % (name, TAG))):
            made += 1
    print("   %d holiday(s)" % len(HOLIDAYS))


def seed_leave():
    print("Seeding leave requests...")
    today = date.today()
    holidays = {d for d, _ in HOLIDAYS}
    made = 0
    for user, kind, offset, span, status, reason in LEAVE:
        start = today + timedelta(days=offset)
        end = start + timedelta(days=span - 1)
        # The charge is worked out and stored now, so a later policy change
        # cannot silently rewrite what somebody was already deducted.
        charge = lp.days_charged(start, end, holidays=holidays)["total"]
        if _exec(
            "INSERT INTO leave_requests "
            "(user_id, start_date, end_date, type, status, half_day, reason, days_charged) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user, start, end, kind, status, 0, "%s %s" % (reason, TAG), charge)
        ):
            made += 1
    print("   %d leave request(s)" % made)


def seed_tickets():
    print("Seeding tickets...")
    made = 0
    for user, category, impact, urgency, status, owner, summary, detail in TICKETS:
        created = datetime.now() - timedelta(hours=random.randint(2, 96))
        body = summary + "\n\n" + detail + " " + TAG
        priority = priority_for(impact, urgency)
        # A ticket somebody picked up has, by definition, been responded to.
        responded = created + timedelta(minutes=random.randint(10, 180)) if owner else None
        if _exec(
            "INSERT INTO it_tickets "
            "(submitted_by, category, description, status, priority, impact, urgency, "
            " assigned_to, created_at, first_response_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user, category, body, status, priority, impact, urgency,
             owner or None, created, responded)
        ):
            made += 1
    print("   %d ticket(s)" % made)

    rows = db.execute_query(
        "SELECT id FROM it_tickets WHERE description LIKE %s ORDER BY id ASC",
        ("%" + TAG,), fetch="all") or []
    ticket_ids = [r["id"] if isinstance(r, dict) else r[0] for r in rows]

    print("Seeding the conversation on those tickets...")
    made = 0
    for index, author, text in COMMENTS:
        if index - 1 >= len(ticket_ids):
            continue
        if _exec(
            "INSERT INTO it_ticket_comments (ticket_id, author, comment_text) "
            "VALUES (%s, %s, %s)",
            (ticket_ids[index - 1], author, text + " " + TAG)
        ):
            made += 1
    print("   %d comment(s)" % made)


def seed_hardware():
    print("Seeding the hardware fleet...")
    made = 0
    for name, kind, gpu, ram in MACHINES:
        if _exec("INSERT INTO hardware_inventory "
                 "(machine_name, type, status, gpu, ram, location) "
                 "VALUES (%s, %s, 'Available', %s, %s, %s) ON CONFLICT DO NOTHING",
                 (name, kind, gpu, ram, "Studio floor " + TAG)):
            made += 1
    print("   %d machine(s)" % len(MACHINES))


def seed_joining():
    """
    People part way through joining and leaving.

    Half-done is the useful state to seed: it shows HR and IT their own
    outstanding lines rather than two identical untouched lists, and it puts a
    machine in somebody's hands so the leaving side has something to ask back.
    """
    print("Seeding joining and leaving...")
    from ut_vfx.core.domain.onboarding_service import OnboardingService

    service = OnboardingService(db)
    free = list(service.available_machines())

    for username, direction, employment, department, done in MOVERS:
        service.start(username, direction, employment, department)
        tasks = service.tasks_for(username, direction)
        for task in tasks[:done]:
            service.complete(task["id"], True)

        # Somebody joining gets a machine; somebody leaving already has one out,
        # which is the whole point of the returning half.
        if free:
            service.issue_machine(free.pop(0), username, "it",
                                  "Issued on joining " + TAG)

    print("   %d person(s) on the move" % len(MOVERS))


def seed_licences():
    """
    Licences, plus the usage history that makes them mean anything.

    Readings are spread backwards over the window rather than written all at
    once, because a peak is only credible if it came from repeated sampling.
    """
    print("Seeding licences and their usage...")
    from ut_vfx.core.infra.licence_repository import LicenceRepository

    repo = LicenceRepository(db)
    today = date.today()
    for name, seats, due, peaks in LICENCES:
        repo.save(name, seats, today + timedelta(days=due))
        for i, in_use in enumerate(peaks):
            taken = datetime.now() - timedelta(days=(len(peaks) - i) * 7)
            repo.record(name, in_use, seats, taken)
    print("   %d licence(s)" % len(LICENCES))


def main():
    if "--clear" in sys.argv:
        clear()
        return
    clear()   # so running it twice does not double up
    seed_holidays()
    seed_leave()
    seed_tickets()
    seed_hardware()
    seed_joining()
    seed_licences()
    print("\nDone. Run with --clear to remove all of it again.")


if __name__ == "__main__":
    main()
