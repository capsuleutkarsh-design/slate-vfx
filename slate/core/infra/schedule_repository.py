"""
Everything Scheduling reads and writes.

The tab used to hold its SQL: an INSERT per click, an UPDATE per milestone
during a shift (each committed on its own, so a failure half way left the plan
half moved), and a bare `except: sched = []` that turned a broken query into
an empty schedule. Here every write is checked against the rules in
core/domain/scheduling.py first, multi-row changes happen in one transaction,
and every change is written to the change history with who made it.

    repo = ScheduleRepository()
    repo.list(include_archived=False)          -> [Milestone]
    repo.add(milestone, by="priya")             -> new id      (ScheduleError when refused)
    repo.edit(milestone, by="priya")
    repo.delete([ids], by="priya")              dependents are detached in the same transaction
    repo.set_status([ids], "Completed", by=...) -> previous {id: (status, completed_on)} for Undo
    repo.apply_dates([(id, start, end)], by=...)  a shift (or its undo), all or nothing
    repo.calendar(first, last)                  weekly offs + holidays
    repo.people_data(first, last)               dashboard work and approved leave
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from slate.core.domain import scheduling as DS
from slate.core.domain.dates import format_date, parse_date

from .db_results import DatabaseUnavailableError, DatabaseWriteError

logger = logging.getLogger(__name__)


class ScheduleRepository:
    def __init__(self, db=None, roles=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db
        # When given, writes are refused for roles without schedule_write -
        # the buttons are hidden for them, and this is the check that holds
        # whatever calls it.
        self.roles = roles

    # ----------------------------------------------------------- permissions
    def _may_write(self):
        if self.roles is None:
            return
        from slate.core.domain import access
        if not access.can(self.roles, "schedule_write"):
            raise PermissionError("You can look at the schedule but not change it.")

    # ----------------------------------------------------------------- reads
    def list(self, include_archived: bool = False) -> List[DS.Milestone]:
        """
        Every milestone, grouped by project then in date order. Milestones of
        projects that are no longer active are left out unless asked for.
        Raises on a failed read - a broken query is not an empty schedule.
        """
        rows = self.db.execute_query(
            "SELECT s.*, p.name AS project_name, p.active AS project_active "
            "FROM prod_scheduling s LEFT JOIN tracking_projects p ON p.code = s.project_code",
            fetch="all")
        if rows is None:
            raise RuntimeError(self._last_error() or "the schedule could not be read")
        milestones = [DS.Milestone.from_row(r) for r in rows]
        if not include_archived:
            milestones = [m for m in milestones if not m.archived]
        milestones.sort(key=lambda m: (m.project_code.casefold(), m.start is None,
                                       m.start or date.max, m.id or 0))
        return milestones

    def get(self, milestone_id) -> Optional[DS.Milestone]:
        row = self.db.execute_query(
            "SELECT s.*, p.name AS project_name, p.active AS project_active "
            "FROM prod_scheduling s LEFT JOIN tracking_projects p ON p.code = s.project_code "
            "WHERE s.id = %s", (int(milestone_id),), fetch="one")
        return DS.Milestone.from_row(row) if row else None

    def projects(self, active_only: bool = True) -> List[Tuple[str, str]]:
        """[(code, name)] of the studio's projects, by code."""
        sql = "SELECT code, name FROM tracking_projects"
        if active_only:
            sql += " WHERE active = 1"
        rows = self.db.execute_query(sql + " ORDER BY code", fetch="all")
        if rows is None:
            raise RuntimeError(self._last_error() or "the projects could not be read")
        out = []
        for r in rows:
            r = dict(r)
            code = str(r.get("code") or "").strip()
            if code:
                out.append((code, str(r.get("name") or "").strip()))
        return out

    def _last_error(self) -> str:
        try:
            return str(self.db.last_error() or "")
        except Exception:
            return ""

    # ---------------------------------------------------------------- writes
    def _check(self, milestone: DS.Milestone, others=None, keep_project: str = "") -> None:
        problems = DS.check_milestone(milestone, others if others is not None
                                      else self.list(include_archived=True))
        if problems:
            raise problems[0]
        self._check_project(milestone.project_code, keep_project)

    def _check_project(self, code: str, keep: str = "") -> None:
        """
        The project must exist and be active - the dialog only offers those,
        but an import or a script is checked here too. A milestone of an
        archived project may still be edited as long as it stays there (keep).
        """
        if keep and code.casefold() == keep.casefold():
            return
        row = self.db.execute_query("SELECT active FROM tracking_projects WHERE code = %s",
                                    (code,), fetch="one")
        if not row:
            raise DS.ScheduleError(f"There is no project {code}.", "project")
        active = str(dict(row).get("active")).strip().lower()
        if active in ("0", "false", "f", "no"):
            raise DS.ScheduleError(f"{code} is archived: milestones go on active projects.",
                                   "project")

    @staticmethod
    def _now():
        return datetime.now().replace(microsecond=0)

    def add(self, milestone: DS.Milestone, by: str = "") -> int:
        """Save a new milestone. Returns its id. ScheduleError when it breaks a rule."""
        self._may_write()
        milestone.name = " ".join(milestone.name.split())
        self._check(milestone)
        status = DS.normalise_status(milestone.status) or DS.SCHEDULED
        now = self._now()
        result = self.db.execute_update(
            "INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, status, "
            "depends_on_id, owner, department, effort_days, completed_on, created_by, created_at, "
            "updated_by, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (milestone.project_code, milestone.name, milestone.start.isoformat(),
             milestone.end.isoformat(), status, milestone.depends_on_id,
             milestone.owner or None, milestone.department or None,
             self._stored(milestone.effort_days),
             date.today().isoformat() if status == DS.COMPLETED else None,
             by or None, now, by or None, now))
        if not result:
            raise DatabaseWriteError(getattr(result, "error", "") or "the database refused it")
        new_id = result.last_id
        self._log(milestone.project_code, new_id, by, "CREATE", "milestone", "", milestone.name)
        return new_id

    # field on Milestone -> (column, how to store it)
    _EDITABLE = (
        ("project_code", "project_code"),
        ("name", "milestone"),
        ("start", "start_date"),
        ("end", "end_date"),
        ("status", "status"),
        ("depends_on_id", "depends_on_id"),
        ("owner", "owner"),
        ("department", "department"),
        ("effort_days", "effort_days"),
    )

    @staticmethod
    def _stored(value):
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            # sqlite3 cannot bind a Decimal; the text is exact on both backends.
            return str(value)
        if value == "":
            return None
        return value

    def edit(self, milestone: DS.Milestone, by: str = "") -> List[str]:
        """
        Save changes to an existing milestone. Returns the fields that changed
        ([] when nothing did). ScheduleError when it breaks a rule.
        """
        self._may_write()
        if milestone.id is None:
            raise DS.ScheduleError("That milestone has not been saved yet.")
        current = self.get(milestone.id)
        if current is None:
            raise DS.ScheduleError("That milestone no longer exists - somebody deleted it.")
        milestone.name = " ".join(milestone.name.split())
        milestone.status = DS.normalise_status(milestone.status) or current.status
        self._check(milestone, keep_project=current.project_code)
        sets, params, changed = [], [], []
        for attr, column in self._EDITABLE:
            old, new = getattr(current, attr), getattr(milestone, attr)
            if (old or None) == (new or None):
                continue
            sets.append(f"{column} = %s")
            params.append(self._stored(new))
            changed.append((attr, column, old, new))
        if not sets:
            return []
        if any(attr == "status" for attr, *_ in changed):
            sets.append("completed_on = %s")
            params.append(date.today().isoformat() if milestone.status == DS.COMPLETED else None)
        sets += ["updated_by = %s", "updated_at = %s"]
        params += [by or None, self._now(), milestone.id]
        result = self.db.execute_update(
            "UPDATE prod_scheduling SET " + ", ".join(sets) + " WHERE id = %s", tuple(params))
        if not result:
            raise DatabaseWriteError(getattr(result, "error", "") or "the database refused it")
        if not getattr(result, "changed", True):
            raise DS.ScheduleError("That milestone no longer exists - somebody deleted it.")
        for attr, column, old, new in changed:
            self._log(milestone.project_code, milestone.id, by, "UPDATE", column,
                      self._shown(old), self._shown(new))
        return [attr for attr, *_ in changed]

    @staticmethod
    def _shown(value) -> str:
        if isinstance(value, date):
            return value.isoformat()
        return "" if value is None else str(value)

    def dependents(self, ids: Iterable[int]) -> List[DS.Milestone]:
        ids = {int(i) for i in ids}
        return [m for m in self.list(include_archived=True)
                if m.depends_on_id in ids and m.id not in ids]

    def delete(self, ids: Sequence[int], by: str = "") -> int:
        """
        Delete milestones. Anything waiting on them is detached (its Depends
        On cleared) in the same transaction, so nothing is left pointing at a
        milestone that is gone. Returns how many were deleted.
        """
        self._may_write()
        from .transaction import atomic
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        doomed = {m.id: m for m in self.list(include_archived=True) if m.id in ids}
        marks = ", ".join(["%s"] * len(ids))
        with atomic(self.db) as tx:
            tx.write(f"UPDATE prod_scheduling SET depends_on_id = NULL, updated_by = %s, "
                     f"updated_at = %s WHERE depends_on_id IN ({marks}) AND id NOT IN ({marks})",
                     (by or None, self._now(), *ids, *ids))
            result = tx.write(f"DELETE FROM prod_scheduling WHERE id IN ({marks})", tuple(ids))
        for m in doomed.values():
            self._log(m.project_code, m.id, by, "DELETE", "milestone", m.name, "")
        return int(getattr(result, "rows", 0) or 0)

    def restore_deleted(self, milestones: Sequence[DS.Milestone],
                        links: Sequence[Tuple[int, int]] = (), by: str = "") -> int:
        """
        Undo a delete: the milestones come back with their ids and every field,
        and what was detached from them (links: [(dependent id, its old
        dependency)]) waits on them again - unless somebody has given it
        another dependency since. One transaction.
        """
        self._may_write()
        from .transaction import atomic
        restored = {m.id for m in milestones}
        with atomic(self.db) as tx:
            for m in milestones:
                tx.write(
                    "INSERT INTO prod_scheduling (id, project_code, milestone, start_date, end_date, "
                    "status, owner, department, effort_days, completed_on, legacy_dates, created_by, "
                    "created_at, updated_by, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (m.id, m.project_code, m.name, self._stored(m.start), self._stored(m.end),
                     m.status or None, m.owner or None, m.department or None,
                     self._stored(m.effort_days), self._stored(m.completed_on),
                     m.legacy_dates or None, m.created_by or None, m.created_at,
                     by or None, self._now()))
            # Dependencies after every row is back: they may point at each other.
            for m in milestones:
                if m.depends_on_id:
                    tx.write("UPDATE prod_scheduling SET depends_on_id = %s WHERE id = %s",
                             (m.depends_on_id, m.id))
            for child_id, parent_id in links:
                if parent_id in restored:
                    tx.write("UPDATE prod_scheduling SET depends_on_id = %s "
                             "WHERE id = %s AND depends_on_id IS NULL", (parent_id, child_id))
        for m in milestones:
            self._log(m.project_code, m.id, by, "RESTORE", "milestone", "", m.name)
        return len(milestones)

    def set_status(self, ids: Sequence[int], status: str, by: str = ""
                   ) -> Dict[int, Tuple[str, Optional[date]]]:
        """
        Give milestones a status, all or nothing. Completed records the day it
        was completed; any other status clears it. Returns what they were
        before ({id: (status, completed_on)}) so the change can be undone.
        """
        self._may_write()
        status = DS.normalise_status(status)
        if status not in DS.STATUSES:
            raise DS.ScheduleError(f"'{status}' is not a milestone status.")
        from .transaction import atomic
        current = {m.id: m for m in self.list(include_archived=True)}
        previous = {}
        today = date.today().isoformat()
        now = self._now()
        with atomic(self.db) as tx:
            for mid in ids:
                m = current.get(int(mid))
                if m is None:
                    raise DS.ScheduleError("One of those milestones no longer exists.")
                previous[m.id] = (m.status, m.completed_on)
                if m.status == status:
                    continue
                tx.write("UPDATE prod_scheduling SET status = %s, completed_on = %s, "
                         "updated_by = %s, updated_at = %s WHERE id = %s",
                         (status, today if status == DS.COMPLETED else None, by or None, now, m.id),
                         expect_rows=True)
        for mid, (old, _done) in previous.items():
            if old != status:
                self._log(current[mid].project_code, mid, by, "UPDATE", "status", old, status)
        return previous

    def restore_statuses(self, previous: Dict[int, Tuple[str, Optional[date]]], by: str = "",
                         expected: Optional[str] = None) -> List[str]:
        """
        Undo set_status: put the milestones back as they were, in one
        transaction. With expected (the status the change set), a milestone
        somebody has given another status since is left alone - Undo does not
        revert a colleague's change. Returns the names left alone.
        """
        self._may_write()
        from .transaction import atomic
        current = {m.id: m for m in self.list(include_archived=True)}
        skipped = []
        if expected is not None:
            expected = DS.normalise_status(expected)
            keep = {}
            for mid, before in previous.items():
                m = current.get(int(mid))
                if m is not None and m.status != expected:
                    skipped.append(m.name)
                else:
                    keep[mid] = before
            previous = keep
        now = self._now()
        with atomic(self.db) as tx:
            for mid, (status, completed_on) in previous.items():
                tx.write("UPDATE prod_scheduling SET status = %s, completed_on = %s, "
                         "updated_by = %s, updated_at = %s WHERE id = %s",
                         (status or None, completed_on.isoformat() if completed_on else None,
                          by or None, now, int(mid)))
        for mid, (status, _done) in previous.items():
            m = current.get(int(mid))
            if m is not None and m.status != status:
                self._log(m.project_code, mid, by, "UPDATE", "status", m.status, status)
        return skipped

    def apply_dates(self, changes: Sequence[Tuple[int, date, date]], by: str = "",
                    action: str = "SHIFT") -> int:
        """
        Write new start/end dates for several milestones in one transaction -
        a shift, a timeline drag, or the Undo of either. Either every row is
        written or none is. Returns the number written.
        """
        self._may_write()
        if not changes:
            return 0
        from .transaction import atomic
        current = {m.id: m for m in self.list(include_archived=True)}
        now = self._now()
        with atomic(self.db) as tx:
            for mid, start, end in changes:
                if start is not None and end is not None and end < start:
                    raise DS.ScheduleError("A milestone cannot end before it starts.")
                tx.write("UPDATE prod_scheduling SET start_date = %s, end_date = %s, "
                         "updated_by = %s, updated_at = %s WHERE id = %s",
                         (start.isoformat() if start else None, end.isoformat() if end else None,
                          by or None, now, int(mid)), expect_rows=True)
        for mid, start, end in changes:
            m = current.get(int(mid))
            if m is None:
                continue
            self._log(m.project_code, mid, by, action, "dates",
                      f"{self._shown(m.start)} - {self._shown(m.end)}",
                      f"{self._shown(start)} - {self._shown(end)}")
        return len(changes)

    def undo_shift(self, plan: DS.ShiftPlan, by: str = "") -> List[str]:
        """
        Put a shift back - only the milestones still where it left them, so
        an Undo a minute later does not revert what a colleague moved since.
        Returns the names left alone.
        """
        current = {m.id: m for m in self.list(include_archived=True)}
        back, skipped = [], []
        for move in plan.moved:
            m = current.get(move.id)
            if m is None or (m.start, m.end) != (move.new_start, move.new_end):
                skipped.append(move.name)
            else:
                back.append((move.id, move.old_start, move.old_end))
        self.apply_dates(back, by=by, action="UNDO SHIFT")
        return skipped

    def _log(self, project, milestone_id, by, action, field, old, new) -> None:
        if not by:
            return              # the change history refuses an unnamed author anyway
        try:
            self.db.log_change_event(project or "", "milestone", str(milestone_id), by, action,
                                     field, old, new)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:              # history is a record, not part of the change
            logger.warning("Milestone change not written to the history: %s", exc)

    # ----------------------------------------------------------- calendar
    def calendar(self, first: Optional[date] = None, last: Optional[date] = None) -> DS.WorkCalendar:
        """
        The studio's working days: weekly offs from the attendance policy and
        the studio-wide holidays. A holiday of one office only is left out:
        the other offices work that day, so a shift must not skip it.
        """
        # ponytail: projects carry no office, so office holidays are not used at
        # all; give tracking_projects a location to apply them per project.
        from slate.core.domain import leave_policy as lp
        try:
            offs = lp.policy().get("weekly_offs", [6])
        except Exception:
            offs = [6]
        holidays = {}
        sql = "SELECT holiday_date, name, location FROM holiday_calendar"
        params = None
        if first and last:
            sql += " WHERE holiday_date >= %s AND holiday_date <= %s"
            params = (first, last)
        rows = self.db.execute_query(sql, params, fetch="all") or []
        for r in rows:
            r = dict(r)
            day = parse_date(r.get("holiday_date"))
            if day is None:
                continue
            name = str(r.get("name") or "Holiday").strip()
            where = str(r.get("location") or "").strip()
            if where and where.lower() != "all":
                continue
            holidays[day] = f"{holidays[day]}, {name}" if day in holidays else name
        return DS.WorkCalendar(offs, holidays)

    # ------------------------------------------------------------- people
    def people_data(self, first: date, last: date, project_codes: Optional[Iterable[str]] = None
                    ) -> Tuple[List[dict], List[DS.Away]]:
        """
        For the People view: dashboard assignments (tracking_tasks with their
        shot and project, active projects only) and approved leave that
        touches first..last. Read-only.
        """
        tasks = self.db.execute_query(
            "SELECT t.department, t.status, t.artist_name, t.bid_days, t.target_date, "
            "s.shot_name, s.reel, s.project_code FROM tracking_tasks t "
            "JOIN tracking_shots s ON s.id = t.shot_id "
            "JOIN tracking_projects p ON p.code = s.project_code AND p.active = 1 "
            "WHERE t.artist_name IS NOT NULL AND t.artist_name <> ''", fetch="all") or []
        tasks = [dict(t) for t in tasks]
        if project_codes is not None:
            wanted = {str(c).casefold() for c in project_codes}
            tasks = [t for t in tasks if str(t.get("project_code") or "").casefold() in wanted]

        away = []
        rows = self.db.execute_query(
            "SELECT user_id, type, start_date, end_date, status FROM leave_requests "
            "WHERE start_date <= %s AND end_date >= %s", (last, first), fetch="all") or []
        from slate.core.domain import leave_policy as lp
        for r in rows:
            r = dict(r)
            if lp.normalise_status(r.get("status")) != lp.STATUS_APPROVED:
                continue           # pending leave is not on the plan until it is decided
            start, end = parse_date(r.get("start_date")), parse_date(r.get("end_date"))
            if start is None or end is None:
                continue
            away.append(DS.Away(str(r.get("user_id") or "").strip(), start, end,
                                str(r.get("type") or "Leave")))
        return tasks, away


def person_resolver(db=None):
    """
    A function turning whatever name the dashboard stored for an artist (a
    username or a display name) into the username, so one person has one lane.
    Unknown names are kept as they are.
    """
    try:
        from slate.core.domain import people
        directory = people.Directory(db).people()
    except DatabaseUnavailableError:
        raise
    except Exception:
        directory = {}
    lookup = {}
    for username, person in directory.items():
        lookup[str(username).casefold()] = username
        name = getattr(person, "name", "") or ""
        if name:
            lookup.setdefault(str(name).casefold(), username)

    def resolve(name: str) -> str:
        text = str(name or "").strip()
        return lookup.get(text.casefold(), text)

    return resolve
