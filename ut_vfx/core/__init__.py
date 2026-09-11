"""Core modules for UT_VFX Production tool.

Expose a facade without importing heavy submodules at package import time.
"""

from importlib import import_module
from typing import Any

_LAZY_IMPORTS = {
    # Infrastructure
    "ConfigManager": ("ut_vfx.core.infra.config_manager", "ConfigManager"),
    "DatabaseManager": ("ut_vfx.core.infra.database_manager", "DatabaseManager"),
    "SafeFileOperations": ("ut_vfx.core.infra.file_operations", "SafeFileOperations"),
    "setup_logging": ("ut_vfx.core.infra.logging_config", "setup_logging"),
    "PerformanceConfig": ("ut_vfx.core.infra.performance_config", "PerformanceConfig"),
    "PerformanceMonitor": ("ut_vfx.core.infra.performance_monitor", "PerformanceMonitor"),
    # Domain
    "SmartMetadataManager": ("ut_vfx.core.domain.metadata_engine", "SmartMetadataManager"),
    "ProxyManager": ("ut_vfx.core.domain.proxy_manager", "ProxyManager"),
    "UserManager": ("ut_vfx.core.domain.user_manager", "UserManager"),
    # Worker facade
    "FolderCreationWorker": ("ut_vfx.core.worker_threads", "FolderCreationWorker"),
    "MoveScanWorker": ("ut_vfx.core.worker_threads", "MoveScanWorker"),
    "ShotSubfoldersWorker": ("ut_vfx.core.worker_threads", "ShotSubfoldersWorker"),
    "ReportWorker": ("ut_vfx.core.worker_threads", "ReportWorker"),
}

__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'ut_vfx.core' has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
