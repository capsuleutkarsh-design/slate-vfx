"""
Joining and leaving, as one record read in two directions.

This is the spine the study kept pointing at. HR creates the person; IT
provisions them; a machine goes out with them. When they leave, the same record
is read backwards - collect that machine, revoke that access - which is the only
reason offboarding can be systematic rather than approximate.

Every access granted on the way in needs a matching revocation on the way out.
For a studio that cycles freelancers per project, that is where the money is:
machines that never came back and seats nobody released.

Two teams own different halves of the list, so a task carries the team that owns
it. HR is never shown "install Nuke" and IT is never shown "collect signed
contract".
"""

from __future__ import annotations

import logging

from datetime import date, datetime

# A database that is down must not look like a studio with no data. The
# manager raises DatabaseUnavailableError precisely so a read cannot quietly
# come back empty; catching it here and returning a fallback puts the fault
# straight back. So it is re-raised, and anything else is logged before the
# fallback is used.
try:
    from .postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    try:
        from ..infra.postgres_manager import DatabaseUnavailableError
    except ImportError:
        class DatabaseUnavailableError(ConnectionError):
            """Fallback when the manager cannot be imported."""

from ..infra.transaction import atomic
from .people import plural

logger = logging.getLogger(__name__)



HR = "HR"
IT = "IT"

JOINING = "onboard"
LEAVING = "offboard"


# The default list. A studio edits it; these are the tasks a VFX facility
# actually has to do, in the order they have to happen.
ONBOARD_TASKS = [
    (HR, "Signed contract received"),
    (HR, "ID and address proof on file"),
    (HR, "Added to payroll"),
    (HR, "Studio induction and policies"),
    (IT, "Domain account created"),
    (IT, "Email and calendar set up"),
    (IT, "Workstation issued"),
    (IT, "DCC software installed"),
    (IT, "Project shares mounted"),
    (IT, "Render farm access"),
    (HR, "Introduced to the team"),
]

# The mirror image. Not a different list - the same list, undone.
OFFBOARD_TASKS = [
    (HR, "Last working day confirmed"),
    (IT, "Render farm access revoked"),
    (IT, "Project shares unmounted"),
    (IT, "Workstation returned"),
    (IT, "Email forwarded and mailbox archived"),
    (IT, "Domain account disabled"),
    (HR, "Final settlement processed"),
    (HR, "Exit interview"),
]

# Freelancers skip the parts that only apply to staff.
FREELANCE_SKIP = {"Added to payroll", "Final settlement processed"}

# One spelling for employment, shared by Users & Roles and the joining
# dialog. The joining dialog wrote 'staff' and Users & Roles 'Staff', so the
# table showed both and the edit dialog grew a second 'staff' option. Old
# values are normalised once (people_schema.normalise_employment).
EMPLOYMENT_TYPES = ("Staff", "Freelance", "Contract")


def employment_value(text) -> str:
    """The stored spelling for whatever was typed: 'staff' -> 'Staff'."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered == "freelancer":
        lowered = "freelance"
    for value in EMPLOYMENT_TYPES:
        if value.lower() == lowered:
            return value
    return raw


def return_line(machine_name: str) -> str:
    """The leaving checklist line for one machine."""
    return "Return %s" % machine_name


class UnknownPerson(ValueError):
    """A checklist for a username that has no account."""


class InactivePerson(UnknownPerson):
    """
    A joining checklist for an account that is switched off.

    Somebody coming back is reactivated on Users & Roles first (Reactivate
    clears a last day that has passed); otherwise the account stays inactive
    however many joining lines are ticked, and leave keeps not accruing.
    """


def _is_active(record: dict) -> bool:
    """The same rule as UserManager: not deactivated and last day not passed."""
    from .user_manager import UserManager
    return UserManager._flag_active(record or {})


def _as_day(value):
    """A stored date (date, datetime or ISO text) as a date, or None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date) or not value:
        return value or None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _issuable_sql() -> str:
    from .hardware import issuable_sql
    return issuable_sql("status")


def _not_on_loan_sql() -> str:
    from .hardware import not_on_loan_sql
    return not_on_loan_sql("hardware_inventory.machine_name")


