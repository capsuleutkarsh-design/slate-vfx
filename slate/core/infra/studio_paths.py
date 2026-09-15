"""
Where this studio keeps things, asked once instead of guessed everywhere.

Several places needed a root to look under and, having none, fell back to a
literal: Path("X:/"). That is one studio's drive letter, and on every other
studio it is a path that does not exist - so the guess never finds anything and
the code that depends on it fails in a way that looks like missing data rather
than a missing setting.

A guess that can only be right for one customer is worse than no guess. These
return None when the studio has not said, and the caller adds no candidate at
all, which is both honest and the same amount of work.

Nothing here caches. Paths change when somebody edits Settings, and a cached
root is how a corrected path fails to take effect until a restart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Read before any settings file. The older spelling is still honoured because
# it appears in studio launch scripts that nobody here can edit.
PROJECTS_ROOT_VARS = ("SLATE_PROJECTS_ROOT", "Slate_PROJECTS_ROOT")
STUDIO_ROOT_VARS = ("SLATE_STUDIO_ROOT",)


def _first_env(names) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _global_setting(key: str) -> str:
    try:
        from slate.core.infra.global_config import GlobalConfig
        return str(GlobalConfig.get(key, "") or "").strip()
    except Exception as exc:
        logger.debug("Could not read %s from the global config: %s", key, exc)
        return ""


def _machine_setting(key: str) -> str:
    try:
        from slate.core.infra.config_manager import ConfigManager
        return str(ConfigManager().settings.get(key, "") or "").strip()
    except Exception as exc:
        logger.debug("Could not read %s from the machine settings: %s", key, exc)
        return ""


def studio_root() -> Optional[Path]:
    """The studio's shared folder, or None if nobody has said where it is."""
    value = _first_env(STUDIO_ROOT_VARS) or _global_setting("SERVER_ROOT")
    return Path(value) if value else None


def projects_root() -> Optional[Path]:
    """
    Where this studio's projects live, or None.

    None is a real answer and callers must handle it. The alternative - a
    default drive letter - is a guess that is wrong everywhere except the one
    studio it was written for.
    """
    value = (_first_env(PROJECTS_ROOT_VARS)
             or _global_setting("PROJECTS_ROOT")
             or _machine_setting("last_project_dir"))
    if value:
        return Path(value)

    root = studio_root()
    if root:
        return root / "Projects"
    return None


def project_folder(code: str) -> Optional[Path]:
    """
    The folder a project code would be in, or None when there is no root.

    This replaces Path("X:/") / code, which was reached whenever nothing else
    matched - so on any studio without an X: drive, the last resort was a path
    that could never exist.
    """
    name = str(code or "").strip()
    if not name:
        return None
    root = projects_root()
    return (root / name) if root else None
