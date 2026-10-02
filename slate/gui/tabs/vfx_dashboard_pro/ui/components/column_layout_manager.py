"""
Column layout persistence for VFX Dashboard table.

Preserves column visibility, widths, order and the sort per user per project.
Supervisors can publish a layout as the project default so new team members
start with a sensible view.

Uses key-based serialization rather than index-based serialization so that
adding departments to departments.json never corrupts saved layouts.

What changed:
- the layout is saved (half a second after the last change) whenever a column
  is resized, moved, hidden or the sort changes - widths used to be saved only
  when a column was hidden, so a widened SOW column snapped back on Refresh;
- restoring never shrinks columns to their contents; only a project opened
  for the first time, with no saved layout, gets the default widths;
- "Reset column layout" forgets the personal layout, so it stays reset;
- Reel and Shot Name are always visible and always first, so the grid can
  never become a header-less grey area.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QTableView


SETTINGS_ORGANIZATION = "UT_Software"
SETTINGS_APPLICATION = "Slate"

# Never hidden, never moved from the front.
PINNED_KEYS = ("reel", "shot_name")


def settings_factory() -> QSettings:
    """Where layouts are kept. Tests point this at a scratch file."""
    return QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)


class ColumnLayoutManager:
    """Manages serialization and restoration of dashboard table column layouts."""

    SAVE_DELAY_MS = 500

    def __init__(self, table: QTableView, model=None, user_id: str = "", project_code: str = "", db_manager=None):
        self.table = table
        self.model = model or getattr(table, "model", lambda: None)()
        self.user_id = str(user_id or "default").strip()
        self.project_code = str(project_code or "").strip()
        self.db_manager = db_manager
        self._restoring = False
        self._save_timer = None

    def set_context(self, user_id: Optional[str] = None, project_code: Optional[str] = None, model=None):
        if user_id is not None:
            self.user_id = str(user_id or "default").strip()
        if project_code is not None:
            self.project_code = str(project_code or "").strip()
        if model is not None:
            self.model = model

    # --- WATCHING ---

    def attach(self):
        """Save the layout shortly after any column change the person makes."""
        self._save_timer = QTimer(self.table)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_user_layout)
        header = self.table.horizontalHeader()
        header.sectionResized.connect(self._changed)
        header.sectionMoved.connect(self._changed)
        header.sortIndicatorChanged.connect(self._changed)

    def _changed(self, *args):
        if self._restoring or self._save_timer is None or not self.project_code:
            return
        self._save_timer.start()

    def flush(self):
        """Write a pending save now (before switching project or closing)."""
        if self._save_timer is not None and self._save_timer.isActive():
            self._save_timer.stop()
            self.save_user_layout()

    def _keys(self):
        return [spec[0] for spec in getattr(self.model, "COLUMNS", [])]

    def _index_of(self, key) -> int:
        for i, k in enumerate(self._keys()):
            if k == key:
                return i
        return -1

    # --- SERIALIZATION ---

    def capture_layout(self) -> Dict[str, Any]:
        """Capture the current column visibility, width, order and sort."""
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return {}

        header = self.table.horizontalHeader()
        columns_state = {}
        for col_idx, (col_key, label, _) in enumerate(self.model.COLUMNS):
            is_hidden = self.table.isColumnHidden(col_idx)
            width = self.table.columnWidth(col_idx)
            if is_hidden:
                previous = self._previous_width(col_key)
                width = previous or width
            columns_state[col_key] = {
                "visible": not is_hidden,
                "width": max(30, int(width or 0)),
                "label": label,
                "order": header.visualIndex(col_idx) if header else col_idx,
            }

        sort_col_idx = header.sortIndicatorSection() if header else -1
        sort_order = header.sortIndicatorOrder().value if header else 0

        sort_col_key = None
        if 0 <= sort_col_idx < len(self.model.COLUMNS):
            sort_col_key = self.model.COLUMNS[sort_col_idx][0]

        return {
            "version": 2,
            "columns": columns_state,
            "sort": {
                "column_key": sort_col_key,
                "order": sort_order,
            },
        }

    def _previous_width(self, key) -> int:
        """A hidden column's width is 0 to Qt; keep the one it had."""
        saved = self.load_user_layout() or {}
        info = (saved.get("columns") or {}).get(key) or {}
        try:
            return int(info.get("width") or 0)
        except (TypeError, ValueError):
            return 0

    def apply_layout(self, layout: Dict[str, Any]) -> bool:
        """Apply saved layout to the table by matching stable column keys."""
        if not layout or not isinstance(layout, dict):
            return False
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return False

        cols_data = layout.get("columns", {})
        if not isinstance(cols_data, dict):
            return False

        self._restoring = True
        try:
            header = self.table.horizontalHeader()
            for col_idx, (col_key, _, _) in enumerate(self.model.COLUMNS):
                info = cols_data.get(col_key)
                if not isinstance(info, dict):
                    continue
                visible = bool(info.get("visible", True)) or col_key in PINNED_KEYS
                width = info.get("width")
                self.table.setColumnHidden(col_idx, not visible)
                if width and isinstance(width, (int, float)) and width >= 30:
                    self.table.setColumnWidth(col_idx, int(width))

            # Column order, by key. Columns the layout does not know (a new
            # department) keep their place after the ones it does.
            ordered = sorted(
                (k for k in self._keys() if isinstance(cols_data.get(k), dict)
                 and isinstance(cols_data[k].get("order"), int)),
                key=lambda k: cols_data[k]["order"])
            ordered = [k for k in PINNED_KEYS if k in self._keys()] + \
                      [k for k in ordered if k not in PINNED_KEYS]
            if header is not None:
                for target, key in enumerate(ordered):
                    logical = self._index_of(key)
                    if logical < 0:
                        continue
                    current = header.visualIndex(logical)
                    if current != target:
                        header.moveSection(current, target)

            sort_data = layout.get("sort")
            if isinstance(sort_data, dict) and header:
                sort_key = sort_data.get("column_key")
                order_val = sort_data.get("order", 0)
                logical = self._index_of(sort_key) if sort_key else -1
                if logical >= 0:
                    sort_order = Qt.SortOrder(order_val) if order_val in (0, 1) else Qt.SortOrder.AscendingOrder
                    self.table.sortByColumn(logical, sort_order)
        finally:
            self._restoring = False
        self._after_change()
        return True

    def _after_change(self):
        sync = getattr(self.table, "sync_columns", None)
        if callable(sync):
            sync()

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
            settings = settings_factory()
            key = self._user_settings_key(uid, code)
            settings.setValue(key, json.dumps(layout))
            settings.sync()
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
            settings = settings_factory()
            key = self._user_settings_key(uid, code)
            raw = settings.value(key)
            if raw and isinstance(raw, str):
                return json.loads(raw)
        except Exception as exc:
            logging.debug("Could not load user layout for %s/%s: %s", uid, code, exc)
        return None

    def forget_user_layout(self, user_id: Optional[str] = None, project_code: Optional[str] = None):
        uid = user_id or self.user_id
        code = project_code or self.project_code
        if not code:
            return
        try:
            settings = settings_factory()
            settings.remove(self._user_settings_key(uid, code))
            settings.sync()
        except Exception as exc:
            logging.debug("Could not forget user layout for %s/%s: %s", uid, code, exc)

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
            result = db.save_tracking_project(code, name, json.dumps(config))
            ok = bool(getattr(result, "ok", result))
            if ok:
                logging.info("Saved project default column layout for %s", code)
            else:
                logging.error("Project default column layout for %s was refused: %s",
                              code, getattr(result, "error", ""))
            return ok
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
        3. Built-in defaults (all visible, sensible widths)
        """
        uid = user_id or self.user_id
        code = project_code or self.project_code
        db = db_manager or self.db_manager

        personal = self.load_user_layout(uid, code)
        if personal:
            return self.apply_layout(personal)

        project_def = self.load_project_default(code, db)
        if project_def:
            return self.apply_layout(project_def)

        return self.apply_builtin_layout()

    def apply_builtin_layout(self) -> bool:
        """Every column visible, in model order, at the default widths, sorted by reel."""
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return False
        self._restoring = True
        try:
            header = self.table.horizontalHeader()
            for i in range(len(self.model.COLUMNS)):
                self.table.setColumnHidden(i, False)
                if header is not None and header.visualIndex(i) != i:
                    header.moveSection(header.visualIndex(i), i)
            self.apply_default_widths()
            reel = self._index_of("reel")
            if reel >= 0:
                self.table.sortByColumn(reel, Qt.SortOrder.AscendingOrder)
        finally:
            self._restoring = False
        self._after_change()
        return True

    def apply_default_widths(self):
        """Widths that show each column's usual content without cutting it."""
        if not self.model or not hasattr(self.model, "COLUMNS"):
            return
        from slate.core.domain import shot_status
        from ..status_delegate import StatusDelegate
        font = self.table.font()
        # A status column fits its longest status pill ("SENT FOR REVIEW").
        status_width = max(StatusDelegate.pill_width_for(s, font) for s in shot_status.WORKFLOW)
        widths = {
            "reel": 80, "shot_name": 160, "status": status_width, "artist": 140,
            "sow": 260, "frames": 70, "plate_range": 100, "target": 110, "type": 110,
            "priority": 80, "version": 80, "in_os": 70, "scan": 110, "edit": 110,
        }
        department_keys = getattr(self.model, "_department_keys", set())
        was = self._restoring
        self._restoring = True
        try:
            for i, key in enumerate(self._keys()):
                if key in department_keys:
                    self.table.setColumnWidth(i, status_width)
                elif key in widths:
                    self.table.setColumnWidth(i, widths[key])
        finally:
            self._restoring = was

    def reset_to_defaults(self) -> bool:
        """Forget the personal layout and go back to the project's, or the built-in one."""
        self.forget_user_layout()
        project_def = self.load_project_default()
        if project_def:
            return self.apply_layout(project_def)
        return self.apply_builtin_layout()
