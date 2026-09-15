"""Slate Production Tool package.

Keep top-level imports lightweight so non-GUI tools (like messenger server)
can import infra/domain modules without triggering GUI side effects.
"""

from importlib import import_module
from typing import Any

__version__ = "BETA 2.0.27"
__author__ = "Utkarsh Tripathi <slateutkarsh@gmail.com>"

_LAZY_IMPORTS = {
    "VFXFolderCreatorApp": ("slate.gui.main_window", "VFXFolderCreatorApp"),
    "ConfigManager": ("slate.core", "ConfigManager"),
    "SafeFileOperations": ("slate.core", "SafeFileOperations"),
    "FolderCreationWorker": ("slate.core.worker_threads", "FolderCreationWorker"),
    "MoveScanWorker": ("slate.core.worker_threads", "MoveScanWorker"),
    "ShotSubfoldersWorker": ("slate.core.worker_threads", "ShotSubfoldersWorker"),
}

__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'slate' has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
