import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from ..models.shot_model import Shot, DepartmentInfo
from slate.core.domain import shot_status
from slate.core.domain.access import ( artist_statuses, can_set_status,
    OfflineError, can_edit_dashboard, can_edit_own_status, is_offline_fallback,
)
from slate.core.domain.departments import department_keys
from slate.core.infra.database_manager import DatabaseManager


def _shot_key(reel, shot_name) -> Tuple[str, str]:
    """A shot's identity within a project: its reel and its name."""
    return (str(reel or "").strip().lower(), str(shot_name or "").strip().lower())


class StaleDataError(Exception):
    """
    Raised when a save would overwrite somebody else's newer save.

    ``conflicts`` says which shots, so the dashboard can show them - reel
    included - and re-apply only the person's own edits:
    [{"shot_id", "shot_name", "reel", "local_version", "db_version", "kind"}],
    kind being "changed" (saved by someone else), "deleted" (the shot is gone)
    or "added" (someone else added a shot of that name first).
    """

    def __init__(self, message: str = "", conflicts=None):
        super().__init__(message or "Someone else saved these shots after you opened them.")
        self.conflicts = list(conflicts or [])


class ProjectClosedError(Exception):
    """The project was deleted or archived by someone else, so nothing was saved."""

    def __init__(self, message: str, archived: bool = False):
        super().__init__(message)
        self.archived = archived


def _conflict_message(conflicts) -> str:
    names = [f"{c['shot_name']} ({c['reel']})" if c.get("reel") else c["shot_name"]
             for c in conflicts]
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    kinds = {c.get("kind", "changed") for c in conflicts}
    one = len(names) == 1
    if kinds == {"deleted"}:
        return f"{shown} {'was' if one else 'were'} deleted by someone else."
    if kinds == {"added"}:
        return f"Someone else added {shown} a moment ago."
    return f"Someone else saved {shown} after you opened {'it' if one else 'them'}."


# Not part of a shot's history: where its files and thumbnail were found.
_NOT_LOGGED = {"thumbnail_path", "folder_paths"}