class OnboardingService:
    def __init__(self, db=None):
        if db is None:
            from ..infra.database_manager import database_manager
            db = database_manager
        self.db = db

    # ------------------------------------------------------------------ people
    def people(self, active_only: bool = False) -> list:
        """
        Everyone, with whatever onboarding state they have.

        Each row carries "active" (deactivated people and people whose last day
        has passed are not). Pickers that hand somebody a machine or a task ask
        for active_only; a list of finished checklists wants everybody.
        """
        try:
            # SELECT *: the deactivation columns are added by UserManager and
            # may not be there yet on a database it has not opened.
            rows = self.db.execute_query(
                "SELECT * FROM ut_users ORDER BY username", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("people failed")
            return []
        out = []
        for row in rows:
            row = dict(row)
            person = {key: row.get(key) for key in (
                "username", "display_name", "job_title", "joined_on", "employment",
                "reports_to", "last_day", "location")}
            person["active"] = _is_active(row)
            if active_only and not person["active"]:
                continue
            out.append(person)
        return out

    def person(self, username: str) -> dict:
        """One person's ut_users row ({} when there is none), for the Start dialog to prefill from."""
        try:
            row = self.db.execute_query(
                "SELECT * FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (str(username or "").strip(),), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("person failed")
            return {}
        return dict(row) if row else {}

    def joining_finished(self) -> set:
        """Usernames (lower-case) whose joining checklist is complete."""
        try:
            rows = self.db.execute_query(
                "SELECT user_id, MIN(CASE WHEN is_completed THEN 1 ELSE 0 END) AS all_done "
                "FROM onboarding_workflows WHERE direction = %s GROUP BY user_id",
                (JOINING,), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("joining_finished failed")
            return set()
        return {str(dict(r)["user_id"]).lower() for r in rows if dict(r).get("all_done")}

    # ------------------------------------------------------------------- tasks
    @staticmethod
    def _cycle(task) -> int:
        """Which of a person's lists in one direction a line belongs to (1 before re-hires existed)."""
        try:
            return int(task.get("cycle") or 1)
        except (TypeError, ValueError):
            return 1

    def tasks_for(self, username: str, direction: str = None) -> list:
        """
        A person's checklist lines. With a direction, only their newest list
        in it: somebody who left and came back has a finished list and a new one.
        """
        try:
            if direction:
                rows = self.db.execute_query(
                    "SELECT * "
                    "FROM onboarding_workflows "
                    "WHERE LOWER(user_id) = LOWER(%s) AND direction = %s ORDER BY id",
                    (username, direction), fetch="all") or []
                rows = [dict(r) for r in rows]
                newest = max((self._cycle(r) for r in rows), default=1)
                return [r for r in rows if self._cycle(r) == newest]
            else:
                rows = self.db.execute_query(
                    "SELECT * "
                    "FROM onboarding_workflows "
                    "WHERE LOWER(user_id) = LOWER(%s) ORDER BY id",
                    (username,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("tasks_for failed")
            return []

    def open_tasks(self, team: str = None, direction: str = None) -> list:
        """Everything outstanding, optionally just one team's half."""
        try:
            rows = self.db.execute_query(
                "SELECT * FROM onboarding_workflows ORDER BY user_id, id",
                fetch="all") or []
            # Outstanding is decided here rather than in SQL: is_completed is a
            # boolean on Postgres and an integer on SQLite, and there is no one
            # literal both accept.
            out = [d for d in (dict(r) for r in rows) if not d.get("is_completed")]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("open_tasks failed")
            return []

        if team:
            out = [t for t in out if (t.get("owner_team") or "").upper() == team.upper()]
        if direction:
            out = [t for t in out if (t.get("direction") or JOINING) == direction]
        return out

    def start(self, username: str, direction: str = JOINING,
              employment: str = None, department: str = "",
              effective_date=None, overwrite_joined: bool = False) -> int:
        """
        Lay down the checklist for somebody joining or leaving.

        Returns how many tasks were created. A list still in progress is
        topped up, never duplicated. When the last list in that direction is
        finished - a freelancer back for the next show - a new list is
        started (the next cycle); it used to add nothing.

        employment: joining records it (None keeps what is on record).
        Leaving never changes it - it only decides the lines (freelancers
        skip payroll), and the person's record is used when it is None.

        The answers the dialog collects are also written to the person's record.
        They used to be used for one thing - skipping the payroll lines for a
        freelancer - and then discarded, which is why leave accrual had no
        joining date to count from and offboarding had no last day to measure
        against.
        """
        # Only for somebody who has an account. A typed name that matched
        # nobody used to start the checklist for whoever was still selected.
        try:
            found = self.db.execute_query(
                "SELECT * FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (str(username or "").strip(),), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("start: account check failed")
            # Not "there is no account": the question could not be asked.
            raise UnknownPerson(
                "Could not check the account %r, so no checklist was started. "
                "Try again; if it keeps happening, tell IT. (%s)" % (str(username or ""), exc))
        if not found:
            raise UnknownPerson(
                "There is no account called %r. Create it on Users & Roles first, "
                "then start the checklist." % str(username or ""))
        found = dict(found)
        username = str(found["username"])
        # Joining an account that is switched off would tick through a whole
        # checklist for somebody Slate still treats as gone. Leaving is allowed:
        # a person who has already left may still have kit to collect.
        if direction == JOINING and not _is_active(found):
            raise InactivePerson(
                "%s's account is deactivated or their last day has passed. If they "
                "are coming back, reactivate the account on Users & Roles (Reactivate "
                "also clears a last day that has passed), then start joining." % username)

        if direction == LEAVING and effective_date:
            joined = _as_day(found.get("joined_on"))
            if joined and effective_date < joined:
                raise ValueError("The last working day is before %s joined (%s)." % (
                    username, joined.isoformat()))

        current = self.tasks_for(username, direction)
        cycle = self._cycle(current[0]) if current else 1
        if current and all(t.get("is_completed") for t in current):
            cycle, current = cycle + 1, []          # finished: a new list
        already = {t["task_name"] for t in current}
        employment = employment_value(employment) if employment is not None else None
        self._record_employment(username, direction,
                                employment if direction == JOINING else None,
                                effective_date, overwrite_joined)
        if not employment:
            employment = employment_value(found.get("employment"))
        template = list(ONBOARD_TASKS if direction == JOINING else OFFBOARD_TASKS)
        if direction == LEAVING:
            # One line per machine still out, so "Workstation returned" is
            # not ticked while two of three machines are still at home.
            template += [(IT, return_line(h["machine_name"])) for h in self.held_by(username)]
        freelance = str(employment or "").strip().lower() in ("freelance", "freelancer", "contract")

        made = 0
        for team, name in template:
            if name in already:
                continue
            if freelance and name in FREELANCE_SKIP:
                continue
            try:
                # Counted only when the database took it: a refused insert
                # used to be counted as a task laid out.
                if self.db.execute_update(
                        "INSERT INTO onboarding_workflows "
                        "(user_id, task_name, department, is_completed, direction, owner_team, cycle) "
                        "VALUES (%s, %s, %s, FALSE, %s, %s, %s)",
                        (username, name, department, direction, team, cycle)):
                    made += 1
            except DatabaseUnavailableError:
                raise
            except Exception:
                logger.exception("start failed")
                continue
        return made

    def _record_employment(self, username, direction, employment, effective_date,
                           overwrite_joined: bool = False):
        """
        Put the dialog's answers on the person's record.

        Joining sets the joining date only if there is not one already: the
        checklist can be re-laid months later, and moving somebody's joining
        date would silently change the leave they have accrued. The dialog
        shows the existing date and asks; overwrite_joined is the answer.
        employment None leaves it alone (leaving always passes None).
        """
        try:
            if employment:
                self.db.execute_update(
                    "UPDATE ut_users SET employment = %s "
                    "WHERE LOWER(username) = LOWER(%s)",
                    (str(employment), username))

            if direction == JOINING:
                self.db.execute_update(
                    "UPDATE ut_users SET joined_on = %s "
                    "WHERE LOWER(username) = LOWER(%s)"
                    + ("" if overwrite_joined else " AND joined_on IS NULL"),
                    (effective_date or date.today(), username))
            else:
                self.db.execute_update(
                    "UPDATE ut_users SET last_day = %s "
                    "WHERE LOWER(username) = LOWER(%s)",
                    (effective_date or date.today(), username))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("_record_employment failed")

    def joined_on(self, username: str):
        """The joining date on record, if any (shown in the Start joining dialog)."""
        try:
            row = self.db.execute_query(
                "SELECT joined_on FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (username,), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("joined_on failed")
            return None
        value = (dict(row) or {}).get("joined_on") if row else None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date) or not value:
            return value or None
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except (TypeError, ValueError):
            return None

    def cancel_checklist(self, username: str, direction: str, by_whom: str,
                         clear_last_day: bool = False) -> int:
        """
        Take back a checklist started by mistake. Returns how many lines went.

        Open lines are removed; lines already ticked stay as the record of what
        was done. Written to the audit log. A mistaken leaving list can also
        clear the last day it recorded.
        """
        removed = 0
        for task in self.tasks_for(username, direction):
            if task.get("is_completed"):
                continue
            result = self.db.execute_update(
                "DELETE FROM onboarding_workflows WHERE id = %s", (task["id"],))
            if getattr(result, "changed", result):
                removed += 1
        if direction == LEAVING and clear_last_day:
            self.db.execute_update(
                "UPDATE ut_users SET last_day = NULL WHERE LOWER(username) = LOWER(%s)",
                (username,))
        try:
            from ..infra.audit_logger import AuditLogger
            AuditLogger().log_event(
                "ONBOARDING", str(by_whom or "System"),
                "Cancelled the %s checklist for %s (%s removed%s)" % (
                    "joining" if direction == JOINING else "leaving", username,
                    plural(removed, "open line"), ", last day cleared" if clear_last_day else ""))
        except Exception as exc:
            logger.warning("Checklist cancel not audited: %s", exc)
        return removed

    def issue_refusal(self, username: str) -> str:
        """
        Why a machine should not go to this person, or "".

        Diya's last day had passed and Issue machine gave her three more.
        """
        last = self.last_day(username)
        # Shown to people as is, so worded with the person's name and a
        # studio date rather than a login and an ISO date.
        try:
            from . import people
            who = people.display_name(username) or username
        except Exception:
            who = username
        if last is not None and last < date.today():
            try:
                from . import dates
                when = dates.format_date(last) or last.isoformat()
            except Exception:
                when = last.isoformat()
            return "%s's last working day (%s) has passed." % (who, when)
        if any(not t.get("is_completed") for t in self.tasks_for(username, LEAVING)):
            return "%s is on the leaving list." % who
        return ""

    def last_day(self, username: str):
        """The last working day recorded for somebody, if any."""
        try:
            row = self.db.execute_query(
                "SELECT last_day FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (username,), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("last_day failed")
            return None
        if not row:
            return None
        value = row["last_day"] if isinstance(row, dict) else row[0]
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except (TypeError, ValueError):
            return None

    def complete(self, task_id, done: bool = True, by: str = None) -> bool:
        """
        Tick (or reopen) a line, recording who ticked it and when - who
        processed the final settlement is the first question afterwards.
        """
        try:
            # True only when a checklist line was actually changed - a refused
            # write, or an id that no longer exists, is not "done".
            result = self.db.execute_update(
                "UPDATE onboarding_workflows SET is_completed = %s, completed_by = %s, "
                "completed_at = %s WHERE id = %s",
                (bool(done), (by or None) if done else None,
                 datetime.now().replace(microsecond=0) if done else None, task_id))
            return bool(getattr(result, "changed", result))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("complete failed")
            return False

    def progress(self, username: str, direction: str = JOINING) -> dict:
        tasks = self.tasks_for(username, direction)
        done = sum(1 for t in tasks if t.get("is_completed"))
        return {"total": len(tasks), "done": done,
                "outstanding": len(tasks) - done,
                "complete": bool(tasks) and done == len(tasks)}

    # ------------------------------------------------------------------ assets
    def issue_machine(self, machine_name: str, username: str, by_whom: str,
                      note: str = "", override: bool = False, issued_on: date = None) -> bool:
        """
        Put a machine in somebody's hands, and record that it happened.

        The assignment row is what offboarding reads to know what to collect.
        The inventory's own assigned_to is kept in step so the fleet view agrees
        with the ledger.

        All of it happens in one transaction. It used to be three separate
        statements whose results were never looked at: when the ledger insert
        was refused (a machine name longer than the column) the inventory
        still said "issued", the tab said "Issued", and offboarding - which
        reads the ledger - would never ask for the machine back.
        """
        if not override and self.issue_refusal(username):
            logger.warning("Issue machine refused: %s", self.issue_refusal(username))
            return False
        try:
            with atomic(self.db) as tx:
                tx.write(
                    "INSERT INTO asset_assignments "
                    "(machine_name, user_id, issued_on, issued_by, note) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (machine_name, username, issued_on or date.today(), by_whom, note))
                tx.write(
                    "UPDATE hardware_inventory SET assigned_to = %s, status = 'Active' "
                    "WHERE machine_name = %s", (username, machine_name),
                    expect_rows=True)
                self._mark_asset_task(username, JOINING, "Workstation issued", machine_name,
                                      tx, by_whom)
            # Somebody on the leaving list gets a line to return it (not a
            # leaving list finished before they were re-hired).
            leaving = self.tasks_for(username, LEAVING)
            if any(not t.get("is_completed") for t in leaving):
                self.db.execute_update(
                    "INSERT INTO onboarding_workflows "
                    "(user_id, task_name, department, is_completed, direction, owner_team, cycle) "
                    "VALUES (%s, %s, %s, FALSE, %s, %s, %s)",
                    (username, return_line(machine_name), "", LEAVING, IT,
                     self._cycle(leaving[0])))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("issue_machine failed")
            return False

    def return_machine(self, machine_name: str, username: str, by_whom: str = None) -> bool:
        """
        Take it back, and free it in the inventory - together, or not at all.

        Returns False when there was no open loan for this person and machine:
        "returned" must not be reported for a loan that was never recorded.
        """
        try:
            with atomic(self.db) as tx:
                tx.write(
                    "UPDATE asset_assignments SET returned_on = %s "
                    "WHERE machine_name = %s AND LOWER(user_id) = LOWER(%s) "
                    "AND returned_on IS NULL",
                    (date.today(), machine_name, username), expect_rows=True)
                tx.write(
                    "UPDATE hardware_inventory SET assigned_to = NULL, status = 'Available' "
                    "WHERE machine_name = %s AND status <> 'Repair'", (machine_name,))
                # A machine that came back broken stays flagged for repair - it is
                # free of its owner but not free to hand to somebody else.
                tx.write(
                    "UPDATE hardware_inventory SET assigned_to = NULL "
                    "WHERE machine_name = %s", (machine_name,))
                self._mark_asset_task(username, LEAVING, return_line(machine_name),
                                      machine_name, tx, by_whom)
            # "Workstation returned" only once nothing is out any more.
            if not self.held_by(username):
                self._mark_asset_task(username, LEAVING, "Workstation returned",
                                      machine_name, None, by_whom)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("return_machine failed")
            return False

    def _mark_asset_task(self, username, direction, task_name, machine_name, tx=None, by=None):
        """
        Tick the matching checklist line, and record which machine it was.

        Inside issue/return this runs in their transaction (tx). A person with
        no checklist simply matches no row, which is fine.
        """
        # The newest list only: a re-hire's old, finished line keeps its record.
        cycle = max((self._cycle(t) for t in self.tasks_for(username, direction)), default=1)
        sql = ("UPDATE onboarding_workflows SET is_completed = TRUE, asset_name = %s, "
               "completed_by = %s, completed_at = %s "
               "WHERE LOWER(user_id) = LOWER(%s) AND direction = %s AND task_name = %s "
               "AND COALESCE(cycle, 1) = %s")
        params = (machine_name, by, datetime.now().replace(microsecond=0),
                  username, direction, task_name, cycle)
        if tx is not None:
            tx.write(sql, params)
            return
        try:
            self.db.execute_update(sql, params)
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("_mark_asset_task failed")
    def held_by_machine(self, machine_name: str) -> list:
        """
        Who currently has this machine, if anybody.

        The inventory's own assigned_to column is a copy; this is the ledger,
        and the ledger is what offboarding reads. Asking the copy is how a
        machine came to be deleted while a loan for it was still open.
        """
        try:
            rows = self.db.execute_query(
                "SELECT user_id, issued_on, note FROM asset_assignments "
                "WHERE LOWER(machine_name) = LOWER(%s) AND returned_on IS NULL "
                "ORDER BY issued_on", (machine_name,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("held_by_machine failed")
            return []

    def held_by(self, username: str) -> list:
        """What this person currently has out. Offboarding's shopping list."""
        try:
            rows = self.db.execute_query(
                "SELECT machine_name, issued_on, note FROM asset_assignments "
                "WHERE LOWER(user_id) = LOWER(%s) AND returned_on IS NULL "
                "ORDER BY issued_on", (username,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("held_by failed")
            return []

    def unreturned(self) -> list:
        """
        Kit still out on loan to somebody who has actually left.

        This is the report that pays for the module - a machine nobody asked
        for back is invisible until something asks this question. But it only
        means anything once the person has gone. It used to fire on anybody
        with an open leaving checklist, so it shouted on the day notice was
        given and kept shouting for the whole notice period, while the person
        was still sitting at the machine using it. An alert that is wrong for a
        month is an alert people learn to close.

        Somebody counts as gone when their account is switched off or their
        last working day has passed - with or without a leaving list: a last
        day set on Users & Roles, or a person who left before anybody started
        the list, used to take the machine with them unnoticed. With no last
        day, a leaving list still open with "Last working day confirmed"
        ticked counts too.
        """
        try:
            rows = self.db.execute_query(
                "SELECT a.machine_name, a.user_id, a.issued_on, u.* "
                "FROM asset_assignments a "
                "LEFT JOIN ut_users u ON LOWER(u.username) = LOWER(a.user_id) "
                "WHERE a.returned_on IS NULL "
                "ORDER BY a.issued_on", fetch="all") or []
            leaving = {str(t.get("user_id") or "").lower() for t in self.open_tasks(direction=LEAVING)}
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("unreturned failed")
            return []

        today = date.today()
        out = []
        for row in rows:
            row = dict(row)
            last = _as_day(row.get("last_day"))
            if row.get("username") and not _is_active(row):
                gone = True                      # deactivated, or last day passed
            elif last is not None:
                gone = last <= today
            elif str(row.get("user_id") or "").lower() in leaving:
                tasks = self.tasks_for(row.get("user_id"), LEAVING)
                gone = any(t.get("is_completed") for t in tasks
                           if str(t.get("task_name") or "").strip().lower()
                           == "last working day confirmed")
            else:
                gone = False

            if gone:
                out.append({k: row.get(k) for k in ("machine_name", "user_id", "issued_on", "last_day")})
        return out

    def available_machines_detail(self) -> list:
        """Free machines with what tells them apart: type, GPU, CPU, RAM, location, status."""
        try:
            rows = self.db.execute_query(
                "SELECT * FROM hardware_inventory "
                # Free means no open loan: the ledger, the IT area's one rule
                # for who holds a machine (not the old assigned_to copy).
                "WHERE " + _not_on_loan_sql() + " "
                # The hardware domain's one rule: not in repair, not end of life.
                "AND " + _issuable_sql() + " "
                "ORDER BY machine_name", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("available_machines_detail failed")
            return []
        keys = ("machine_name", "type", "gpu", "cpu", "ram", "location", "status")
        return [{k: (dict(r).get(k) or "") for k in keys} for r in rows]

    def available_machines(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT machine_name FROM hardware_inventory "
                "WHERE " + _not_on_loan_sql() + " "
                # In for repair, or at the end of its life: the hardware
                # domain's one rule (IT area).
                "AND " + _issuable_sql() + " "
                "ORDER BY machine_name", fetch="all") or []
            return [(r["machine_name"] if isinstance(r, dict) else r[0]) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("available_machines failed")
            return []
