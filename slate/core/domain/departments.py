"""
Department registry.

The set of departments a shot can carry used to be hardcoded in seven places
(the Shot dataclass, the SQLite handler's mapping tuple, the table columns, the
Excel importer and the search filter). Adding a department
meant a code change, which is why matchmove, de-age and AI were untrackable
despite having folders in the project template.

The list now lives in data. Edit ``slate/data/departments.json`` to add,
remove or rename one; nothing else has to change.

Each entry has:
    key     stable identifier - stored in the database and in shot JSON.
            Never rename a key that already has data against it.
    label   short column heading shown in the dashboard table.
    name    full human name, used in tooltips and dialogs.
    folder  the shot subfolder this department works out of, matching the
            project template in ``templates.json``.
    family  departments that belong together (comp and slapcomp are both
            "comp"), used for grouping and roll-ups.
    order   display order, low to high.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


def _package_data_path(filename: str) -> Path:
    """
    Locate a file in slate/data, in a source tree or a frozen build.

    Mirrors how templates.json is found: importlib.resources first, falling
    back to a path relative to this module.
    """
    try:
        from importlib.resources import files
        candidate = files("slate.data").joinpath(filename)
        path = Path(str(candidate))
        if path.exists():
            return path
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "data" / filename

DEPARTMENTS_FILE = _package_data_path("departments.json")


@dataclass(frozen=True)
class Department:
    key: str
    label: str
    name: str
    folder: str = ""
    family: str = ""
    order: int = 0

    @property
    def json_key(self) -> str:
        """Key used inside a shot's stored JSON, e.g. 'comp' -> 'comp_dept'."""
        return f"{self.key}_dept"


# Built-in defaults, mirroring the standard project folder template.
# Used when departments.json is missing or unreadable.
_DEFAULTS: List[Dict] = [
    {"key": "dmp", "label": "DMP", "name": "Matte Painting",
     "folder": "02_Dmp", "family": "dmp", "order": 10},
    {"key": "cg", "label": "CG", "name": "CG",
     "folder": "03_Cg", "family": "cg", "order": 20},
    {"key": "roto", "label": "Roto", "name": "Roto",
     "folder": "04_Roto", "family": "roto", "order": 30},
    {"key": "prep", "label": "Prep", "name": "Prep / Paint",
     "folder": "05_Prep", "family": "prep", "order": 40},
    {"key": "matchmove", "label": "MMV", "name": "Matchmove",
     "folder": "06_Cmm", "family": "matchmove", "order": 50},
    {"key": "comp", "label": "Comp", "name": "Comp",
     "folder": "07_Comp", "family": "comp", "order": 60},
    {"key": "slapcomp", "label": "Slap", "name": "Slap Comp",
     "folder": "08_Output/SLAPCOMP", "family": "comp", "order": 70},
    {"key": "deage", "label": "Face", "name": "Face / De-age",
     "folder": "09_Deage", "family": "deage", "order": 80},
    {"key": "ai", "label": "AI", "name": "AI",
     "folder": "10_AI", "family": "ai", "order": 90},
    {"key": "mgfx", "label": "MGFX", "name": "Motion Graphics",
     "folder": "11_Mgfx", "family": "mgfx", "order": 100},
]


_cache: Optional[List[Department]] = None


def _coerce(raw: Dict, fallback_order: int) -> Optional[Department]:
    key = str(raw.get("key", "")).strip().lower()
    if not key:
        return None
    label = str(raw.get("label") or key.upper())
    return Department(
        key=key,
        label=label,
        name=str(raw.get("name") or label),
        folder=str(raw.get("folder") or ""),
        family=str(raw.get("family") or key).strip().lower(),
        order=int(raw.get("order", fallback_order)),
    )


def load_departments(force: bool = False) -> List[Department]:
    """Return every configured department, in display order."""
    global _cache
    if _cache is not None and not force:
        return _cache

    raw_list = _DEFAULTS
    try:
        if DEPARTMENTS_FILE.exists():
            with open(DEPARTMENTS_FILE, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            entries = loaded.get("departments") if isinstance(loaded, dict) else loaded
            if isinstance(entries, list) and entries:
                raw_list = entries
            else:
                logging.warning("departments.json has no usable entries; using defaults")
    except Exception as exc:
        logging.warning("Could not read departments.json (%s); using defaults", exc)

    departments: List[Department] = []
    seen = set()
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        dept = _coerce(raw, fallback_order=index * 10)
        if dept is None or dept.key in seen:
            continue
        seen.add(dept.key)
        departments.append(dept)

    if not departments:   # a malformed file must never leave us with nothing
        departments = [_coerce(raw, i * 10) for i, raw in enumerate(_DEFAULTS)]

    departments.sort(key=lambda d: (d.order, d.key))
    _cache = departments
    return _cache


def department_keys() -> List[str]:
    return [d.key for d in load_departments()]


def get_department(key: str) -> Optional[Department]:
    wanted = str(key or "").strip().lower()
    for dept in load_departments():
        if dept.key == wanted:
            return dept
    return None


def families() -> Dict[str, List[Department]]:
    """Departments grouped by family, e.g. {'comp': [comp, slapcomp]}."""
    grouped: Dict[str, List[Department]] = {}
    for dept in load_departments():
        grouped.setdefault(dept.family, []).append(dept)
    return grouped


def reset_cache() -> None:
    """Drop the cached list so the next read picks up an edited file."""
    global _cache
    _cache = None


# Functions that are not shot-work departments but that people still belong to.
NON_PRODUCTION_DEPARTMENTS = [
    "Production", "IT", "HR", "Admin", "Editorial", "General",
]


def staff_department_names() -> List[str]:
    """
    Departments to offer when assigning someone's designation.

    Production departments come from departments.json so the list a person can
    be assigned to always matches the columns they can be assigned work in.
    """
    names = [dept.name for dept in load_departments()]
    return names + list(NON_PRODUCTION_DEPARTMENTS)