def _history_text(value) -> str:
    """A stored value as one line of history text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return ", ".join(_history_text(v.get("text") if isinstance(v, dict) else v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return str(value)


class SQLiteHandler:
    """
    The dashboard's shots in the database (tracking_shots/tracking_tasks).

    One write path, ``_write``, used by every save - the toolbar Save, the
    artist's own status, Add shots: the shot rows are written under a version
    lock and only the department rows that changed are written, in one
    transaction; then every changed field goes into the history.
    """

    def __init__(
        self,
        project_code: str,
        db_manager: DatabaseManager = None,
        user_id: int = None,
        user_role: str = "artist",
        username: str = "",
        actor_identities=None,
    ):
        self.project_code = project_code
        # Why the last write was refused, for the person who asked for it.
        self.last_error = ""
        # Shots the last read found but could not show, as "name: reason".
        self.read_problems = []
        # The numeric id is kept for callers that still pass it, but history
        # is written under the username - the identity the rest of the app
        # uses. It used to be "get_user_id(display name) or 1", so any
        # mismatch recorded the admin as the author of the change.
        self.user_id = int(user_id) if str(user_id or "").strip().isdigit() else None
        self.username = str(username or "").strip()
        # Every name the person making the change answers to: nobody is
        # notified about their own change.
        self.actor_identities = {
            str(i).strip().lower() for i in (actor_identities or []) if str(i).strip()
        }
        if self.username:
            self.actor_identities.add(self.username.lower())
        if isinstance(user_role, list):
            self.user_roles = [str(r).lower() for r in user_role if str(r).strip()]
            self.user_role = self.user_roles[0] if self.user_roles else "artist"
        else:
            role = str(user_role or "artist").lower()
            self.user_role = role
            self.user_roles = [role]

        from slate.core.infra.database_manager import database_manager

        self.db_manager = db_manager or database_manager

        self.notifier = None
        try:
            from slate.core.domain.notification_manager import NotificationManager

            self.notifier = NotificationManager()
        except Exception as e:
            logging.exception(f"Failed to init NotificationManager: {e}")

    def _check_permission(self):
        if is_offline_fallback():
            raise OfflineError(
                "The central database is unreachable. Changes made now would "
                "not reach anyone else, so saving is disabled until it returns."
            )
        if not can_edit_dashboard(self.user_roles):
            raise PermissionError(f"User role(s) {self.user_roles} not authorized to make changes.")

    def _check_project_open(self):
        """
        The project must still exist and be active. A project somebody deleted
        came back on the next save of anyone who still had it open; one they
        archived kept taking saves nobody else could see.
        """
        rows = self.db_manager.execute_query(
            "SELECT active FROM tracking_projects WHERE code=%s", (self.project_code,), fetch="all")
        if rows is None:
            raise RuntimeError(f"Could not check that project {self.project_code} is still open.")
        if not rows:
            raise ProjectClosedError(
                f"{self.project_code} was deleted by someone else, so nothing was saved.")
        if str(dict(rows[0]).get("active")).strip().lower() not in ("1", "t", "true", "y", "yes"):
            raise ProjectClosedError(
                f"{self.project_code} was archived by someone else, so nothing was saved. "
                "It has to be restored before it takes changes.", archived=True)

    @staticmethod
    def _serialize_shot(shot: Shot) -> str:
        # Underscored fields are UI state, not shot data. Storing _modified
        # meant a shot saved from the grid was "unsaved" in every session
        # after, for everyone.
        data = {k: v for k, v in asdict(shot).items() if not k.startswith("_")}
        return json.dumps(data, default=str)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def _task_columns(self) -> Dict[str, Any]:
        """Which tracking_tasks columns this database has (they grew over time)."""
        cols = getattr(self, "_task_cols", None)
        if cols is None:
            from slate.core.infra.migrations.workplace_schema import _column_exists

            def has(column):
                return _column_exists(self.db_manager, "tracking_tasks", column)

            cols = {
                "artist": "artist" if has("artist") else "artist_name",
                "project_code": has("project_code"),
                "actual_days": has("actual_days"),
            }
            self._task_cols = cols
        return cols

    def _read(self, where: str = "", params=()) -> List[Shot]:
        """
        Shots of this project as the dashboard shows them: the stored data with
        each department row applied. A failed read raises - an empty list must
        only ever mean "no shots", or an outage looks like an empty project.
        """
        rows = self.db_manager.execute_query(
            "SELECT id, reel, shot_name, data_json, version FROM tracking_shots "
            "WHERE project_code=%s" + where, (self.project_code, *params), fetch="all")
        if rows is None:
            raise RuntimeError(f"The shots of {self.project_code} could not be read.")
        rows = [dict(r) for r in rows]
        tasks_by_shot: Dict[int, Dict[str, Dict[str, Any]]] = {}
        ids = [int(r["id"]) for r in rows]
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join(["%s"] * len(chunk))
            tasks = self.db_manager.execute_query(
                f"SELECT * FROM tracking_tasks WHERE shot_id IN ({marks})", tuple(chunk), fetch="all")
            if tasks is None:
                raise RuntimeError(f"The department rows of {self.project_code} could not be read.")
            for task in tasks:
                task = dict(task)
                if task.get("shot_id") is None or not task.get("department"):
                    continue
                tasks_by_shot.setdefault(int(task["shot_id"]), {})[task["department"]] = task

        from slate.core.infra.tracking_repository import shot_row_to_dict
        shots = []
        for row in rows:
            try:
                item = shot_row_to_dict(row)
                if item is None:
                    continue
                shot = Shot.from_dict(item)
                shot.id = int(row["id"])
                v_raw = row.get("version")
                shot.version = int(v_raw) if v_raw is not None else int(shot.version or 1)
                self._apply_task_overrides(shot, tasks_by_shot.get(shot.id, {}))
                shots.append(shot)
            except Exception as e:
                name = row.get('shot_name') or row.get('id')
                logging.exception(f"Failed to deserialize shot {name}: {e}")
                self.read_problems.append(f"{name}: {e}")
        return shots

    def read_shots(self) -> List[Shot]:
        """Every shot of the project. Raises when the database could not be read."""
        self.read_problems = []
        return self._read()

    def read_shots_by_id(self, shot_ids) -> List[Shot]:
        """
        Just these shots, loaded exactly as read_shots() loads them.

        For keeping an open dashboard current: when somebody changes three
        shots, the other screens read those three, not the whole project. Ids
        that belong to another project, or no longer exist, are simply not
        returned. A failed read raises.
        """
        ids = sorted({int(i) for i in shot_ids if str(i).strip().lstrip("-").isdigit()})
        shots: List[Shot] = []
        self.read_problems = []
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join(["%s"] * len(chunk))
            shots.extend(self._read(f" AND id IN ({marks})", chunk))
        return shots

    @staticmethod
    def _apply_task_overrides(shot: Shot, task_map: Dict[str, Dict[str, Any]]):
        # Departments present in the database but not in departments.json are
        # still applied, so removing one from config never destroys its data.
        # The shot's own artist is never derived from Comp: they are
        # independent (FIX_PLAN), and filling a cleared artist back in from
        # Comp undid the clear on the next load.
        for dept_key in set(department_keys()) | set(task_map.keys()):
            task = task_map.get(dept_key)
            if not task:
                continue
            dept = shot.dept(dept_key)
            dept.status = task.get("status") or dept.status or ""
            dept.artist = task.get("artist") or task.get("artist_name") or dept.artist or ""
            dept.bid_days = float(task.get("bid_days") or 0.0)
            dept.target = task.get("target_date") or task.get("target") or dept.target or ""
            if "actual_days" in task:
                # The column is the record: NULL is "not recorded", not 0.
                value = task.get("actual_days")
                try:
                    dept.actual_days = None if value is None else float(value)
                except (TypeError, ValueError):
                    pass

    # ------------------------------------------------------------------
    # Notifications and history
    # ------------------------------------------------------------------
    def _is_actor(self, name) -> bool:
        return str(name or "").strip().lower() in self.actor_identities

    def _department_name(self, dept_key: str) -> str:
        from slate.core.domain.departments import load_departments
        for dept in load_departments():
            if dept.key == dept_key:
                return dept.name
        return str(dept_key or "").title()

    def _department_family(self) -> str:
        """
        The department a scoped role (a lead) is confined to, from the
        person's own ut_users record. It used to be whatever the screen
        passed in. Empty means the job title names no department, so a scoped
        role can edit nothing until an admin sets one.
        """
        from slate.core.domain.departments import family_of
        if not self.username:
            return ""
        row = self.db_manager.execute_query(
            "SELECT job_title FROM ut_users WHERE LOWER(username) = LOWER(%s)",
            (self.username,), fetch="one", strict=True)
        return family_of(dict(row).get("job_title")) or "" if row else ""

    def _family_name(self) -> str:
        from slate.core.domain.departments import family_name
        return family_name(self._department_family())

    def _notify_assignment(self, shot_name: str, old_artist: str, new_artist: str, dept_key: str = ""):
        if not self.notifier:
            return
        if not new_artist or new_artist == old_artist or self._is_actor(new_artist):
            return
        try:
            where = f" ({self._department_name(dept_key)})" if dept_key else ""
            msg = f"You have been assigned to {shot_name}{where}."
            self.notifier.add_notification(new_artist, msg, "assignment")
        except Exception as e:
            logging.debug(f"Notification failed for assignment: {e}")

    def _notify_status(self, shot_name: str, artist: str, old_status: str, new_status: str,
                       dept_key: str = ""):
        if not self.notifier:
            return
        # Nobody needs telling about a status that went blank, an unchanged
        # one, or their own change.
        if not artist or not str(new_status or "").strip() or old_status == new_status:
            return
        if self._is_actor(artist):
            return
        try:
            where = f" ({self._department_name(dept_key)})" if dept_key else ""
            msg = f"{shot_name}{where} is now {new_status}."
            self.notifier.add_notification(artist, msg, "update")
        except Exception as e:
            logging.debug(f"Notification failed for status update: {e}")

    def _log_change(self, entity_type: str, entity_id: str, action: str, field: str, old_val, new_val,
                    shot: Optional[Shot] = None, department: str = ""):
        """
        One line of history, under the signed-in person's username and with
        the shot's id, reel and department in columns of their own (so a
        shot's history is found by id, not by LIKE on a name).
        """
        if not self.username:
            logging.error("History not written for %s %s: this dashboard does not know "
                          "who is signed in.", entity_id, field)
            return
        details = {}
        if shot is not None:
            shot_id = getattr(shot, "id", None)
            details = {
                "shot_id": int(shot_id) if shot_id and int(shot_id) > 0 else None,
                "shot_name": shot.shot_name,
                "reel": str(getattr(shot, "reel_episode", "") or ""),
                "department": department or "",
            }
        try:
            self.db_manager.log_change_event(
                self.project_code, entity_type, entity_id, self.username, action, field,
                old_val, new_val, **details,
            )
        except Exception as e:
            logging.warning(f"History log write failed: {e}")

    def _log_changes(self, before: Optional[Shot], shot: Shot):
        """
        History for every field that changed between `before` (as stored) and
        `shot` (as saved), and a notification to whoever was assigned or had
        their status changed. A new shot is one 'added' line.
        """
        name = shot.shot_name
        if before is None:
            self._log_change("shot", name, "CREATE", "shot", "", "added", shot)
            before = Shot(shot_name=name, reel_episode=shot.reel_episode, status="")
            before.departments = {k: DepartmentInfo() for k in shot.departments}
            log = False
        else:
            log = True
        old, new = before.to_dict(), shot.to_dict()
        for key in sorted(set(old) | set(new)):
            if key in _NOT_LOGGED or key == "version":
                continue
            if key == "departments":
                old_depts, new_depts = old.get(key) or {}, new.get(key) or {}
                for dept in sorted(set(old_depts) | set(new_depts)):
                    a, b = old_depts.get(dept) or {}, new_depts.get(dept) or {}
                    for leaf in sorted(set(a) | set(b)):
                        if (a.get(leaf) or None) == (b.get(leaf) or None):
                            continue
                        if log:
                            self._log_change("task", f"{name}_{dept}",
                                             "ASSIGN" if leaf == "artist" else "UPDATE",
                                             f"{dept}_{leaf}", _history_text(a.get(leaf)),
                                             _history_text(b.get(leaf)), shot, dept)
                        if leaf == "status":
                            self._notify_status(name, b.get("artist") or "", a.get(leaf) or "",
                                                b.get(leaf) or "", dept)
                        elif leaf == "artist":
                            self._notify_assignment(name, a.get(leaf) or "", b.get(leaf) or "", dept)
                continue
            if old.get(key) == new.get(key):
                continue
            if log:
                self._log_change("shot", name, "ASSIGN" if key == "assigned_artist" else "UPDATE",
                                 key, _history_text(old.get(key)), _history_text(new.get(key)), shot)
            if key == "assigned_artist":
                self._notify_assignment(name, old.get(key) or "", new.get(key) or "")

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def _stored(self) -> Dict[Tuple[str, str], Shot]:
        """Every stored shot of the project, keyed by (reel, name), as the grid loads them."""
        return {_shot_key(s.reel_episode, s.shot_name): s for s in self._read()}

    @staticmethod
    def find_conflicts(shots, stored) -> List[Dict[str, Any]]:
        """
        Shots that cannot be saved over what is stored now: saved by someone
        else since they were loaded here, deleted, or added by someone else
        first (a shot never loaded from the database has no id).
        """
        conflicts = []
        for shot in shots:
            row = stored.get(_shot_key(getattr(shot, "reel_episode", ""), shot.shot_name))
            try:
                mine = int(getattr(shot, "id", -1))
            except (TypeError, ValueError):
                mine = -1
            local_v = int(getattr(shot, "version", 0) or 0)
            entry = {"shot_name": shot.shot_name, "reel": str(getattr(shot, "reel_episode", "") or ""),
                     "local_version": local_v}
            if row is None:
                if mine >= 0:
                    conflicts.append(dict(entry, shot_id=mine, db_version=0, kind="deleted"))
                continue
            db_v = int(row.version or 0)
            if mine < 0:
                conflicts.append(dict(entry, shot_id=int(row.id), db_version=db_v, kind="added"))
            elif local_v != db_v:
                conflicts.append(dict(entry, shot_id=int(row.id), db_version=db_v, kind="changed"))
        return conflicts

    def write_shots(self, shots: List[Shot], force: bool = False) -> bool:
        """
        Save these shots (only these - the dashboard passes the ones with
        pending edits). Returns whether everything was written; when it was
        not, ``last_error`` says why.

        A shot somebody else saved since it was loaded here, deleted or added
        first is refused with StaleDataError (naming each shot), unless
        ``force`` (which still never brings a deleted shot back). A deleted or
        archived project raises ProjectClosedError.
        """
        self.last_error = ""
        if not self.project_code:
            return False
        if not shots:
            return True

        self._check_permission()
        self._check_project_open()

        # One copy per shot: a name repeated in one save would be written twice.
        unique: Dict[Tuple[str, str], Shot] = {}
        for shot in shots:
            if shot.shot_name:
                unique[_shot_key(getattr(shot, "reel_episode", ""), shot.shot_name)] = shot
        if not unique:
            return False

        stored = self._stored()
        conflicts = self.find_conflicts(unique.values(), stored)
        deleted = [c for c in conflicts if c["kind"] == "deleted"]
        if conflicts and (not force or deleted):
            raise StaleDataError(_conflict_message(deleted or conflicts), deleted or conflicts)

        self._assert_within_department(unique.values(), stored)
        pairs = [(shot, stored.get(key)) for key, shot in unique.items()]
        return self._write(pairs, force=force)

    def _write(self, pairs, force: bool = False) -> bool:
        """
        Write (shot, as stored now or None) pairs in one transaction: each shot
        row under its version lock, then the department rows that changed.
        History and notifications follow once it is committed.
        """
        from slate.core.infra.db_results import DatabaseWriteError
        from slate.core.infra.transaction import atomic
        from slate.core.infra.tracking_repository import status_and_priority

        cols = self._task_columns()
        loaded = [(shot, shot.version) for shot, _before in pairs]
        done = []
        task_rows = []
        try:
            with atomic(self.db_manager) as tx:
                for shot, before in pairs:
                    lock = int(before.version or 0) if before is not None and force \
                        else int(getattr(shot, "version", 0) or 0)
                    shot.version = lock
                    data = self._serialize_shot(shot)
                    status, priority = status_and_priority(data)
                    reel = str(shot.reel_episode or "").strip()
                    if before is None:
                        try:
                            result = tx.write(
                                "INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, "
                                "data_json, last_updated, version) VALUES (%s, %s, %s, %s, %s, %s, %s, 1) "
                                "RETURNING id",
                                (self.project_code, reel, shot.shot_name, status, priority, data,
                                 _now()))
                        except DatabaseWriteError as exc:
                            if exc.kind == "duplicate":
                                c = {"shot_id": -1, "shot_name": shot.shot_name, "reel": reel,
                                     "local_version": 0, "db_version": 1, "kind": "added"}
                                raise StaleDataError(_conflict_message([c]), [c]) from exc
                            raise
                        shot_id, version = int(result.last_id), 1
                    else:
                        result = tx.write(
                            "UPDATE tracking_shots SET data_json=%s, status=%s, priority=%s, "
                            "version=version+1, last_updated=%s WHERE id=%s AND version=%s",
                            (data, status, priority, _now(), int(before.id), lock))
                        if result.rows <= 0:
                            c = {"shot_id": int(before.id), "shot_name": shot.shot_name, "reel": reel,
                                 "local_version": lock, "db_version": int(before.version or 0),
                                 "kind": "changed"}
                            raise StaleDataError(_conflict_message([c]), [c])
                        shot_id, version = int(before.id), lock + 1
                    done.append((shot, before, shot_id, version))
                    task_rows.extend(self._task_rows(shot_id, shot, before, cols))
                self._write_tasks(tx, task_rows, cols)
        except StaleDataError:
            for shot, version in loaded:
                shot.version = version
            raise
        except (DatabaseWriteError, RuntimeError) as exc:
            for shot, version in loaded:
                shot.version = version
            self.last_error = str(exc) or "The database refused the save."
            logging.error("Dashboard save refused: %s", exc)
            return False

        for shot, before, shot_id, version in done:
            shot.id = shot_id
            shot.version = version
            self._log_changes(before, shot)
        return True

    def _task_rows(self, shot_id, shot, before, cols):
        """
        The department rows to write for one shot: every department of a new
        shot, otherwise only those whose values changed. Re-saving all of them
        wrote 0 actual days everywhere and put back bid days and targets the
        department rows held (Bidding writes there).
        """
        rows = []
        keys = list(department_keys()) + [k for k in shot.departments if k not in department_keys()]
        for key in keys:
            dept = shot.dept(key)
            if before is not None:
                old = before.dept(key)
                same = (old.status or "", old.artist or "", float(old.bid_days or 0), old.target or "",
                        old.actual_days) == (dept.status or "", dept.artist or "",
                                             float(dept.bid_days or 0), dept.target or "", dept.actual_days)
                if same:
                    continue
            row = {"shot_id": shot_id, "department": key, "status": dept.status or "",
                   "artist": dept.artist or "", "bid_days": float(dept.bid_days or 0.0),
                   # The target only: an ETA is not a target, and storing it as
                   # one turned every ETA into a target date on the next load.
                   "target_date": dept.target or ""}
            if cols["actual_days"]:
                row["actual_days"] = dept.actual_days
            rows.append(row)
        return rows

    def _write_tasks(self, tx, rows, cols):
        """Upsert department rows, many per statement."""
        if not rows:
            return
        artist_col = cols["artist"]
        names = ["shot_id"] + (["project_code"] if cols["project_code"] else []) + \
            ["department", "status", artist_col, "artist_id", "bid_days", "target_date"] + \
            (["actual_days"] if cols["actual_days"] else [])
        target = "(project_code, shot_id, department)" if cols["project_code"] else "(shot_id, department)"
        updates = ", ".join(f"{n} = EXCLUDED.{n}" for n in names if n not in ("shot_id", "department"))
        user_ids = {}
        values = []
        for row in rows:
            artist = row["artist"]
            if artist and artist not in user_ids:
                user_ids[artist] = self.db_manager.get_user_id(artist)
            values.append([row["shot_id"]] + ([self.project_code] if cols["project_code"] else []) +
                          [row["department"], row["status"], artist, user_ids.get(artist) if artist else None,
                           row["bid_days"], row["target_date"]] +
                          ([row.get("actual_days")] if cols["actual_days"] else []))
        group = "(" + ", ".join(["%s"] * len(names)) + ")"
        for start in range(0, len(values), 50):
            chunk = values[start:start + 50]
            tx.write(f"INSERT INTO tracking_tasks ({', '.join(names)}) VALUES "
                     + ", ".join([group] * len(chunk))
                     + f" ON CONFLICT {target} DO UPDATE SET {updates}",
                     [v for row in chunk for v in row])

    # ------------------------------------------------------------------
    # Department scoping
    # ------------------------------------------------------------------

    def _scoped_department_keys(self):
        """The department keys this person may edit, or None if unrestricted (departments.scope_keys)."""
        from slate.core.domain.departments import scope_keys
        return scope_keys(self.user_roles, self._department_family())

    @staticmethod
    def _shot_level_fields(shot: Shot) -> dict:
        """Everything about a shot that is not a department, for comparison."""
        data = shot.to_dict()
        for key in ("departments", "version", "id"):
            data.pop(key, None)
        return data

    def _assert_within_department(self, shots, stored) -> None:
        """
        A department-scoped role may change only its own department's columns.

        Each shot is compared with what this person loaded (its baseline), not
        with what is stored now: somebody else changing another field in the
        meantime is a conflict (checked first), not this lead's breach. A shot
        with no baseline (written outside the grid) is compared with the
        stored row. Creating shots is a coordinator's job.
        """
        allowed = self._scoped_department_keys()
        if allowed is None:
            return

        if not allowed:
            raise PermissionError(
                "Your user record does not say which department you lead, so "
                "nothing can be edited. Ask an admin to set your job title."
            )

        family_label = self._family_name()
        for shot in shots:
            if not shot.shot_name:
                continue
            baseline = getattr(shot, "_baseline", None)
            before = Shot.from_dict(dict(baseline)) if baseline else \
                stored.get(_shot_key(shot.reel_episode, shot.shot_name))
            if before is None:
                raise PermissionError(
                    f"{shot.shot_name} is not in this project. Adding shots is "
                    "a coordinator's job."
                )
            if baseline:
                before.status = baseline.get("status", before.status)

            if self._shot_level_fields(before) != self._shot_level_fields(shot):
                raise PermissionError(
                    f"{shot.shot_name}: as {family_label} lead you can change "
                    f"only the {family_label} columns, not the shot itself."
                )

            for key, info in shot.departments.items():
                if key in allowed:
                    continue
                if info.to_dict() != before.dept(key).to_dict():
                    raise PermissionError(
                        f"{shot.shot_name}: {self._department_name(key)} belongs to another "
                        f"department. As {family_label} lead you can change "
                        f"only {family_label}."
                    )

    # ------------------------------------------------------------------
    # An artist's own status
    # ------------------------------------------------------------------
    def _is_own_undo(self, shot: Shot, dept_key: str, back_to: str, undo_of: str) -> bool:
        """
        Whether putting `back_to` on this department undoes this person's own
        last change: the department still shows what they set, and the newest
        history line for it is theirs, from `back_to` to `undo_of`.
        """
        if not self.username or (shot.dept(dept_key).status or "") != (undo_of or ""):
            return False
        try:
            from slate.core.infra.change_history import read_history
            rows = read_history(self.db_manager, self.project_code, shot.shot_name, 50,
                                shot_id=shot.id, reel=shot.reel_episode)
        except Exception as exc:
            logging.debug("Undo check could not read the history: %s", exc)
            return False
        field = f"{dept_key}_status"
        last = next((r for r in rows if r.get("field_changed") == field), None)
        return bool(last) and str(last.get("author") or "").lower() == self.username.lower() \
            and (last.get("old_value") or "") == (back_to or "") \
            and (last.get("new_value") or "") == (undo_of or "")

    def update_department_status(self, shot_name: str, reel: str, dept_key: str,
                                 status: str, current_version: int,
                                 actor_identities=None, shot_id=None, undo_of=None) -> bool:
        """
        Set the status of one department row - the artist's way in.

        It is deliberately the narrowest write in the system: one field, on one
        department, on a shot they are named on, through the same save as
        everything else (so only that department's row is written, with the
        shot, in one transaction, and the history says so). The check happens
        here rather than only in the interface, so a caller that skips the UI
        cannot widen it.

        ``undo_of`` is the status this person set and now takes back: an undo
        may put back a status they could not choose themselves, but only over
        their own last change.
        """
        self.last_error = ""
        if is_offline_fallback():
            raise OfflineError(
                "The central database is unreachable. Changes made now would "
                "not reach anyone else, so saving is disabled until it returns."
            )

        dept_key = str(dept_key or "").strip().lower()
        if dept_key not in set(department_keys()):
            logging.error("Rejected status change for unknown department '%s'", dept_key)
            return False

        full_rights = can_edit_dashboard(self.user_roles)
        if not full_rights and not can_edit_own_status(self.user_roles):
            raise PermissionError("You do not have permission to change a status.")
        self._check_project_open()

        if shot_id is not None and int(shot_id) >= 0:
            found = self.read_shots_by_id([shot_id])
        else:
            found = self._read(" AND reel=%s AND shot_name=%s", (str(reel or ""), shot_name))
        if not found:
            self.last_error = f"{shot_name} is not in this project any more."
            return False
        shot = found[0]
        name = shot.shot_name

        # An artist may say "working" or "done, look at it". Approved, Retake
        # and Omit are verdicts - somebody else's to give - so they are refused
        # here, where a caller that skips the dropdown still meets them.
        # Clearing your own status (an Undo back to blank) is allowed too.
        if str(status or "").strip() and not can_set_status(self.user_roles, status) \
                and not (undo_of is not None and self._is_own_undo(shot, dept_key, status, undo_of)):
            allowed = ", ".join(sorted(artist_statuses()))
            if shot_status.canonical(status) in (shot_status.APPROVED, shot_status.RETAKE):
                reason = f"'{status}' is a review verdict."
            else:
                reason = f"'{status}' is set by a supervisor or coordinator."
            raise PermissionError(f"{reason} You can set {allowed}; a supervisor or "
                                  "coordinator sets the rest.")

        if not full_rights:
            # Somebody without full rights may only touch a department they
            # are personally named on.
            assigned = str(shot.dept(dept_key).artist or "").strip().lower()
            identities = {str(i).strip().lower()
                          for i in (actor_identities or []) if str(i).strip()}
            if not assigned or assigned not in identities:
                raise PermissionError(
                    f"You are not assigned to {self._department_name(dept_key)} on {name}."
                )

        if int(current_version or 0) != 0 and int(current_version or 0) != int(shot.version or 0):
            raise StaleDataError(f"{name} has been changed by someone else.")

        if (shot.dept(dept_key).status or "") == (status or ""):
            return True

        before = Shot.from_dict(shot.to_dict())
        before.id, before.version = shot.id, shot.version
        before.status = shot.status
        shot.dept(dept_key).status = status
        if not self._write([(shot, before)]):
            raise RuntimeError(self.last_error or f"{name}: the status could not be saved.")
        return True


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat()
