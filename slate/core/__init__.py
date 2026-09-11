"""Core modules for Slate Production tool.

Expose a facade without importing heavy submodules at package import time.
"""

from importlib import import_module
from typing import Any

_LAZY_IMPORTS = {
    # Infrastructure
    "ConfigManager": ("slate.core.infra.config_manager", "ConfigManager"),
    "DatabaseManager": ("slate.core.infra.database_manager", "DatabaseManager"),
    "SafeFileOperations": ("slate.core.infra.file_operations", "SafeFileOperations"),
    "setup_logging": ("slate.core.infra.logging_config", "setup_logging"),
    "PerformanceConfig": ("slate.core.infra.performance_config", "PerformanceConfig"),
    "PerformanceMonitor": ("slate.core.infra.performance_monitor", "PerformanceMonitor"),
    # Domain
    "SmartMetadataManager": ("slate.core.domain.metadata_engine", "SmartMetadataManager"),
    "ProxyManager": ("slate.core.domain.proxy_manager", "ProxyManager"),
    "UserManager": ("slate.core.domain.user_manager", "UserManager"),
    # Worker facade
    "FolderCreationWorker": ("slate.core.worker_threads", "FolderCreationWorker"),
    "MoveScanWorker": ("slate.core.worker_threads", "MoveScanWorker"),
    "ShotSubfoldersWorker": ("slate.core.worker_threads", "ShotSubfoldersWorker"),
    "ReportWorker": ("slate.core.worker_threads", "ReportWorker"),
}

__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'slate.core' has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
