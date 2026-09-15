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


class OnboardingService:
    def __init__(self, db=None):
        if db is None:
            from ..infra.database_manager import database_manager
            db = database_manager
        self.db = db

    # ------------------------------------------------------------------ people
    def people(self) -> list:
        """Everyone, with whatever onboarding state they have."""
        try:
            rows = self.db.execute_query(
                "SELECT username, display_name, job_title, joined_on, employment, reports_to "
                "FROM ut_users ORDER BY COALESCE(joined_on, CURRENT_DATE) DESC",
                fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("people failed")
            return []

    # ------------------------------------------------------------------- tasks
    def tasks_for(self, username: str, direction: str = None) -> list:
        try:
            if direction:
                rows = self.db.execute_query(
                    "SELECT id, user_id, task_name, department, is_completed, "
                    "       direction, owner_team, asset_name "
                    "FROM onboarding_workflows "
                    "WHERE LOWER(user_id) = LOWER(%s) AND direction = %s ORDER BY id",
                    (username, direction), fetch="all") or []
            else:
                rows = self.db.execute_query(
                    "SELECT id, user_id, task_name, department, is_completed, "
                    "       direction, owner_team, asset_name "
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
                "SELECT id, user_id, task_name, department, is_completed, "
                "       direction, owner_team, asset_name "
                "FROM onboarding_workflows ORDER BY user_id, id",
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
              employment: str = "staff", department: str = "",
              effective_date=None) -> int:
        """
        Lay down the checklist for somebody joining or leaving.

        Returns how many tasks were created. Existing tasks for the same
        direction are left alone, so running this twice does not duplicate a
        half-finished list.

        The answers the dialog collects are also written to the person's record.
        They used to be used for one thing - skipping the payroll lines for a
        freelancer - and then discarded, which is why leave accrual had no
        joining date to count from and offboarding had no last day to measure
        against.
        """
        already = {t["task_name"] for t in self.tasks_for(username, direction)}
        self._record_employment(username, direction, employment, effective_date)
        template = ONBOARD_TASKS if direction == JOINING else OFFBOARD_TASKS
        freelance = str(employment or "").strip().lower() in ("freelance", "freelancer", "contract")

        made = 0
        for team, name in template:
            if name in already:
                continue
            if freelance and name in FREELANCE_SKIP:
                continue
            try:
                self.db.execute_update(
                    "INSERT INTO onboarding_workflows "
                    "(user_id, task_name, department, is_completed, direction, owner_team) "
                    "VALUES (%s, %s, %s, FALSE, %s, %s)",
                    (username, name, department, direction, team))
                made += 1
            except DatabaseUnavailableError:
                raise
            except Exception:
                logger.exception("start failed")
                continue
        return made

    def _record_employment(self, username, direction, employment, effective_date):
        """
        Put the dialog's answers on the person's record.

        Joining sets the joining date only if there is not one already: the
        checklist can be re-laid months later, and moving somebody's joining
        date would silently change the leave they have accrued.
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
                    "WHERE LOWER(username) = LOWER(%s) AND joined_on IS NULL",
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

    def complete(self, task_id, done: bool = True) -> bool:
        try:
            self.db.execute_update(
                "UPDATE onboarding_workflows SET is_completed = %s WHERE id = %s",
                (bool(done), task_id))
            return True
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
                      note: str = "") -> bool:
        """
        Put a machine in somebody's hands, and record that it happened.

        The assignment row is what offboarding reads to know what to collect.
        The inventory's own assigned_to is kept in step so the fleet view agrees
        with the ledger.
        """
        try:
            self.db.execute_update(
                "INSERT INTO asset_assignments "
                "(machine_name, user_id, issued_on, issued_by, note) "
                "VALUES (%s, %s, %s, %s, %s)",
                (machine_name, username, date.today(), by_whom, note))
            self.db.execute_update(
                "UPDATE hardware_inventory SET assigned_to = %s, status = 'Active' "
                "WHERE machine_name = %s", (username, machine_name))
            self._mark_asset_task(username, JOINING, "Workstation issued", machine_name)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("issue_machine failed")
            return False

    def return_machine(self, machine_name: str, username: str) -> bool:
        """Take it back, and free it in the inventory."""
        try:
            self.db.execute_update(
                "UPDATE asset_assignments SET returned_on = %s "
                "WHERE machine_name = %s AND LOWER(user_id) = LOWER(%s) "
                "AND returned_on IS NULL",
                (date.today(), machine_name, username))
            self.db.execute_update(
                "UPDATE hardware_inventory SET assigned_to = NULL, status = 'Available' "
                "WHERE machine_name = %s AND status <> 'Repair'", (machine_name,))
            # A machine that came back broken stays flagged for repair - it is
            # free of its owner but not free to hand to somebody else.
            self.db.execute_update(
                "UPDATE hardware_inventory SET assigned_to = NULL "
                "WHERE machine_name = %s", (machine_name,))
            self._mark_asset_task(username, LEAVING, "Workstation returned", machine_name)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("return_machine failed")
            return False

    def _mark_asset_task(self, username, direction, task_name, machine_name):
        """Tick the matching checklist line, and record which machine it was."""
        try:
            self.db.execute_update(
                "UPDATE onboarding_workflows SET is_completed = TRUE, asset_name = %s "
                "WHERE LOWER(user_id) = LOWER(%s) AND direction = %s AND task_name = %s",
                (machine_name, username, direction, task_name))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("_mark_asset_task failed")
            pass

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

        Somebody counts as gone when their last working day has passed, or when
        IT have ticked the line that says the workstation came back.
        """
        try:
            rows = self.db.execute_query(
                "SELECT a.machine_name, a.user_id, a.issued_on, u.last_day "
                "FROM asset_assignments a "
                "LEFT JOIN ut_users u ON LOWER(u.username) = LOWER(a.user_id) "
                "WHERE a.returned_on IS NULL "
                "AND EXISTS (SELECT 1 FROM onboarding_workflows w "
                "            WHERE LOWER(w.user_id) = LOWER(a.user_id) "
                "            AND w.direction = 'offboard') "
                "ORDER BY a.issued_on", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("unreturned failed")
            return []

        today = date.today()
        out = []
        for row in rows:
            row = dict(row)
            last = row.get("last_day")
            if isinstance(last, datetime):
                last = last.date()
            elif last and not isinstance(last, date):
                try:
                    last = datetime.fromisoformat(str(last)[:10]).date()
                except (TypeError, ValueError):
                    last = None

            # No last day recorded at all: fall back to the checklist, which is
            # the only other evidence that the person has actually gone.
            if last is None:
                tasks = self.tasks_for(row.get("user_id"), LEAVING)
                gone = any(t.get("is_completed") for t in tasks
                           if str(t.get("task_name") or "").strip().lower()
                           == "last working day confirmed")
            else:
                gone = last <= today

            if gone:
                out.append(row)
        return out

    def available_machines(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT machine_name FROM hardware_inventory "
                "WHERE (assigned_to IS NULL OR assigned_to = '') "
                "AND COALESCE(status, '') <> 'Repair' "
                "ORDER BY machine_name", fetch="all") or []
            return [(r["machine_name"] if isinstance(r, dict) else r[0]) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("available_machines failed")
            return []
