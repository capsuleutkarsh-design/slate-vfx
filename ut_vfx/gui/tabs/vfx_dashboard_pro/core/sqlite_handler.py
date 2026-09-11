import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from ..models.shot_model import Shot, DepartmentInfo
from ut_vfx.core.domain.access import ( artist_statuses, can_set_status,
    OfflineError, can_edit_dashboard, can_edit_own_status, is_offline_fallback,
)
from ut_vfx.core.domain.departments import department_keys
from ut_vfx.core.infra.database_manager import DatabaseManager


class StaleDataError(Exception):
    """Raised when trying to save a shot that has been modified by another user."""


class SQLiteHandler:
    """
    Adapter compatible with ExcelHandler APIs, backed by tracking_shots/tracking_tasks.
    """


    def __init__(
        self,
        project_code: str,
        db_manager: DatabaseManager = None,
        user_id: int = 1,
        user_role: str = "artist",
        department_family: str = "",
    ):
        self.project_code = project_code
        self.user_id = int(user_id or 1)
        # The department a scoped role (a lead) is confined to. Empty means
        # the person's job title named no department, so a scoped role can
        # edit nothing until an admin sets one.
        self.department_family = str(department_family or "").strip().lower()

        if isinstance(user_role, list):
            self.user_roles = [str(r).lower() for r in user_role if str(r).strip()]
            self.user_role = self.user_roles[0] if self.user_roles else "artist"
        else:
            role = str(user_role or "artist").lower()
            self.user_role = role
            self.user_roles = [role]

        from ut_vfx.core.infra.database_manager import database_manager

        self.db_manager = db_manager or database_manager

        self.notifier = None
        try:
            from ut_vfx.core.domain.notification_manager import NotificationManager

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

    @staticmethod
    def _serialize_shot(shot: Shot) -> str:
        # Underscored fields are UI state, not shot data. Storing _modified
        # meant a shot saved from the grid was "unsaved" in every session
        # after, for everyone.
        data = {k: v for k, v in asdict(shot).items() if not k.startswith("_")}
        return json.dumps(data, default=str)

    def _get_db_shot_row(self, shot_name: str,
                         reel: str = None) -> Optional[Dict[str, Any]]:
        """
        One shot's row. The reel is part of a shot's identity, so a name on its
        own can match more than one row - SH010 may exist in ReelA and ReelB.
        """
        if reel is not None:
            return self.db_manager.execute_query(
                "SELECT id, data_json, version FROM tracking_shots "
                "WHERE project_code=%s AND reel=%s AND shot_name=%s",
                (self.project_code, reel, shot_name), fetch="one",
            )

        rows = self.db_manager.execute_query(
            "SELECT id, data_json, version, reel FROM tracking_shots "
            "WHERE project_code=%s AND shot_name=%s",
            (self.project_code, shot_name), fetch="all",
        ) or []
        if len(rows) > 1:
            logging.warning(
                "Shot name '%s' exists in %d reels on project %s; "
                "pass the reel to address one of them.",
                shot_name, len(rows), self.project_code,
            )
        return rows[0] if rows else None

    @staticmethod
    def _safe_json_load(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _build_tasks_payload(self, shot_id: int, shot: Shot) -> List[Dict[str, Any]]:
        tasks_payload = []
        for dept_key in department_keys():
            dept = shot.dept(dept_key)
            artist_name = dept.artist or ""
            tasks_payload.append(
                {
                    "shot_id": shot_id,
                    "department": dept_key,
                    "status": dept.status or "",
                    "artist": artist_name,
                    "artist_id": self.db_manager.get_user_id(artist_name) if artist_name else None,
                    "bid_days": dept.bid_days or 0.0,
                    "target": dept.target or dept.eta or "",
                }
            )
        return tasks_payload

    def _save_tasks_for_shot(self, shot_id: int, shot: Shot) -> bool:
        tasks_payload = self._build_tasks_payload(shot_id, shot)
        if not tasks_payload:
            return True
        result = self.db_manager.save_tracking_tasks(self.project_code, tasks_payload)
        return bool(result is None or result)

    def _apply_task_overrides(self, shot: Shot, task_map: Dict[str, Dict[str, Any]]):
        # Departments present in the database but not in departments.json are
        # still applied, so removing one from config never destroys its data.
        for dept_key in set(department_keys()) | set(task_map.keys()):
            task = task_map.get(dept_key)
            if not task:
                continue
            dept = shot.dept(dept_key)
            dept.status = task.get("status") or dept.status or ""
            dept.artist = task.get("artist") or task.get("artist_name") or dept.artist or ""
            dept.bid_days = float(task.get("bid_days") or 0.0)
            dept.target = task.get("target_date") or task.get("target") or dept.target or ""

        if not shot.assigned_artist and shot.dept("comp").artist:
            shot.assigned_artist = shot.dept("comp").artist

    def _set_shot_field_value(self, payload: Dict[str, Any], field: str, value) -> Tuple[Any, bool]:
        field_name = str(field or "").strip()
        if not field_name:
            return None, False

        if field_name in {"status", "overall_status"}:
            old_val = payload.get("status")
            payload["status"] = value
            self._payload_department(payload, "comp")["status"] = value
            return old_val, old_val != value

        if field_name in {"assigned_artist", "artist"}:
            old_val = payload.get("assigned_artist")
            new_value = value or ""
            payload["assigned_artist"] = new_value
            self._payload_department(payload, "comp")["artist"] = new_value
            return old_val, old_val != new_value

        if field_name in {"curr_version", "version"}:
            old_val = payload.get("curr_version")
            payload["curr_version"] = value
            return old_val, old_val != value

        # Department fields, in any of the spellings callers use:
        #   "comp_dept.status", "departments.comp.status", "comp.status"
        dept_target = self._resolve_department_field(field_name)
        if dept_target:
            dept_key, leaf = dept_target
            departments = payload.get("departments")
            if not isinstance(departments, dict):
                departments = {}
                payload["departments"] = departments
            entry = departments.get(dept_key)
            if not isinstance(entry, dict):
                entry = {}
                departments[dept_key] = entry
            old_val = entry.get(leaf)
            entry[leaf] = value
            return old_val, old_val != value

        if "." in field_name:
            parts = [p for p in field_name.split(".") if p]
            if not parts:
                return None, False
            target = payload
            for part in parts[:-1]:
                node = target.get(part)
                if not isinstance(node, dict):
                    node = {}
                    target[part] = node
                target = node
            leaf = parts[-1]
            old_val = target.get(leaf)
            target[leaf] = value
            return old_val, old_val != value

        if field_name not in Shot.__dataclass_fields__:
            logging.error(
                "Rejected update to unknown shot field '%s' on project %s",
                field_name, self.project_code
            )
            return None, None   # None (not False) means "invalid field"

        old_val = payload.get(field_name)
        payload[field_name] = value
        return old_val, old_val != value

    @staticmethod
    def _payload_department(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
        """The department sub-dict inside a shot payload, created if absent."""
        departments = payload.get("departments")
        if not isinstance(departments, dict):
            departments = {}
            payload["departments"] = departments
        entry = departments.get(key)
        if not isinstance(entry, dict):
            entry = {}
            departments[key] = entry
        return entry

    @staticmethod
    def _resolve_department_field(field_name: str):
        """
        Map a field name onto (department_key, attribute), or None.

        Accepts "comp_dept.status", "departments.comp.status" and "comp.status".
        """
        parts = [p for p in str(field_name or "").split(".") if p]
        if len(parts) < 2:
            return None

        if parts[0] == "departments" and len(parts) >= 3:
            key, leaf = parts[1], parts[2]
        elif len(parts) == 2:
            key, leaf = parts[0], parts[1]
        else:
            return None

        key = key[:-5] if key.endswith("_dept") else key
        key = key.lower()

        if leaf not in DepartmentInfo.__dataclass_fields__:
            return None
        return key, leaf

    def _notify_assignment(self, shot_name: str, old_artist: str, new_artist: str, dept_key: str = "comp"):
        if not self.notifier:
            return
        if new_artist and new_artist != old_artist:
            try:
                msg = f"You have been assigned to: {shot_name} ({dept_key.upper()})"
                self.notifier.add_notification(new_artist, msg, "assignment")
            except Exception as e:
                logging.debug(f"Notification failed for assignment: {e}")

    def _notify_status(self, shot_name: str, artist: str, old_status: str, new_status: str):
        if not self.notifier:
            return
        if not artist or old_status == new_status:
            return
        try:
            msg = f"Shot update: {shot_name} is now {new_status}"
            self.notifier.add_notification(artist, msg, "update")
        except Exception as e:
            logging.debug(f"Notification failed for status update: {e}")

    def _log_change(self, entity_type: str, entity_id: str, action: str, field: str, old_val, new_val):
        try:
            self.db_manager.log_change_event(
                self.project_code,
                entity_type,
                entity_id,
                self.user_id,
                action,
                field,
                old_val,
                new_val,
            )
        except Exception as e:
            logging.debug(f"History log write failed: {e}")

    def _log_shot_and_task_changes(self, shot: Shot, old_data: Dict[str, Any]):
        old_status = old_data.get("status")
        if old_status != shot.status:
            self._log_change("shot", shot.shot_name, "UPDATE", "status", old_status, shot.status)

        old_assigned = old_data.get("assigned_artist")
        if old_assigned != shot.assigned_artist:
            self._log_change("shot", shot.shot_name, "ASSIGN", "assigned_artist", old_assigned, shot.assigned_artist)
            self._notify_assignment(shot.shot_name, old_assigned or "", shot.assigned_artist or "", "comp")

        old_departments = old_data.get("departments")
        if not isinstance(old_departments, dict):
            old_departments = {}

        for dept_key in department_keys():
            new_dept = shot.dept(dept_key)
            # Read either the new "departments" map or the legacy "<key>_dept".
            old_dept = old_departments.get(dept_key)
            if not isinstance(old_dept, dict):
                old_dept = old_data.get(f"{dept_key}_dept", {})
            if not isinstance(old_dept, dict):
                old_dept = {}

            old_dept_status = old_dept.get("status", "")
            old_dept_artist = old_dept.get("artist", "")

            if old_dept_status != new_dept.status:
                self._log_change(
                    "task",
                    f"{shot.shot_name}_{dept_key}",
                    "UPDATE",
                    f"{dept_key}_status",
                    old_dept_status,
                    new_dept.status,
                )
                self._notify_status(shot.shot_name, new_dept.artist or "", old_dept_status, new_dept.status or "")

            if old_dept_artist != new_dept.artist:
                self._log_change(
                    "task",
                    f"{shot.shot_name}_{dept_key}",
                    "ASSIGN",
                    f"{dept_key}_artist",
                    old_dept_artist,
                    new_dept.artist,
                )
                self._notify_assignment(shot.shot_name, old_dept_artist or "", new_dept.artist or "", dept_key)

    def _insert_new_shot(self, shot: Shot) -> bool:
        shot_tuple = (shot.shot_name, shot.status, shot.priority, self._serialize_shot(shot))
        return bool(self.db_manager.save_tracking_shots(self.project_code, [shot_tuple]))

    def _write_single_shot(self, shot: Shot) -> bool:
        if not shot.shot_name:
            return False

        db_row = self._get_db_shot_row(shot.shot_name, shot.reel_episode)
        if not db_row:
            created = self._insert_new_shot(shot)
            if not created:
                return False
            db_row = self._get_db_shot_row(shot.shot_name, shot.reel_episode)
            if not db_row:
                return False
            shot_id = int(db_row.get("id") or 0)
            shot.id = shot_id
            shot.version = int(db_row.get("version") or 1)
            if shot_id:
                self._save_tasks_for_shot(shot_id, shot)
            self._log_shot_and_task_changes(shot, {})
            return True

        old_data = self._safe_json_load(db_row.get("data_json"))
        shot_id = int(db_row.get("id") or 0)
        db_version = int(db_row.get("version") or 0)
        current_version = int(getattr(shot, "version", 0) or 0)

        if current_version != 0 and db_version != 0 and current_version != db_version:
            raise StaleDataError(
                f"Shot '{shot.shot_name}' has been modified by another user. Please refresh."
            )

        lock_version = db_version
        shot.version = lock_version
        json_str = self._serialize_shot(shot)

        success = self.db_manager.update_tracking_shot_safe(
            self.project_code, shot.shot_name, json_str, lock_version,
            reel=shot.reel_episode,
        )
        if not success:
            raise StaleDataError(
                f"Shot '{shot.shot_name}' has been modified by another user. Please refresh."
            )

        shot.version = lock_version + 1
        if shot_id:
            self._save_tasks_for_shot(shot_id, shot)
        self._log_shot_and_task_changes(shot, old_data)
        return True

    def _write_batch_shots(self, shots: List[Shot], force: bool = False) -> bool:
        if not shots:
            return False

        # Concurrency safety: detect stale shots before bulk updating
        if not force:
            existing_rows = self.db_manager.get_tracking_shots(self.project_code) or []
            db_version_map = {
                (str(r.get("reel", "") or "").strip().lower(), str(r.get("shot_name", "")).strip().lower()): int(r.get("version") or 0)
                for r in existing_rows if r.get("shot_name")
            }
            
            stale_shots = []
            for shot in shots:
                current_v = int(getattr(shot, "version", 0) or 0)
                shot_key = (str(getattr(shot, "reel_episode", "") or "").strip().lower(), str(shot.shot_name or "").strip().lower())
                db_v = db_version_map.get(shot_key, db_version_map.get(("", shot_key[1]), 0))
                if current_v != 0 and db_v != 0 and current_v != db_v:
                    stale_shots.append(f"'{shot.shot_name}' (local v{current_v} vs db v{db_v})")
            
            if stale_shots:
                conflicts_str = ", ".join(stale_shots[:5])
                if len(stale_shots) > 5:
                    conflicts_str += f" and {len(stale_shots) - 5} more"
                raise StaleDataError(
                    f"Conflict detected for {conflicts_str}. "
                    "Another user has updated these shots in the database. Please refresh to load the latest data."
                )

        batch_data = []
        for shot in shots:
            if not shot.shot_name:
                continue
            batch_data.append((shot.shot_name, shot.status, shot.priority, self._serialize_shot(shot)))

        if not batch_data:
            return False

        if not self.db_manager.save_tracking_shots(self.project_code, batch_data):
            return False

        # Keep relational task table in sync for board/assignment features.
        rows = self.db_manager.get_tracking_shots(self.project_code) or []
        row_by_key = {
            (str(r.get("reel", "") or "").strip().lower(), str(r.get("shot_name", "")).strip().lower()): r
            for r in rows if r.get("shot_name")
        }

        tasks_payload = []
        for shot in shots:
            shot_key = (str(getattr(shot, "reel_episode", "") or "").strip().lower(), str(shot.shot_name or "").strip().lower())
            row = row_by_key.get(shot_key) or row_by_key.get(("", shot_key[1]))
            if not row:
                continue
            shot.id = int(row.get("id") or -1)
            shot.version = int(row.get("version") or shot.version or 1)
            tasks_payload.extend(self._build_tasks_payload(shot.id, shot))

        if tasks_payload:
            self.db_manager.save_tracking_tasks(self.project_code, tasks_payload)
        return True

    def read_shots(self) -> List[Shot]:
        """Fetch all shots for this project from DB and deserialize."""
        try:
            tasks = self.db_manager.get_tracking_tasks(self.project_code) or []
            tasks_by_shot: Dict[int, Dict[str, Dict[str, Any]]] = {}
            for task in tasks:
                shot_id = task.get("shot_id")
                dept = task.get("department")
                if shot_id is None or not dept:
                    continue
                tasks_by_shot.setdefault(shot_id, {})[dept] = task

            raw_data = self.db_manager.get_tracking_shots(self.project_code) or []
            shots = []
            for item in raw_data:
                try:
                    shot = Shot.from_dict(item)
                    shot.id = int(item.get("id") or -1)
                    v_raw = item.get("version")
                    shot.version = int(v_raw) if v_raw is not None else int(getattr(shot, "version", 1) or 1)
                    self._apply_task_overrides(shot, tasks_by_shot.get(shot.id, {}))
                    shots.append(shot)
                except Exception as e:
                    logging.exception(f"Failed to deserialize shot {item.get('shot_name', 'unknown')}: {e}")
            return shots
        except Exception as e:
            logging.exception(f"SQLiteHandler read_shots failed: {e}")
            return []

    def write_shots(self, shots: List[Shot], force: bool = False) -> bool:
        """Serialize and save shots to DB."""
        if not self.project_code:
            return False
        if not shots:
            return True

        self._check_permission()
        self._assert_within_department(shots)

        if len(shots) == 1:
            return self._write_single_shot(shots[0])
        return self._write_batch_shots(shots, force=force)

    # ------------------------------------------------------------------
    # Department scoping
    # ------------------------------------------------------------------

    def _scoped_department_keys(self):
        """The department keys this person may edit, or None if unrestricted."""
        from ut_vfx.core.domain.access import is_department_scoped
        from ut_vfx.core.domain.departments import families

        if not is_department_scoped(self.user_roles):
            return None
        members = families().get(self.department_family, [])
        return {dept.key for dept in members}

    @staticmethod
    def _shot_level_fields(shot: Shot) -> dict:
        """Everything about a shot that is not a department, for comparison."""
        data = shot.to_dict()
        for key in ("departments", "version", "id"):
            data.pop(key, None)
        return data

    def _assert_within_department(self, shots) -> None:
        """
        A department-scoped role may change only its own department's columns.

        This is checked against what is stored, not against what the grid
        offered: a lead who edits a comp cell through some path the interface
        forgot to lock is still stopped here. Creating shots is a coordinator's
        job, so a scoped role cannot do that either.
        """
        allowed = self._scoped_department_keys()
        if allowed is None:
            return

        if not allowed:
            raise PermissionError(
                "Your user record does not say which department you lead, so "
                "nothing can be edited. Ask an admin to set your job title."
            )

        family_label = self.department_family.title()

        # "Before" is loaded the same way the grid loaded it - through
        # read_shots, task overrides and all - so an untouched shot compares
        # equal. Comparing against the raw stored JSON flagged every shot,
        # because assigned_artist is derived from the comp task on load.
        before = {}
        for stored in self.read_shots():
            key = (str(stored.reel_episode or "").strip().lower(),
                   str(stored.shot_name or "").strip().lower())
            before[key] = stored

        for shot in shots:
            if not shot.shot_name:
                continue

            key = (str(shot.reel_episode or "").strip().lower(),
                   str(shot.shot_name or "").strip().lower())
            stored = before.get(key) or before.get(("", key[1]))
            if stored is None:
                raise PermissionError(
                    f"{shot.shot_name} is not in this project. Adding shots is "
                    "a coordinator's job."
                )

            if self._shot_level_fields(stored) != self._shot_level_fields(shot):
                raise PermissionError(
                    f"{shot.shot_name}: as {family_label} lead you can change "
                    f"only the {family_label} columns, not the shot itself."
                )

            for key, info in shot.departments.items():
                if key in allowed:
                    continue
                if info.to_dict() != stored.dept(key).to_dict():
                    raise PermissionError(
                        f"{shot.shot_name}: {key} belongs to another "
                        f"department. As {family_label} lead you can change "
                        f"only {family_label}."
                    )

    def update_department_status(self, shot_name: str, reel: str, dept_key: str,
                                 status: str, current_version: int,
                                 actor_identities=None) -> bool:
        """
        Set the status of one department row.

        This is the artist's way in. It is deliberately the narrowest write in
        the system: one field, on one department, on a shot they are named on.
        The check happens here rather than only in the interface, so a caller
        that skips the UI cannot widen it.
        """
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

        # An artist may say "working" or "done, look at it". Approved, Retake
        # and Omit are verdicts - somebody else's to give - so they are refused
        # here, where a caller that skips the dropdown still meets them.
        if not can_set_status(self.user_roles, status):
            raise PermissionError(
                f"'{status}' is a review verdict. You can set "
                f"{', '.join(sorted(artist_statuses()))}; a supervisor or "
                "coordinator sets the rest."
            )

        row = self._get_db_shot_row(shot_name, reel)
        if not row:
            return False

        payload = self._safe_json_load(row.get("data_json"))
        shot = Shot.from_dict(payload)
        shot.shot_name = shot_name

        if not full_rights:
            # Somebody without full rights may only touch a department they
            # are personally named on.
            assigned = str(shot.dept(dept_key).artist or "").strip().lower()
            identities = {str(i).strip().lower()
                          for i in (actor_identities or []) if str(i).strip()}
            if not assigned or assigned not in identities:
                raise PermissionError(
                    f"You are not assigned to {dept_key} on {shot_name}."
                )

        db_version = int(row.get("version") or 0)
        if int(current_version or 0) != 0 and db_version != 0                 and int(current_version or 0) != db_version:
            raise StaleDataError(
                f"Shot '{shot_name}' has been modified. Update rejected."
            )

        old_status = shot.dept(dept_key).status
        if old_status == status:
            return True

        entry = self._payload_department(payload, dept_key)
        entry["status"] = status

        if not self.db_manager.update_tracking_shot_safe(
            self.project_code, shot_name,
            json.dumps(payload, default=str), db_version, reel=reel,
        ):
            raise StaleDataError(
                f"Shot '{shot_name}' has been modified. Update rejected."
            )

        shot_id = int(row.get("id") or 0)
        if shot_id:
            shot.dept(dept_key).status = status
            self._save_tasks_for_shot(shot_id, shot)

        self._log_change("task", f"{shot_name}_{dept_key}", "UPDATE",
                         f"{dept_key}_status", old_status, status)
        self._notify_status(shot_name, shot.dept(dept_key).artist or "",
                            old_status or "", status or "")
        return True

    def update_shot_field(self, shot_name: str, field: str, value, current_version: int,
                          reel: Optional[str] = None) -> bool:
        """
        Updates a specific field of a shot using optimistic locking.
        """
        self._check_permission()

        try:
            row = self._get_db_shot_row(shot_name, reel)
            if not row:
                return False

            shot_id = int(row.get("id") or 0)
            db_version = int(row.get("version") or 0)
            if int(current_version or 0) != 0 and db_version != 0 and int(current_version or 0) != db_version:
                raise StaleDataError(f"Shot '{shot_name}' has been modified. Update rejected.")

            payload = self._safe_json_load(row.get("data_json"))
            old_val, changed = self._set_shot_field_value(payload, field, value)
            if changed is None:
                return False        # unknown field - never report success
            if not changed:
                return True

            json_str = json.dumps(payload, default=str)
            success = self.db_manager.update_tracking_shot_safe(
                self.project_code, shot_name, json_str, db_version, reel=reel,
            )
            if not success:
                raise StaleDataError(f"Shot '{shot_name}' has been modified. Update rejected.")

            self._log_change("shot", shot_name, "UPDATE", field, old_val, value)

            if shot_id:
                shot_obj = Shot.from_dict(payload)
                shot_obj.shot_name = shot_name
                self._save_tasks_for_shot(shot_id, shot_obj)

            if field in {"assigned_artist", "artist"}:
                self._notify_assignment(shot_name, old_val or "", str(value or ""), "comp")
            elif field in {"status", "overall_status"}:
                target_artist = payload.get("assigned_artist") or ""
                self._notify_status(shot_name, target_artist, str(old_val or ""), str(value or ""))

            return True
        except StaleDataError:
            raise
        except Exception as e:
            logging.exception(f"Granular update failed for {shot_name}: {e}")
            return False

    def create_backup(self):
        logging.info("SQLiteHandler: Database central backup covers this data.")
        return "DB_BACKUP_MANAGED_centrally"

    def debug_column_mapping(self):
        logging.info("SQLiteHandler: No column mapping (Direct Object Storage).")
