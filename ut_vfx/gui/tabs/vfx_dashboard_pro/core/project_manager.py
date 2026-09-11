import logging
import json
import os
from copy import deepcopy
from typing import List, Optional, Dict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from ut_vfx.core.infra.consistency_protocol import CrossStoreConsistencyProtocol, StoreAction
from ut_vfx.utils.security import SecurityValidator
from ut_vfx.utils.safe_json import SafeJsonIO

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
    from ut_vfx.core.domain.departments import load_departments

    template = {
        "scan": "05_Reels/{reel}/{shot}/01_Scan",
        "deliver": "05_Reels/{reel}/{shot}/08_Deliver",
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
    from ut_vfx.core.infra.global_config import GlobalConfig
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

    from ut_vfx.core.domain.departments import load_departments

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

    for dept in load_departments():
        key = dept.key
        # A department counts as mapped if it already has an artist or status
        # column under any of the historical spellings.
        aliases = {
            "artist": [f"{key}_artist"],
            "status": [f"{key}_status", f"{key}_required", f"{key}_comp"],
            "bid": [f"{key}_bid", f"{key}_mandays"],
            "eta": [f"{key}_eta", f"{key}_target"],
        }
        for field, names in aliases.items():
            if any(name in mapping for name in names):
                continue
            mapping[names[0]] = claim()

    return mapping


class ProjectManager:
    def __init__(self):
        self.config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        self.config_path = os.path.join(self.config_dir, "projects.json")
        self.consistency = CrossStoreConsistencyProtocol(scope="project_manager")
        self.projects: Dict[str, ProjectConfig] = {}
        self.extended_sheets = {}
        self.shot_types = []
        self.priority_levels = []
        self.departments = []
        self.feedback_sources = []
        self.default_project = None
        self.load_config()
        
    def load_config(self):
        # HYBRID LOAD: Try DB first, fall back to JSON
        # This allows seamless transition.
        from ut_vfx.core.infra.database_manager import database_manager
        
        db_projects = database_manager.get_all_tracking_projects()
        if db_projects:
            logging.info(f"ProjectManager: Loaded {len(db_projects)} projects from DB.")
            for p_data in db_projects:
                try:
                    # Convert dict to ProjectConfig
                    # p_data is the dict from JSON
                    # We can use dacite or just simple constructor
                    
                    # Ensure defaults for missing fields
                    p_code = p_data.get('code')
                    if p_code:
                         # Filter out unknown fields if config changed
                        valid_fields = ProjectConfig.__dataclass_fields__.keys()
                        filtered = {k: v for k, v in p_data.items() if k in valid_fields}
                        self.projects[p_code] = ProjectConfig(**filtered)
                except Exception as e:
                    logging.exception(f"Error loading project from DB: {e}")

            # Also load global settings if stored in DB (Not yet, still in JSON for now)
            # For now, we still read the JSON for the "shared" lists (departments, etc)
            # In "Config Consolidation" phase we will move these too.
            self._load_json_aux_data()
            return

        # Fallback to JSON
        if not os.path.exists(self.config_path):
            self._create_default_config()
            return
            
        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
                
            # Load projects
            for code, proj_data in data.get("projects", {}).items():
                self.projects[code] = ProjectConfig(
                    code=code,
                    name=proj_data.get("name", code),
                    project_number=proj_data.get("project_number", 0),
                    excel_path=proj_data.get("excel_path", ""),
                    local_excel_path=proj_data.get("local_excel_path", ""),
                    sheet_name=proj_data.get("sheet_name", "MASTER"),
                    header_row=proj_data.get("header_row", 2),
                    data_start_row=proj_data.get("data_start_row", 3),
                    folder_base=proj_data.get("folder_base", ""),
                    folder_template=proj_data.get("folder_template", {}),
                    column_mapping=proj_data.get("column_mapping", {}),
                    status=proj_data.get("status", "active")
                )
            
            self._populate_aux_data(data)
            
        except Exception as e:
            logging.exception(f"Error loading config: {e}")
            self._create_default_config()

    def _load_json_aux_data(self):
        """Helper to load shared lists from JSON even if projects came from DB."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    data = json.load(f)
                self._populate_aux_data(data)
            except (OSError, json.JSONDecodeError, TypeError) as e:
                logging.error(f"ProjectManager: Failed to load auxiliary JSON config: {e}")

    def _populate_aux_data(self, data):
        self.extended_sheets = data.get("extended_sheets", {})
        self.shot_types = data.get("shot_types", ["Prep", "2D Comp", "2.5D Comp", "CG Comp", "AI Shot"])
        self.priority_levels = data.get("priority_levels", [0, 1, 2, 3])
        self.departments = data.get("departments", ["Comp", "Roto", "Prep", "DMP", "CG", "MGFX"])
        self.feedback_sources = data.get("feedback_sources", ["Client", "Director", "Internal", "UT_VFX"])
        self.default_project = data.get("default_project", "AK74")
    
    def _create_default_config(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        ak74_path = os.path.join(base_dir, "AK74_DELIVERY_SHEET.xlsx")
        
        self.projects["AK74"] = ProjectConfig(
            code="AK74",
            name="P038_AK74",
            project_number=38,
            excel_path=ak74_path,
            local_excel_path="AK74_DELIVERY_SHEET.xlsx",
            sheet_name="MASTER",
            header_row=2,
            data_start_row=3
        )
        self.default_project = "AK74"
    
    def get_all_projects(self) -> List[ProjectConfig]:
        return sorted(self.projects.values(), key=lambda p: p.project_number)
    
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
        
        # Try server path first
        if project.excel_path and os.path.exists(project.excel_path):
            return project.excel_path
        
        # Fall back to local path
        if project.local_excel_path:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            local_path = os.path.join(base_dir, project.local_excel_path)
            if os.path.exists(local_path):
                return local_path

        return project.excel_path

    @staticmethod
    def _save_project_to_db(project: ProjectConfig) -> None:
        from ut_vfx.core.infra.database_manager import database_manager

        config_json = json.dumps(asdict(project))
        save_ok = database_manager.save_tracking_project(project.code, project.name, config_json)
        if save_ok is False:
            raise RuntimeError(f"Failed to save project {project.code} to DB")

    @staticmethod
    def _delete_project_from_db(code: str) -> None:
        from ut_vfx.core.infra.database_manager import database_manager

        deleted = database_manager.delete_tracking_project(code)
        if deleted is False:
            raise RuntimeError(f"Failed to delete project {code} from DB")
    
    def add_project(self, code: str, name: str, excel_path: str, folder_base: str, 
                   sheet_name: str = "MASTER", header_row: int = 2, data_start_row: int = 3,
                   column_mapping: dict = None):
        """Creates a new project configuration and saves it."""
        # Determine next project number
        next_num = 1
        if self.projects:
            next_num = max(p.project_number for p in self.projects.values()) + 1
            
        default_mapping = default_column_mapping()

        if column_mapping is None:
            column_mapping = default_mapping

        # SECURITY VALIDATION
        excel_valid, _, excel_err = SecurityValidator.validate_file_path(excel_path)
        if not excel_valid:
            logging.warning(f"Security Warning: Invalid Excel Path: {excel_err}")
            # We might still proceed if it's a new file to be created, but basic safety needed
        
        folder_valid, folder_err = SecurityValidator.validate_directory_path(Path(folder_base), must_exist=False)
        if not folder_valid:
             logging.warning(f"Security Warning: Invalid Folder Base: {folder_err}")

        new_project = ProjectConfig(
            code=code,
            name=name,
            project_number=next_num,
            excel_path=str(excel_path), # Normalize
            folder_base=str(folder_base),
            sheet_name=sheet_name,
            header_row=header_row,
            data_start_row=data_start_row,
            column_mapping=column_mapping or default_mapping,
            # Folder paths come from the department registry, so they always
            # match the folders Build & Ingest actually creates.
            folder_template=_default_folder_template()
        )
        projects_before = deepcopy(self.projects)
        existing_project = deepcopy(self.projects.get(code)) if code in self.projects else None
        self.projects[code] = new_project

        def apply_db() -> None:
            self._save_project_to_db(new_project)

        def rollback_db() -> None:
            if existing_project is not None:
                self._save_project_to_db(existing_project)
            else:
                self._delete_project_from_db(code)

        def apply_json() -> None:
            if not self.save_config():
                raise RuntimeError("Failed writing projects.json")

        def rollback_json() -> None:
            self.projects = deepcopy(projects_before)
            if not self.save_config():
                raise RuntimeError("Failed rolling back projects.json")

        result = self.consistency.execute(
            operation="project.add",
            actions=[
                StoreAction("tracking_projects_db", apply_db, rollback_db),
                StoreAction("projects_json", apply_json, rollback_json),
            ],
            metadata={"code": code, "name": name},
        )
        if not result.success:
            self.projects = deepcopy(projects_before)
            self.save_config()
            logging.info(
                f"ProjectManager: Cross-store add failed ({result.failed_store}): {result.error}"
            )
            return None

        return new_project
        
    def update_project(self, code: str, name: str, excel_path: str, folder_base: str, 
                      sheet_name: str = None, header_row: int = None, data_start_row: int = None):
        """Updates an existing project configuration."""
        if code not in self.projects:
            return False

        projects_before = deepcopy(self.projects)
        previous_project = deepcopy(self.projects[code])
        project = self.projects[code]
        project.name = name
        project.excel_path = str(excel_path)
        project.folder_base = str(folder_base)
        
        # New Feature: Optional Sheet/Row Updates
        if sheet_name:
            project.sheet_name = sheet_name
        if header_row is not None:
            project.header_row = int(header_row)
        if data_start_row is not None:
            project.data_start_row = int(data_start_row)

        def apply_db() -> None:
            self._save_project_to_db(project)

        def rollback_db() -> None:
            self._save_project_to_db(previous_project)

        def apply_json() -> None:
            if not self.save_config():
                raise RuntimeError("Failed writing projects.json")

        def rollback_json() -> None:
            self.projects = deepcopy(projects_before)
            if not self.save_config():
                raise RuntimeError("Failed rolling back projects.json")

        result = self.consistency.execute(
            operation="project.update",
            actions=[
                StoreAction("tracking_projects_db", apply_db, rollback_db),
                StoreAction("projects_json", apply_json, rollback_json),
            ],
            metadata={"code": code, "name": name},
        )
        if not result.success:
            self.projects = deepcopy(projects_before)
            self.save_config()
            logging.info(
                f"ProjectManager: Cross-store update failed ({result.failed_store}): {result.error}"
            )
            return False

        return True

    def delete_project(self, code: str) -> bool:
        """Deletes a project from config and database."""
        if code not in self.projects:
            return False
        
        projects_before = deepcopy(self.projects)
        deleted_project = deepcopy(self.projects[code])
        del self.projects[code]

        def apply_db() -> None:
            self._delete_project_from_db(code)

        def rollback_db() -> None:
            self._save_project_to_db(deleted_project)

        def apply_json() -> None:
            if not self.save_config():
                raise RuntimeError("Failed writing projects.json")

        def rollback_json() -> None:
            self.projects = deepcopy(projects_before)
            if not self.save_config():
                raise RuntimeError("Failed rolling back projects.json")

        result = self.consistency.execute(
            operation="project.delete",
            actions=[
                StoreAction("tracking_projects_db", apply_db, rollback_db),
                StoreAction("projects_json", apply_json, rollback_json),
            ],
            metadata={"code": code},
        )
        if not result.success:
            self.projects = deepcopy(projects_before)
            self.save_config()
            logging.info(
                f"ProjectManager: Cross-store delete failed ({result.failed_store}): {result.error}"
            )
            return False

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
        data = {
            "projects": {
                code: {
                    "name": p.name,
                    "project_number": p.project_number,
                    "excel_path": p.excel_path,
                    "local_excel_path": p.local_excel_path,
                    "sheet_name": p.sheet_name,
                    "header_row": p.header_row,
                    "data_start_row": p.data_start_row,
                    "folder_base": p.folder_base,
                    "folder_template": p.folder_template,
                    "column_mapping": p.column_mapping,
                    "status": p.status
                } for code, p in self.projects.items()
            },
            "extended_sheets": self.extended_sheets,
            "shot_types": self.shot_types,
            "priority_levels": self.priority_levels,
            "departments": self.departments,
            "feedback_sources": self.feedback_sources,
            "default_project": self.default_project
        }
        try:
            return SafeJsonIO.save_json(Path(self.config_path), data, indent=4)
        except Exception as e:
            logging.exception(f"Error saving config: {e}")
            return False

    def set_project_folder_base(self, code: str, path: str):
        project = self.get_project(code)
        if project:
            project.folder_base = str(path)
            # Ensure templates exist if empty
            if not project.folder_template:
                project.folder_template = {
                    "scan": "05_Reels/{reel}/{shot}/01_Scan",
                    "roto": "05_Reels/{reel}/{shot}/02_Roto",
                    "prep": "05_Reels/{reel}/{shot}/03_Prep",
                    "comp": "05_Reels/{reel}/{shot}/04_Comp",
                    "dmp":  "05_Reels/{reel}/{shot}/05_DMP",
                    "output": "05_Reels/{reel}/{shot}/09_Output"
                }
            self.save_config()

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
