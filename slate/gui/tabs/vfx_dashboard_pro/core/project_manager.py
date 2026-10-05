import logging
import json
import os
from copy import deepcopy
from typing import List, Optional, Dict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from slate.utils.security import SecurityValidator

@dataclass
class ProjectConfig:
    code: str
    name: str
    # Neither of these is genuinely required. The Excel file is a passbook
    # backup the software writes to, not something a project needs to exist,
    # and the ingest creates projects with no number. Making them mandatory
    # meant every project Build & Ingest created was thrown away on load.
    project_number: int = 0
    excel_path: str = ""
    local_excel_path: str = ""
    sheet_name: str = "MASTER"
    header_row: int = 2
    data_start_row: int = 3
    folder_base: str = ""
    folder_template: Dict[str, str] = field(default_factory=dict)
    column_mapping: Dict[str, str] = field(default_factory=dict)
    status: str = "active"

def _default_folder_template() -> Dict[str, str]:
    """Per-department folder paths, matching the project folder template."""
    from slate.core.domain.departments import load_departments

    template = {
        "scan": "05_Reels/{reel}/{shot}/01_Scan",
        "deliver": "05_Reels/{reel}/{shot}/08_Deliver",
        # Open Output, Quick Look and auto-publish ask for "output"; new
        # projects used to have only "deliver", so they found nothing.
        "output": "05_Reels/{reel}/{shot}/08_Deliver",
    }
    for dept in load_departments():
        if dept.folder:
            template[dept.key] = "05_Reels/{reel}/{shot}/" + dept.folder
    return template


def default_column_mapping() -> Dict[str, str]:
    """
    The standard sheet layout, with a column set for every department.

    Used for every project that does not bring its own mapping - including
    the ones Build & Ingest creates, whose passbook the software writes from
    nothing.
    """
    mapping = {
        "serial": "A", "thumbnail": "B", "reel": "C", "shot_name": "D",
        "frames": "E", "sow": "F", "internal_comment": "G",
        "client_feedback": "H", "director_feedback": "I",
        "scan_status": "J", "assigned_artist": "K",
        "wip_date": "L", "shot_done_date": "M",
        "internal_status": "N", "overall_status": "O",
        "client_status": "P", "version": "Q", "submission_date": "R",
        "roto_bid": "S", "roto_artist": "T", "roto_status": "U", "roto_eta": "V",
        "dmp_status": "W", "dmp_mandays": "X", "dmp_eta": "Y",
        "cg_status": "Z", "cg_artist": "AA", "cg_mandays": "AB", "cg_eta": "AC",
        "latest_version": "AD", "priority": "AE", "shot_type": "AF",
        "slapcomp_artist": "AG", "slapcomp_status": "AH", "slapcomp_bid": "AI",
        "slapcomp_eta": "AJ", "slapcomp_target": "AK",
    }
    return _extend_mapping_with_departments(mapping)


def passbook_path(project_code: str) -> Path:
    """
    Where a project's Excel passbook lives: the central server folder.

    That is the folder the software and the server already exchange data
    through, so it is reachable from every machine and backed up with the rest.
    """
    from slate.core.infra.global_config import GlobalConfig
    safe = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(project_code))
    return GlobalConfig.server_root() / "Tracking" / f"{safe}_tracking.xlsx"


