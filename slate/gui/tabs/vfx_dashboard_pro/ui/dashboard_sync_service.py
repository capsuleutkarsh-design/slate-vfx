import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any, Sequence

from slate.core.infra.database_manager import database_manager

from slate.core.domain.departments import department_keys

from ..core.excel_handler import ExcelHandler


class DashboardSyncService:
    """Synchronization helpers extracted from DashboardWidget."""

    def __init__(self, project_manager):
        self.project_manager = project_manager
        # Backup health: Excel is the studio's failsafe, so its state has to be
        # visible rather than assumed.
        self.last_backup_at = None
        self.last_backup_error = None

    @staticmethod
    def _is_local_fallback_mode() -> bool:
        try:
            status = database_manager.get_runtime_status() or {}
            active_mode = str(status.get("active_mode", "")).lower()
            return active_mode == "sqlite" and bool(status.get("fallback_used", False))
        except Exception:
            return False

    def mirror_shots_to_excel(
        self,
        shots: Sequence[Any],
        current_project: Any,
        data_handler: Any,
        force: bool = False,
    ):
        """
        Write edited shots back to project Excel when DB mode is active.
        By default, the SQL database is the Single Source of Truth (SSOT).
        Mirroring to Excel only occurs if 'auto_mirror_excel' is enabled in config or force=True.
        Returns (success, last_excel_mtime_or_none).
        """
        if not shots or not current_project:
            return False, None
        if isinstance(data_handler, ExcelHandler):
            return True, None

        if not force:
            from slate.core.infra.global_config import GlobalConfig
            # Excel is the studio's backup copy, so mirroring is on unless it
            # has been deliberately switched off.
            if not GlobalConfig.get("auto_mirror_excel", True):
                return True, None

        # A project with no passbook yet gets one, in the central folder. This
        # also fills in a column mapping for a project that has none - an
        # ingested project - without which a sheet has nowhere to put anything.
        excel_path = self.project_manager.ensure_excel_path(current_project.code)
        if not excel_path:
            self.last_backup_error = "This project has nowhere to keep its passbook."
            return False, None

        try:
            excel_handler = ExcelHandler(excel_path, current_project)
            if not os.path.exists(excel_path) and not excel_handler.create_workbook():
                self.last_backup_error = (
                    f"The passbook could not be created at {excel_path}. "
                    "Check the central server folder is reachable."
                )
                return False, None
            success = bool(excel_handler.write_shots(shots))
            if success:
                self.last_backup_error = None
                self.last_backup_at = datetime.now()
                return True, os.path.getmtime(excel_path)
            self.last_backup_error = (
                "The Excel file could not be written. It is usually open in "
                "Excel on someone's machine, or the share is unavailable."
            )
            return False, None
        except Exception as exc:
            logging.exception("Excel mirror save failed: %s", exc)
            return False, None
