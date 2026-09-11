from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

from ut_vfx.core.domain.departments import department_keys, load_departments

@dataclass
class DepartmentInfo:
    artist: str = ""
    bid_days: float = 0.0
    eta: Optional[str] = None
    status: str = ""
    wip_date: Optional[str] = None
    target: Optional[str] = None
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict):
        if not data:
            return cls()
        valid_fields = {"artist", "bid_days", "eta", "status", "wip_date", "target"}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass  
class ArtistLogEntry:
    shot_id: str = ""
    artist: str = ""
    department: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    notes: str = ""


@dataclass
class FeedbackEntry:
    date: str = ""
    source: str = ""
    text: str = ""
    logged_by: str = ""
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict):
        if not data:
            return cls()
        if isinstance(data, str):
            return cls(text=data)
        valid_fields = {"date", "source", "text", "logged_by"}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class Shot:
    id: int = -1 # Database ID
    shot_name: str = ""
    reel_episode: str = ""
    status: str = "WIP"
    edit_frames: float = 0.0
    # The plate's real first and last frame, recorded by the ingest. Kept apart
    # from edit_frames, which is a length somebody types in and is not always
    # the same thing as what the client actually delivered.
    first_frame: int = 0
    last_frame: int = 0
    scan_status: str = ""
    edit_status: str = ""
    in_os: str = ""
    shot_type: str = ""
    priority: int = 3
    is_hero: bool = False
    similar_to: List[str] = field(default_factory=list)
    sow: str = ""
    target: Optional[str] = None
    prev_version: str = ""
    curr_version: str = ""
    assigned_artist: str = ""
    artist_history: List[ArtistLogEntry] = field(default_factory=list)
    # Department work, keyed by department key ("comp", "roto", "matchmove"...).
    # The set of keys comes from ut_vfx/data/departments.json, not from code.
    # The legacy <key>_dept attributes below still work - they read and write
    # straight through to this dict.
    departments: Dict[str, DepartmentInfo] = field(default_factory=dict)
    feedback_internal: List[FeedbackEntry] = field(default_factory=list)
    feedback_client: List[FeedbackEntry] = field(default_factory=list)
    feedback_director: List[FeedbackEntry] = field(default_factory=list)
    wip_date: Optional[str] = None
    shot_done_date: Optional[str] = None
    submission_date: Optional[str] = None
    exr_submission: Optional[str] = None
    mov_submission: Optional[str] = None
    thumbnail_path: str = ""
    folder_paths: Dict[str, str] = field(default_factory=dict)
    description: str = ""
    notes: str = ""
    _row_idx: int = field(default=0, repr=False)
    _modified: bool = field(default=False, repr=False)
    _semantic_embedding: Optional[List[float]] = field(default=None, repr=False)
    version: int = 1 # Optimistic Locking
    
    def __post_init__(self):
        """Ensure every configured department has a slot."""
        if not isinstance(self.departments, dict):
            self.departments = {}
        for key in department_keys():
            entry = self.departments.get(key)
            if isinstance(entry, dict):
                self.departments[key] = DepartmentInfo.from_dict(entry)
            elif not isinstance(entry, DepartmentInfo):
                self.departments[key] = DepartmentInfo()

    @property
    def frame_count(self) -> int:
        """How many frames the delivered plate actually has."""
        if self.last_frame and self.last_frame >= self.first_frame:
            return self.last_frame - self.first_frame + 1
        return 0

    @property
    def frame_range_text(self) -> str:
        """The range as a person reads it, or blank when it is not known."""
        if not self.frame_count:
            return ""
        return f"{self.first_frame}-{self.last_frame}"

    def dept(self, key: str) -> DepartmentInfo:
        """
        Department work for `key`, created on demand.

        Use this for new code; it works for any department in
        departments.json, including ones added after this was written.
        """
        key = str(key or "").strip().lower()
        if key not in self.departments:
            self.departments[key] = DepartmentInfo()
        return self.departments[key]

    def __getattr__(self, name):
        # Legacy accessors: shot.comp_dept -> shot.departments["comp"]
        if name.endswith("_dept") and not name.startswith("_"):
            # __getattr__ only fires when normal lookup failed, so guard
            # against recursion during unpickling / early construction.
            departments = self.__dict__.get("departments")
            if departments is None:
                raise AttributeError(name)
            key = name[:-5]
            if key not in departments:
                departments[key] = DepartmentInfo()
            return departments[key]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __setattr__(self, name, value):
        if (name.endswith("_dept") and not name.startswith("_")
                and "departments" in self.__dict__):
            key = name[:-5]
            if isinstance(value, dict):
                value = DepartmentInfo.from_dict(value)
            self.__dict__["departments"][key] = value
            return
        object.__setattr__(self, name, value)

    def get_latest_feedback(self):
        all_feedback = self.feedback_internal + self.feedback_client + self.feedback_director
        if not all_feedback:
            return None
        sorted_fb = sorted([f for f in all_feedback if f.date], key=lambda x: x.date, reverse=True)
        return sorted_fb[0] if sorted_fb else all_feedback[0]
    
    def get_all_artists(self):
        artists = set()
        if self.assigned_artist:
            artists.add(self.assigned_artist)
        for e in self.artist_history:
            if e.artist:
                artists.add(e.artist)
        for d in self.departments.values():
            if getattr(d, "artist", ""):
                artists.add(d.artist)
        return list(artists)
    
    def to_dict(self):
        # Updated to_dict to include more fields and handle nested dataclasses
        return {
            "shot_name": self.shot_name, 
            "reel_episode": self.reel_episode, 
            "status": self.status,
            "edit_frames": self.edit_frames,
            "first_frame": self.first_frame,
            "last_frame": self.last_frame,
            "scan_status": self.scan_status,
            "edit_status": self.edit_status,
            "in_os": self.in_os,
            "shot_type": self.shot_type, 
            "priority": self.priority,
            "is_hero": self.is_hero, 
            "similar_to": self.similar_to,
            "sow": self.sow, 
            "target": self.target,
            "prev_version": self.prev_version,
            "curr_version": self.curr_version,
            "assigned_artist": self.assigned_artist,
            "artist_history": [entry.to_dict() for entry in self.artist_history],
            "departments": {
                key: info.to_dict() for key, info in self.departments.items()
            },
            "feedback_internal": [fb.to_dict() for fb in self.feedback_internal],
            "feedback_client": [fb.to_dict() for fb in self.feedback_client],
            "feedback_director": [fb.to_dict() for fb in self.feedback_director],
            "wip_date": self.wip_date,
            "shot_done_date": self.shot_done_date,
            "submission_date": self.submission_date,
            "exr_submission": self.exr_submission,
            "mov_submission": self.mov_submission,
            "thumbnail_path": self.thumbnail_path,
            "folder_paths": self.folder_paths,
            "description": self.description,
            "notes": self.notes,
            "version": self.version
        }
    
    def matches_filter(self, search_text="", status_filter="All", priority_filter="All"):
        if search_text:
            artists = " ".join(self.get_all_artists())
            searchable = f"{self.shot_name} {self.sow} {self.description} {artists} {self.reel_episode}".lower()
            if search_text.lower() not in searchable:
                return False
        if status_filter != "All" and self.status != status_filter:
            return False
        if priority_filter != "All":
            try:
                if int(priority_filter) != self.priority:
                    return False
            except ValueError:
                pass
        return True
    
    def get_folder_path(self, department, folder_template, folder_base):
        template = folder_template.get(department.lower(), "")
        if not template:
            return ""
        path = template.format(reel=self.reel_episode, shot=self.shot_name)
        return f"{folder_base}\\{path}" if folder_base else path

    @classmethod
    def from_dict(cls, data: dict):
        if not data:
            return cls()
        
                
        # Lists of objects
        list_fields = {
            "feedback_internal": FeedbackEntry,
            "feedback_client": FeedbackEntry,
            "feedback_director": FeedbackEntry,
            "artist_history": ArtistLogEntry
        }
        
        init_data = {}
        departments: Dict[str, DepartmentInfo] = {}

        # Shots written before departments were configurable stored one
        # "<key>_dept" object per department. Read either shape.
        raw_departments = data.get("departments")
        if isinstance(raw_departments, dict):
            for key, value in raw_departments.items():
                if isinstance(value, DepartmentInfo):
                    departments[str(key).lower()] = value
                elif isinstance(value, dict):
                    departments[str(key).lower()] = DepartmentInfo.from_dict(value)

        for k, v in data.items():
            if k.endswith("_dept") and k != "departments":
                key = k[:-5].lower()
                if key in departments:
                    continue      # the new-style entry wins
                if isinstance(v, DepartmentInfo):
                    departments[key] = v
                elif isinstance(v, dict):
                    departments[key] = DepartmentInfo.from_dict(v)

        for k, v in data.items():
            if k.endswith("_dept") or k == "departments":
                continue
            if k in list_fields:
                # v is list of dicts
                if isinstance(v, list):
                    item_cls = list_fields[k]
                    # Data classes might not all have from_dict, check recursively
                    # ArtistLogEntry doesn't have from_dict in the file I saw?
                    # Let's simple init for data classes
                    if hasattr(item_cls, 'from_dict'):
                        init_data[k] = [item_cls.from_dict(i) for i in v]
                    else:
                        init_data[k] = [item_cls(**i) for i in v if isinstance(i, dict)]
            else:
                init_data[k] = v
                
        # Filter out unknown keys to prevent TypeError on __init__.
        # Underscored fields are in-memory state (_modified, _row_idx). They
        # were once serialised into the database, so every shot ever edited
        # came back flagged "unsaved" for everybody, forever. They are never
        # restored from stored data.
        valid_keys = cls.__dataclass_fields__.keys()
        filtered_data = {k: v for k, v in init_data.items()
                         if k in valid_keys and not str(k).startswith("_")}
        filtered_data["departments"] = departments

        return cls(**filtered_data)


# ---------------------------------------------------------------------------
# Backwards compatibility for the constructor.
#
# Departments used to be seven separate dataclass fields, so a lot of code
# (and every Excel import path) builds shots as Shot(comp_dept=..., roto_dept=...).
# The dataclass no longer has those fields, so fold any "<key>_dept" keyword
# into the departments dict before the generated __init__ sees it.
# ---------------------------------------------------------------------------
_shot_dataclass_init = Shot.__init__


def _shot_init(self, *args, **kwargs):
    legacy = {}
    for name in [k for k in kwargs if k.endswith("_dept")]:
        legacy[name[:-5].lower()] = kwargs.pop(name)

    if legacy:
        merged = dict(kwargs.get("departments") or {})
        merged.update(legacy)
        kwargs["departments"] = merged

    _shot_dataclass_init(self, *args, **kwargs)


Shot.__init__ = _shot_init