def _extend_mapping_with_departments(mapping: Dict[str, str]) -> Dict[str, str]:
    """
    Give every department a set of Excel columns.

    The hand-written default covered roto, dmp, cg and slapcomp only - comp,
    prep and mgfx had no columns at all, so a new project's backup sheet could
    not hold its main department's artist or status. Missing columns are
    appended after the last one in use.
    """
    from openpyxl.utils import column_index_from_string, get_column_letter

    from slate.core.domain.departments import load_departments

    mapping = dict(mapping)

    used = set()
    highest = 0
    for letter in mapping.values():
        try:
            index = column_index_from_string(str(letter))
        except Exception:
            continue
        used.add(index)
        highest = max(highest, index)

    next_index = highest + 1

    def claim() -> str:
        nonlocal next_index
        while next_index in used:
            next_index += 1
        used.add(next_index)
        return get_column_letter(next_index)

    # Shot fields the backup must hold for a restore to keep the schedule.
    for field in ("target", "in_os", "edit_status", "description", "prev_version"):
        if field not in mapping:
            mapping[field] = claim()

    for dept in load_departments():
        key = dept.key
        # A department counts as mapped if it already has an artist or status
        # column under any of the historical spellings.
        aliases = {
            "artist": [f"{key}_artist"],
            "status": [f"{key}_status", f"{key}_required", f"{key}_comp"],
            "bid": [f"{key}_bid", f"{key}_mandays"],
            "eta": [f"{key}_eta", f"{key}_target"],
            "actual": [f"{key}_actual"],
        }
        for field, names in aliases.items():
            if any(name in mapping for name in names):
                continue
            mapping[names[0]] = claim()

    return mapping


