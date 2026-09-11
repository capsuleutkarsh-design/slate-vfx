"""UT_VFX Production Tool package.

Keep top-level imports lightweight so non-GUI tools (like messenger server)
can import infra/domain modules without triggering GUI side effects.
"""

from importlib import import_module
from typing import Any

__version__ = "BETA 2.0.22"
__author__ = "Utkarsh Tripathi <capsuleutkarsh@gmail.com>"

_LAZY_IMPORTS = {
    "VFXFolderCreatorApp": ("ut_vfx.gui.main_window", "VFXFolderCreatorApp"),
    "ConfigManager": ("ut_vfx.core", "ConfigManager"),
    "SafeFileOperations": ("ut_vfx.core", "SafeFileOperations"),
    "FolderCreationWorker": ("ut_vfx.core.worker_threads", "FolderCreationWorker"),
    "MoveScanWorker": ("ut_vfx.core.worker_threads", "MoveScanWorker"),
    "ShotSubfoldersWorker": ("ut_vfx.core.worker_threads", "ShotSubfoldersWorker"),
}

__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'ut_vfx' has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
