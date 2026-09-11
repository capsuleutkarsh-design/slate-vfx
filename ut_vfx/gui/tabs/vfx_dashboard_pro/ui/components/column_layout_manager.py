"""
Column layout persistence for VFX Dashboard table.

Preserves column visibility, widths, and sort order per user per project.
Supervisors can publish a layout as the project default so new team members
start with a sensible view.

Uses key-based serialization rather than index-based serialization so that
adding departments to departments.json never corrupts saved layouts.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QTableView


SETTINGS_ORGANIZATION = "UT_Software"
SETTINGS_APPLICATION = "UT_VFX"


class ColumnLayoutManager:
    """Manages serialization and restoration of dashboard table column layouts."""

    def __init__(self, table: QTableView, model=None, user_id: str = "", project_code: str = "", db_manager=None):
        self.table = table
        self.model = model or getattr(table, "model", lambda: None)()
        self.user_id = str(user_id or "default").strip()
        self.project_code = str(project_code or "").strip()
        self.db_manager = db_manager

    def set_context(self, user_id: Optional[str] = None, project_code: Optional[str] = None, model=None):
        if user_id is not None:
            self.user_id = str(user_id or "default").strip()
        if project_code is not None:
            self.project_code = str(project_code or "").strip()
        if model is not None:
            self.model = model

    # --- SERIALIZATION ---

    def capture_layout(self) -> Dict[str, Any]:
        """Capture the current column visibility, width, and sort order."""
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return {}

        columns_state = {}
        for col_idx, (col_key, label, _) in enumerate(self.model.COLUMNS):
            is_hidden = self.table.isColumnHidden(col_idx)
            width = self.table.columnWidth(col_idx)
            columns_state[col_key] = {
                "visible": not is_hidden,
                "width": max(30, int(width)),
                "label": label,
            }

        header = self.table.horizontalHeader()
        sort_col_idx = header.sortIndicatorSection() if header else -1
        sort_order = header.sortIndicatorOrder().value if header else 0

        sort_col_key = None
        if 0 <= sort_col_idx < len(self.model.COLUMNS):
            sort_col_key = self.model.COLUMNS[sort_col_idx][0]

        return {
            "version": 1,
            "columns": columns_state,
            "sort": {
                "column_key": sort_col_key,
                "order": sort_order,
            },
        }

    def apply_layout(self, layout: Dict[str, Any]) -> bool:
        """Apply saved layout to the table by matching stable column keys."""
        if not layout or not isinstance(layout, dict):
            return False
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return False

        cols_data = layout.get("columns", {})
        if not isinstance(cols_data, dict):
            return False

        header = self.table.horizontalHeader()
        for col_idx, (col_key, _, _) in enumerate(self.model.COLUMNS):
            if col_key in cols_data:
                info = cols_data[col_key]
                if isinstance(info, dict):
                    visible = bool(info.get("visible", True))
                    width = info.get("width")
                    self.table.setColumnHidden(col_idx, not visible)
                    if width and isinstance(width, (int, float)) and width >= 30:
                        self.table.setColumnWidth(col_idx, int(width))

        # Restore sort order if present
        sort_data = layout.get("sort")
        if isinstance(sort_data, dict) and header:
            sort_key = sort_data.get("column_key")
            order_val = sort_data.get("order", 0)
            if sort_key:
                for col_idx, (col_key, _, _) in enumerate(self.model.COLUMNS):
                    if col_key == sort_key:
                        sort_order = Qt.SortOrder(order_val) if order_val in (0, 1) else Qt.SortOrder.AscendingOrder
                        self.table.sortByColumn(col_idx, sort_order)
                        break

        return True

    # --- USER SETTINGS PERSISTENCE (QSettings) ---

    def _user_settings_key(self, user_id: str, project_code: str) -> str:
        safe_user = str(user_id or "default").replace("/", "_")
        safe_proj = str(project_code or "global").replace("/", "_")
        return f"dashboard_layouts/{safe_user}/{safe_proj}"

    def save_user_layout(self, user_id: Optional[str] = None, project_code: Optional[str] = None) -> bool:
        """Save the current layout to QSettings for this user and project."""
        uid = user_id or self.user_id
        code = project_code or self.project_code
        if not code:
            return False

        layout = self.capture_layout()
        if not layout:
            return False

        try:
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            key = self._user_settings_key(uid, code)
            settings.setValue(key, json.dumps(layout))
            return True
        except Exception as exc:
            logging.warning("Could not save column layout for %s/%s: %s", uid, code, exc)
            return False

    def load_user_layout(self, user_id: Optional[str] = None, project_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Load the user's saved layout for this project."""
        uid = user_id or self.user_id
        code = project_code or self.project_code
        if not code:
            return None

        try:
            settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
            key = self._user_settings_key(uid, code)
            raw = settings.value(key)
            if raw and isinstance(raw, str):
                return json.loads(raw)
        except Exception as exc:
            logging.debug("Could not load user layout for %s/%s: %s", uid, code, exc)
        return None

    # --- PROJECT DEFAULT PERSISTENCE (Database) ---

    def save_project_default(self, project_code: Optional[str] = None, db_manager=None) -> bool:
        """Supervisor action: publish current layout as the project default."""
        code = project_code or self.project_code
        db = db_manager or self.db_manager
        if not code or db is None:
            return False

        layout = self.capture_layout()
        if not layout:
            return False

        try:
            proj_data = db.get_tracking_project(code)
            config = {}
            if isinstance(proj_data, dict):
                config = dict(proj_data)
            config["default_columns"] = layout
            name = config.get("name", code)
            db.save_tracking_project(code, name, json.dumps(config))
            logging.info("Saved project default column layout for %s", code)
            return True
        except Exception as exc:
            logging.error("Could not save project default column layout for %s: %s", code, exc)
            return False

    def load_project_default(self, project_code: Optional[str] = None, db_manager=None) -> Optional[Dict[str, Any]]:
        """Load the project default layout published by a supervisor."""
        code = project_code or self.project_code
        db = db_manager or self.db_manager
        if not code or db is None:
            return None

        try:
            proj_data = db.get_tracking_project(code)
            if isinstance(proj_data, dict):
                default_cols = proj_data.get("default_columns")
                if isinstance(default_cols, dict):
                    return default_cols
        except Exception as exc:
            logging.debug("Could not read project default columns for %s: %s", code, exc)
        return None

    # --- ORCHESTRATION ---

    def restore_layout(self, user_id: Optional[str] = None, project_code: Optional[str] = None, db_manager=None) -> bool:
        """
        Restore layout with resolution hierarchy:
        1. User's personal saved layout for this project
        2. Project default layout published by supervisor
        3. Built-in defaults (all visible)
        """
        uid = user_id or self.user_id
        code = project_code or self.project_code
        db = db_manager or self.db_manager

        # 1. Personal layout
        personal = self.load_user_layout(uid, code)
        if personal:
            return self.apply_layout(personal)

        # 2. Project default
        project_def = self.load_project_default(code, db)
        if project_def:
            return self.apply_layout(project_def)

        # 3. System default (show all)
        return self.reset_to_defaults()

    def reset_to_defaults(self) -> bool:
        """Reset all columns to visible with standard widths."""
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return False

        for i in range(len(self.model.COLUMNS)):
            self.table.showColumn(i)

        self.table.resizeColumnsToContents()
        return True