class ProjectManager:
    """
    The dashboard's projects - in the database (tracking_projects), only.

    Adding, editing, archiving or deleting a project used to write
    projects.json inside the install folder as well: on an installed build
    that folder is read-only, and each machine kept its own copy that nobody
    else saw. The studio-wide lists (shot types, priorities) are studio
    settings now (shot_status); the packaged projects.json is only read for the
    Excel template's auxiliary sheets, and never written.

    There is no built-in sample project any more (it pointed at a 42 MB client
    sheet shipped inside the package).
    """

    def __init__(self):
        self.config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        self.config_path = os.path.join(self.config_dir, "projects.json")
        self.projects: Dict[str, ProjectConfig] = {}
        self.extended_sheets = {}
        self.shot_types = []
        self.priority_levels = []
        self.departments = []
        self.feedback_sources = []
        # Why the last change was refused, for the person who asked for it.
        self.last_error = ""
        self.load_config()

    @property
    def default_project(self):
        """No sample project is opened by default any more (the dashboard remembers yours)."""
        return None

    def load_config(self):
        from slate.core.infra.database_manager import database_manager

        db_projects = database_manager.get_all_tracking_projects() or []
        valid_fields = ProjectConfig.__dataclass_fields__.keys()
        for p_data in db_projects:
            try:
                p_code = p_data.get('code')
                if p_code:
                    filtered = {k: v for k, v in p_data.items() if k in valid_fields}
                    self.projects[p_code] = ProjectConfig(**filtered)
            except Exception as e:
                logging.exception(f"Error loading project from DB: {e}")
        logging.info("ProjectManager: %d projects from the database.", len(self.projects))
        self._load_aux_data()

    def _load_aux_data(self):
        """Shared lists: the studio's settings, and the packaged sheet layout."""
        from slate.core.domain import shot_status
        from slate.core.domain.departments import load_departments
        data = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except (OSError, json.JSONDecodeError, TypeError) as e:
                logging.error(f"ProjectManager: could not read the packaged sheet layout: {e}")
        self.extended_sheets = data.get("extended_sheets", {})
        self.shot_types = shot_status.shot_types()
        self.priority_levels = [value for value, _label in shot_status.priorities()]
        self.departments = [d.name for d in load_departments()]
        self.feedback_sources = data.get("feedback_sources", ["Client", "Director", "Internal", "Slate"])

    # Kept for older callers.
    def _load_json_aux_data(self):
        self._load_aux_data()

    def _populate_aux_data(self, data):
        self._load_aux_data()

    def get_all_projects(self) -> List[ProjectConfig]:
        return sorted(self.projects.values(), key=lambda p: (p.project_number, p.code))

    def get_project(self, code: str) -> Optional[ProjectConfig]:
        return self.projects.get(code)

    def ensure_excel_path(self, code: str) -> str:
        """
        Make sure a project has somewhere for its passbook, and return it.

        A project made by Build & Ingest has no Excel path and no column
        mapping. Without this, the first save after an ingest reported
        "Excel mirror failed" and the passbook was never opened.
        """
        project = self.get_project(code)
        if not project:
            return ""

        changed = False
        if not project.excel_path:
            project.excel_path = str(passbook_path(code))
            changed = True
        if not project.column_mapping:
            project.column_mapping = default_column_mapping()
            changed = True
        else:
            # Columns added since the mapping was made (targets, actual days)
            # go after the last one in use; nothing already mapped moves.
            extended = _extend_mapping_with_departments(project.column_mapping)
            if extended != project.column_mapping:
                project.column_mapping = extended
                changed = True

        if changed:
            try:
                self._save_project_to_db(project)
            except Exception as exc:
                logging.warning("Could not record the passbook path for %s: %s", code, exc)
        return project.excel_path

    def get_excel_path(self, code: str) -> str:
        project = self.get_project(code)
        if not project:
            return ""
        if project.excel_path and os.path.exists(project.excel_path):
            return project.excel_path
        if project.local_excel_path:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            local_path = os.path.join(base_dir, project.local_excel_path)
            if os.path.exists(local_path):
                return local_path
        return project.excel_path

    @staticmethod
    def _save_project_to_db(project: ProjectConfig) -> None:
        from slate.core.infra.database_manager import database_manager

        existing = database_manager.get_tracking_project(project.code) or {}
        config = dict(existing) if isinstance(existing, dict) else {}
        # Keep what other screens store with the project (the default column
        # layout) - only the project's own fields are replaced.
        config.update(asdict(project))
        save_ok = database_manager.save_tracking_project(project.code, project.name, json.dumps(config))
        if not save_ok:
            raise RuntimeError(getattr(save_ok, "error", "") or f"Failed to save project {project.code}")

    def add_project(self, code: str, name: str, excel_path: str, folder_base: str,
                    sheet_name: str = "MASTER", header_row: int = 2, data_start_row: int = 3,
                    column_mapping: dict = None):
        """Creates a new project in the database. Returns it, or None (see last_error)."""
        self.last_error = ""
        from slate.core.domain.naming import shot_name_problem
        from slate.core.infra.database_manager import database_manager
        # The code names folders, the passbook file and every row of the
        # project: one spelling, in capitals, with no spaces or slashes.
        code = str(code or "").strip().upper()
        problem = shot_name_problem(code, "The project code")
        if problem:
            self.last_error = problem
            return None
        # Every project, archived ones too: re-using an archived code quietly
        # brought that project back with the new settings.
        rows = database_manager.execute_query("SELECT code, active FROM tracking_projects", fetch="all")
        if rows is None:
            self.last_error = "The list of projects could not be read, so nothing was created."
            return None
        for row in rows:
            row = dict(row)
            if str(row.get("code") or "").upper() == code:
                archived = str(row.get("active")).strip().lower() not in ("1", "t", "true", "y", "yes")
                self.last_error = (f"{row['code']} is archived - restore it (Manage project > Show "
                                   "archived projects) or pick another code." if archived
                                   else f"There is already a project {row['code']}.")
                return None
        next_num = max((p.project_number for p in self.projects.values()), default=0) + 1

        if excel_path:
            excel_valid, _, excel_err = SecurityValidator.validate_file_path(excel_path)
            if not excel_valid:
                logging.warning(f"Security Warning: Invalid Excel Path: {excel_err}")
        if folder_base:
            folder_valid, folder_err = SecurityValidator.validate_directory_path(
                Path(folder_base), must_exist=False)
            if not folder_valid:
                logging.warning(f"Security Warning: Invalid Folder Base: {folder_err}")

        new_project = ProjectConfig(
            code=code,
            name=name or code,
            project_number=next_num,
            excel_path=str(excel_path or ""),
            folder_base=str(folder_base or ""),
            sheet_name=sheet_name,
            header_row=header_row,
            data_start_row=data_start_row,
            column_mapping=column_mapping or default_column_mapping(),
            # Folder paths come from the department registry, so they always
            # match the folders Build & Ingest actually creates.
            folder_template=_default_folder_template(),
        )
        # A plain INSERT: a code taken in the meantime is refused, never merged.
        result = database_manager.execute_update(
            "INSERT INTO tracking_projects (code, name, config_json, active) VALUES (%s, %s, %s, 1)",
            (code, new_project.name, json.dumps(asdict(new_project))))
        if not result:
            self.last_error = (f"There is already a project {code}." if getattr(result, "duplicate", False)
                               else getattr(result, "error", "") or f"Project {code} could not be created.")
            logging.error("ProjectManager: add %s failed: %s", code, self.last_error)
            return None
        self.projects[code] = new_project
        return new_project

    def update_project(self, code: str, name: str, excel_path: str, folder_base: str,
                       sheet_name: str = None, header_row: int = None, data_start_row: int = None):
        """Updates an existing project in the database."""
        self.last_error = ""
        if code not in self.projects:
            self.last_error = f"There is no project {code}."
            return False
        previous = deepcopy(self.projects[code])
        project = self.projects[code]
        project.name = name
        project.excel_path = str(excel_path or "")
        project.folder_base = str(folder_base or "")
        if sheet_name:
            project.sheet_name = sheet_name
        if header_row is not None:
            project.header_row = int(header_row)
        if data_start_row is not None:
            project.data_start_row = int(data_start_row)
        if not project.folder_template:
            project.folder_template = _default_folder_template()
        try:
            self._save_project_to_db(project)
        except Exception as exc:
            self.projects[code] = previous
            self.last_error = str(exc)
            logging.error("ProjectManager: update %s failed: %s", code, exc)
            return False
        return True

    def set_project_folder_base(self, code: str, path: str) -> bool:
        """
        Point a project at its root folder, in the database.

        This used to be written only to projects.json in the install folder,
        so it was lost on the next start, and it filled an empty folder template
        with names (02_Roto, 04_Comp, 09_Output) the studio template does not
        use - the Open folder actions then pointed at folders that do not exist.
        """
        self.last_error = ""
        project = self.get_project(code)
        if not project:
            self.last_error = f"There is no project {code}."
            return False
        previous = deepcopy(project)
        project.folder_base = str(path)
        if not project.folder_template:
            project.folder_template = _default_folder_template()
        else:
            for key, value in _default_folder_template().items():
                project.folder_template.setdefault(key, value)
        try:
            self._save_project_to_db(project)
        except Exception as exc:
            self.projects[code] = previous
            self.last_error = str(exc)
            return False
        return True

    # ------------------------------------------------------------ archive / delete
    def _check_delete_right(self, code, roles) -> bool:
        if roles is not None:
            from slate.core.domain.access import can_delete_project
            if not can_delete_project(roles):
                logging.warning("ProjectManager: archive/delete of %s refused for roles %s", code, roles)
                self.last_error = "You don't have permission to archive or delete a project."
                return False
        return True

    @staticmethod
    def _audit(code, by, action, old, new):
        try:
            from slate.core.infra.database_manager import database_manager
            database_manager.log_change_event(code, "project", code, by or "", action,
                                              "project", old, new)
        except Exception as exc:
            logging.debug("Project %s not written to history: %s", action, exc)

    def archive_project(self, code: str, roles=None, by: str = "") -> bool:
        """
        Hide a project from every list. Its shots, tasks and history stay where
        they are, and restore_project brings it back. This is what Delete was
        used for - and Delete removed the change history with it.
        """
        self.last_error = ""
        if not self._check_delete_right(code, roles):
            return False
        if code not in self.projects:
            self.last_error = f"There is no project {code}."
            return False
        from slate.core.infra.database_manager import database_manager
        result = database_manager.execute_update(
            "UPDATE tracking_projects SET active = 0 WHERE code = %s", (code,))
        if not getattr(result, "changed", result):
            self.last_error = getattr(result, "error", "") or "The database did not archive it."
            return False
        self.projects.pop(code, None)
        self._audit(code, by, "ARCHIVE", "active", "archived")
        return True

    def restore_project(self, code: str, roles=None, by: str = "") -> bool:
        self.last_error = ""
        if not self._check_delete_right(code, roles):
            return False
        from slate.core.infra.database_manager import database_manager
        if code not in {p["code"] for p in self.archived_projects()}:
            self.last_error = f"{code} is not archived."
            return False
        result = database_manager.execute_update(
            "UPDATE tracking_projects SET active = 1 WHERE code = %s", (code,))
        if not getattr(result, "changed", result):
            self.last_error = getattr(result, "error", "") or "There is no archived project by that code."
            return False
        data = database_manager.get_tracking_project(code) or {}
        valid_fields = ProjectConfig.__dataclass_fields__.keys()
        try:
            self.projects[code] = ProjectConfig(**{k: v for k, v in data.items() if k in valid_fields})
        except Exception:
            self.projects[code] = ProjectConfig(code=code, name=data.get("name", code))
        self._audit(code, by, "RESTORE", "archived", "active")
        return True

    @staticmethod
    def archived_projects() -> List[dict]:
        """[{'code', 'name'}] of archived projects."""
        from slate.core.infra.database_manager import database_manager
        rows = database_manager.execute_query(
            "SELECT code, name, active FROM tracking_projects ORDER BY code") or []
        out = []
        for row in rows:
            row = dict(row)
            active = str(row.get("active")).strip().lower()
            if active not in ("1", "t", "true", "y", "yes"):
                out.append({"code": row.get("code"), "name": row.get("name")})
        return out

    def delete_project(self, code: str, roles=None, by: str = "") -> bool:
        """
        Remove a project, its shots, their department rows, versions, review
        notes and delivery packages for good - a new project given the same
        code later must not inherit any of them.

        `roles` are the acting person's: when given, the delete_project ability
        is checked here too, not only by the menu that offers it. The change
        history is kept - it is the audit trail of who did what, and it now
        records the deletion itself.
        """
        self.last_error = ""
        if not self._check_delete_right(code, roles):
            return False
        if code not in self.projects:
            self.last_error = f"There is no project {code}."
            return False
        from slate.core.infra.database_manager import database_manager
        from slate.core.infra.transaction import atomic
        try:
            from slate.core.infra.migrations.workplace_schema import _table_exists
            extra = [(table, sql) for table, sql in (
                ("tracking_version_notes", "DELETE FROM tracking_version_notes WHERE version_id IN "
                                           "(SELECT id FROM tracking_versions WHERE project_code = %s)"),
                ("tracking_versions", "DELETE FROM tracking_versions WHERE project_code = %s"),
                ("tracking_delivery_items", "DELETE FROM tracking_delivery_items WHERE delivery_id IN "
                                            "(SELECT id FROM tracking_deliveries WHERE project_code = %s)"),
                ("tracking_deliveries", "DELETE FROM tracking_deliveries WHERE project_code = %s"),
            ) if _table_exists(database_manager, table)]
            with atomic(database_manager) as tx:
                for _table, sql in extra:
                    tx.write(sql, (code,))
                tx.write("DELETE FROM tracking_tasks WHERE shot_id IN "
                         "(SELECT id FROM tracking_shots WHERE project_code = %s)", (code,))
                tx.write("DELETE FROM tracking_shots WHERE project_code = %s", (code,))
                tx.write("DELETE FROM tracking_projects WHERE code = %s", (code,), expect_rows=True)
        except Exception as exc:
            self.last_error = str(exc)
            logging.error("ProjectManager: delete of %s failed: %s", code, exc)
            return False
        self.projects.pop(code, None)
        self._audit(code, by, "DELETE", "project", "deleted permanently")
        return True

    def get_folder_path(self, code: str, department: str, reel: str, shot: str) -> str:
        project = self.get_project(code)
        if not project:
            return ""
        
        template = project.folder_template.get(department.lower(), "")
        if not template:
            return ""
            
        # --- NORMALIZATION FIXES ---
        # 1. Reel: "REEL 08" -> "REEL_08"
        reel_clean = reel.replace(" ", "_")
        
        # 2. Shot: "VRU_RL08..." -> "VRU_RL_08..."
        # Insert underscore between RL and digits if missing
        import re
        shot_clean = re.sub(r'(RL)(\d+)', r'\1_\2', shot, flags=re.IGNORECASE)
        
        # 3. Format Path
        # Try with cleaned names first (Most likely correct for folders)
        path = template.format(reel=reel_clean, shot=shot_clean)
        from slate.core.domain.naming import path_inside
        try:
            path_inside(project.folder_base, path)      # a stored name with '..' stops here
        except ValueError as exc:
            logging.error("ProjectManager: %s", exc)
            return ""
        full_path = os.path.join(project.folder_base, path)
        
        # 4. Smart/Fuzzy Find (Fix for strict naming mismatch)
        if not os.path.exists(full_path):
            try:
                # Assuming template matches "05_Reels/{reel}" pattern
                base_parts = template.split('/{shot}')
                if len(base_parts) > 0:
                    # 1. Resolve Reel Folder (Fuzzy)
                    base_parts[0] # "05_Reels/{reel}"
                    reels_root = os.path.join(project.folder_base, "05_Reels")
                    
                    found_reel_path = None
                    if os.path.exists(reels_root):
                        # Try exact match first (SC_68)
                        candidate_1 = os.path.join(reels_root, reel_clean)
                        if os.path.exists(candidate_1):
                            found_reel_path = candidate_1
                        else:
                            # Try REEL_XX match logic
                            # Extract digits from 'SC_68' -> '68'
                            import re
                            digits = re.findall(r'\d+', reel_clean)
                            if digits:
                                number_key = digits[0]
                                # Look for "REEL_68" or "SC_68" or "EP_68"
                                for r_child in os.listdir(reels_root):
                                    if number_key in r_child:
                                        # Candidate found
                                        found_reel_path = os.path.join(reels_root, r_child)
                                        break
                    
                    if found_reel_path: # We found the reel folder (e.g. REEL_68)
                         # 2. Resolve Shot Folder (Fuzzy) inside found reel
                         # Search children for shot name
                         # Remove common prefixes for looser matching
                        search_key = shot.replace("SH_", "").strip()
                        for child in os.listdir(found_reel_path):
                            if search_key in child:
                                # Found candidate shot folder: child (e.g. MA2_SC_68_002)
                                # Re-append the rest of the template
                                if len(base_parts) > 1:
                                    suffix = base_parts[1] # e.g. /01_Scan/EXR
                                    new_full = os.path.join(found_reel_path, child) + suffix
                                    # Normalize slashes
                                    new_full = new_full.replace("/", os.sep).replace("\\", os.sep)
                                    if os.path.exists(new_full):
                                        full_path = new_full
                                        break
            except OSError as e:
                logging.error(f"ProjectManager: Smart path fallback skipped due to filesystem error: {e}")
        
        # 4. Smart Path Detection (Fix for Scan/EXR)
        if department.lower() == 'scan':
            # Check if specific EXR folder exists
            exr_path = os.path.join(full_path, "EXR")
            if os.path.exists(exr_path):
                return exr_path
                
        # If the cleaned path doesn't exist, maybe try the original raw names?
        # But usually folder structures are strict. 
        # Let's return the cleaned path as the primary attempt.
        
        return full_path
    
    def open_folder(self, path: str) -> bool:
        if not path:
            return False
            
        # SECURITY CHECK
        valid, msg = SecurityValidator.validate_directory_path(Path(path), must_exist=True)
        if not valid:
            logging.info(f"Security blocked open_folder: {msg}")
            return False

        try:
            # Use 'explorer' securely? subprocess.Popen with shell=True is risky if path has quotes
            # SecurityValidator checks for restricted chars, but let's be safe.
            # Using os.startfile is safer on Windows
            os.startfile(path)
            return True
        except Exception as e:
            logging.exception(f"Error opening folder: {e}")
            return False
    
    def save_config(self) -> bool:
        """Projects live in the database only; nothing is written to the install folder."""
        return True

    def get_column_index(self, code: str, field_name: str) -> int:
        project = self.get_project(code)
        if not project:
            return -1
        
        col_letter = project.column_mapping.get(field_name, "")
        if not col_letter:
            return -1
        
        return self._letter_to_index(col_letter)
    
    def _letter_to_index(self, letter: str) -> int:
        result = 0
        for char in letter.upper():
            result = result * 26 + (ord(char) - ord("A") + 1)
        return result - 1
