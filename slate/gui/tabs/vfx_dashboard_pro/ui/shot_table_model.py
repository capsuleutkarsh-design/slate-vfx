from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex, Signal
from typing import List, Optional, Dict, Any
from ..models.shot_model import Shot
from .group_header_delegate import GROUP_HEADER_ROLE
from slate.core.domain.departments import load_departments


class ShotTableModel(QAbstractTableModel):
    """
    Table model with all 19 VFX pipeline columns, supporting:
    - Inline cell editing (Status, Artist, Priority, SOW, Frames, etc.)
    - ShotGrid / Flow-style Group-By mode (Sequence/Reel, Status, Artist, Priority)
    - Collapsible section header rows with roll-up summary progress metrics
    """

    # How many cell edits can be taken back.
    UNDO_LIMIT = 100

    # Emitted when somebody without full edit rights changes a department
    # status they own, so it can be saved straight away.
    own_status_edited = Signal(object, str, str)

    # Columns that always exist, in the order they appear before and after
    # the per-department status columns.
    LEADING_COLUMNS = [
        ("reel", "Reel/EP", lambda s: s.reel_episode or "-"),
        ("shot_name", "Shot Name", lambda s: s.shot_name),
        ("status", "Status", lambda s: s.status),
        ("artist", "Artist", lambda s: s.assigned_artist or "-"),
        ("sow", "SOW", lambda s: s.sow[:50] + "..." if len(s.sow) > 50 else s.sow or "-"),
        ("frames", "Frames", lambda s: str(int(s.edit_frames)) if s.edit_frames else "-"),
        # What the client actually delivered, recorded by the ingest. Separate
        # from Frames, which is a length somebody types in.
        ("plate_range", "Plate Range", lambda s: s.frame_range_text or "-"),
        ("target", "Target", lambda s: s.target or "-"),
        ("type", "Type", lambda s: s.shot_type or "-"),
        ("priority", "Priority", lambda s: str(s.priority)),
        ("version", "Version", lambda s: s.curr_version or "-"),
    ]

    TRAILING_COLUMNS = [
        ("in_os", "IN/OS", lambda s: s.in_os or "-"),
        ("scan", "Scan Status", lambda s: s.scan_status or "-"),
        ("edit", "Edit Status", lambda s: s.edit_status or "-"),
    ]

    @staticmethod
    def build_columns():
        """
        Full column list: fixed columns plus one status column per department.

        Department columns come from slate/data/departments.json, so adding a
        department to that file adds a column here with no code change.
        """
        def dept_getter(dept_key):
            return lambda s: s.dept(dept_key).status or "-"

        columns = list(ShotTableModel.LEADING_COLUMNS)
        for dept in load_departments():
            columns.append((dept.key, dept.label, dept_getter(dept.key)))
        columns.extend(ShotTableModel.TRAILING_COLUMNS)
        return columns

    # Identity, plus what the ingest measured off the plate. A cell that
    # accepts typing and then ignores it looks broken.
    READ_ONLY_COLUMNS = ("reel", "shot_name", "plate_range")

    STATUS_COLORS = {
        "APPROVED": "#5FBF8F",
        "WIP": "#3EA8BF",
        "RETAKE": "#D9A441",
        "SENT FOR REVIEW": "#3EA8BF",
        "YTS": "#87857F",
    }

    def __init__(self, shots: List[Shot] = None, user_role: str = "artist",
                 user_identities=None):
        super().__init__()
        # Names this person answers to, for deciding which department rows are
        # theirs to update.
        self.user_identities = {
            str(i).strip().lower() for i in (user_identities or []) if str(i).strip()
        }
        # Instance attribute so a departments.json edit is picked up on reload.
        self.COLUMNS = self.build_columns()
        self._department_keys = {d.key for d in load_departments()}
        # For a department-scoped role (a lead): the department keys they may
        # edit. None means unrestricted; an empty set means nothing at all.
        self.department_scope = None
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        # Cell edits that can still be taken back, oldest first.
        self._undo_stack = []
        self.shots = shots or []
        self.group_by = "None"
        self.collapsed_groups = set()
        self.display_items: List[Dict[str, Any]] = []

        # Multi-role handling
        if isinstance(user_role, list):
            self.user_role = user_role[0].lower() if user_role else "artist"
            self.user_roles = [r.lower() for r in user_role]
        else:
            self.user_role = str(user_role or "artist").lower()
            self.user_roles = [self.user_role]

        self.headers = [col[1] for col in self.COLUMNS]
        self._rebuild_display_items()

    def set_group_by(self, mode: str):
        """Set grouping mode ('None', 'Reel / Sequence', 'Status', 'Artist', 'Priority')."""
        if self.group_by == mode:
            return
        self.beginResetModel()
        self.group_by = mode
        self._rebuild_display_items()
        self.endResetModel()

    def toggle_group_collapse(self, group_key: str):
        """Toggle collapse/expand state for a group."""
        self.beginResetModel()
        if group_key in self.collapsed_groups:
            self.collapsed_groups.remove(group_key)
        else:
            self.collapsed_groups.add(group_key)
        self._rebuild_display_items()
        self.endResetModel()

    def _rebuild_display_items(self):
        """Reconstruct the flat display items list considering grouping and collapse states."""
        self.display_items = []
        if not self.shots:
            return

        if self.group_by in ["None", "", None]:
            for shot in self.shots:
                self.display_items.append({"type": "shot", "shot": shot})
            return

        # Partition shots into groups
        grouped: Dict[str, List[Shot]] = {}
        for shot in self.shots:
            if self.group_by == "Reel / Sequence":
                key = (shot.reel_episode or "NO REEL").strip()
            elif self.group_by == "Status":
                key = (shot.status or "NO STATUS").strip().upper()
            elif self.group_by == "Artist":
                key = (shot.assigned_artist or "UNASSIGNED").strip()
            elif self.group_by == "Priority":
                key = f"Priority {shot.priority}"
            else:
                key = "ALL SHOTS"

            if key not in grouped:
                grouped[key] = []
            grouped[key].append(shot)

        # Build group headers and rows
        for group_key, group_shots in grouped.items():
            count = len(group_shots)
            approved = sum(1 for s in group_shots if (s.status or "").upper() in ["APPROVED", "DONE"])
            total_frames = sum(int(s.edit_frames or 0) for s in group_shots)
            
            total_bids = 0.0
            for s in group_shots:
                for dept in getattr(s, "departments", {}).values():
                    if getattr(dept, "bid_days", None):
                        try:
                            total_bids += float(dept.bid_days)
                        except Exception:
                            pass

            is_collapsed = group_key in self.collapsed_groups

            header_item = {
                "type": "header",
                "group_key": group_key,
                "title": group_key,
                "count": count,
                "approved_count": approved,
                "total_frames": total_frames,
                "total_bids": total_bids,
                "is_collapsed": is_collapsed,
            }
            self.display_items.append(header_item)

            if not is_collapsed:
                for shot in group_shots:
                    self.display_items.append({
                        "type": "shot",
                        "shot": shot,
                        "group_key": group_key
                    })

    def rowCount(self, parent=QModelIndex()):
        return len(self.display_items)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def is_group_header(self, row: int) -> bool:
        if 0 <= row < len(self.display_items):
            return self.display_items[row].get("type") == "header"
        return False

    def get_group_info(self, row: int) -> Optional[Dict[str, Any]]:
        if 0 <= row < len(self.display_items):
            item = self.display_items[row]
            if item.get("type") == "header":
                return item
        return None

    def get_shot_at(self, row: int) -> Optional[Shot]:
        if 0 <= row < len(self.display_items):
            item = self.display_items[row]
            if item.get("type") == "shot":
                return item.get("shot")
        return None

    def get_header_rows(self) -> List[int]:
        return [r for r, item in enumerate(self.display_items) if item.get("type") == "header"]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.display_items)):
            return None

        item = self.display_items[index.row()]
        col = index.column()

        # Handle Header Rows
        if item.get("type") == "header":
            if role == GROUP_HEADER_ROLE:
                return item
            if role == Qt.ItemDataRole.DisplayRole and col == 0:
                return item.get("title", "")
            return None

        # Handle Shot Rows
        shot = item.get("shot")
        if not shot:
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            getter = self.COLUMNS[col][2]
            return getter(shot)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in [5, 8, 10]:  # Frames, Priority, Version
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        item = self.display_items[index.row()]
        if item.get("type") == "header":
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        flags = super().flags(index)

        # RBAC check
        from slate.core.domain.access import can_edit_dashboard, is_offline_fallback

        if is_offline_fallback():
            return flags & ~Qt.ItemFlag.ItemIsEditable

        shot = item.get("shot")
        col_key = self.COLUMNS[index.column()][0]

        if not can_edit_dashboard(self.user_roles):
            # An artist may still set the status of a department they are
            # named on - the one field only they know the answer to.
            if self._owns_department(shot, col_key):
                return flags | Qt.ItemFlag.ItemIsEditable
            return flags

        # A lead edits their own department's columns and nothing else.
        if self.department_scope is not None:
            if col_key in self.department_scope:
                return flags | Qt.ItemFlag.ItemIsEditable
            return flags

        # Everything is editable except identity, and what the ingest measured.
        if col_key not in self.READ_ONLY_COLUMNS:
            flags |= Qt.ItemFlag.ItemIsEditable

        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False

        item = self.display_items[index.row()]
        if item.get("type") == "header":
            return False

        shot = item.get("shot")
        if not shot:
            return False

        col_key = self.COLUMNS[index.column()][0]

        # The same rule as flags(), enforced on the write. A model that offers
        # a cell for editing but does not check on save is a hole.
        from slate.core.domain.access import can_edit_dashboard, is_offline_fallback
        if is_offline_fallback():
            return False
        if not can_edit_dashboard(self.user_roles) and not self._owns_department(shot, col_key):
            return False
        if col_key in self.READ_ONLY_COLUMNS:
            return False
        if (can_edit_dashboard(self.user_roles) and self.department_scope is not None
                and col_key not in self.department_scope):
            return False

        try:
            previous = self._read_cell(shot, col_key)
            if not self._write_cell(shot, col_key, value):
                return False
            if previous == self._read_cell(shot, col_key):
                return True          # nothing actually changed

            self._push_undo(shot, col_key, previous)
            shot._modified = True
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])

            from slate.core.domain.access import can_edit_dashboard
            if not can_edit_dashboard(self.user_roles):
                # No Save button for this person; write it now.
                self.own_status_edited.emit(shot, col_key, str(value).strip())
            return True
        except Exception:
            return False

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder):
        """
        Sort by a column. Clicking a heading is how people expect a table to
        work, and this table had no sorting at all.

        Blank cells always sort last, in both directions - a shot with no
        target date is not "earliest", it is unscheduled. In group mode the
        order is kept inside each group.
        """
        if not (0 <= column < len(self.COLUMNS)):
            return

        getter = self.COLUMNS[column][2]

        def raw_value(shot):
            try:
                return str(getter(shot) or "").strip()
            except Exception:
                return ""

        def is_blank(shot):
            return raw_value(shot) in ("", "-")

        def sort_key(shot):
            text = raw_value(shot)
            try:
                # Numeric columns (priority, frames, bids) must not sort as text.
                return (0, float(text), "")
            except ValueError:
                return (1, 0.0, text.lower())

        ranked = [s for s in self.shots if not is_blank(s)]
        blanks = [s for s in self.shots if is_blank(s)]
        ranked.sort(key=sort_key,
                    reverse=(order == Qt.SortOrder.DescendingOrder))

        self.layoutAboutToBeChanged.emit()
        self.shots = ranked + blanks
        self._sort_column = column
        self._sort_order = order
        self._rebuild_display_items()
        self.layoutChanged.emit()

    # ------------------------------------------------------------------
    # Reading and writing one cell
    # ------------------------------------------------------------------

    _SIMPLE_FIELDS = {
        "status": "status",
        "artist": "assigned_artist",
        "sow": "sow",
        "target": "target",
        "type": "shot_type",
        "version": "curr_version",
        "in_os": "in_os",
        "scan": "scan_status",
        "edit": "edit_status",
    }

    def _read_cell(self, shot, col_key: str):
        """The stored value behind a cell, as undo would need to restore it."""
        if col_key in self._department_keys:
            return shot.dept(col_key).status
        if col_key in self._SIMPLE_FIELDS:
            return getattr(shot, self._SIMPLE_FIELDS[col_key], "")
        if col_key == "frames":
            return shot.edit_frames
        if col_key == "priority":
            return shot.priority
        return None

    def _write_cell(self, shot, col_key: str, value) -> bool:
        """Set the value behind a cell. Returns False for a read-only column."""
        if col_key in self._department_keys:
            shot.dept(col_key).status = str(value).strip()
            return True
        if col_key in self._SIMPLE_FIELDS:
            setattr(shot, self._SIMPLE_FIELDS[col_key], str(value).strip())
            return True
        if col_key == "frames":
            shot.edit_frames = int(value) if str(value).isdigit() else 0
            return True
        if col_key == "priority":
            shot.priority = int(value) if str(value).isdigit() else 0
            return True
        return False

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _push_undo(self, shot, col_key: str, previous):
        """Remember one cell edit so it can be taken back."""
        self._undo_stack.append((shot, col_key, previous))
        # Bounded: this is for the mistake noticed immediately, not a history.
        if len(self._undo_stack) > self.UNDO_LIMIT:
            self._undo_stack.pop(0)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def undo(self):
        """
        Take back the last cell edit.

        Returns a description of what was undone, or None if there was nothing
        to take back.
        """
        if not self._undo_stack:
            return None

        shot, col_key, previous = self._undo_stack.pop()
        current = self._read_cell(shot, col_key)
        self._write_cell(shot, col_key, "" if previous is None else previous)
        shot._modified = True

        row = next((i for i, item in enumerate(self.display_items)
                    if item.get("shot") is shot), None)
        if row is not None:
            column = next((i for i, spec in enumerate(self.COLUMNS)
                           if spec[0] == col_key), None)
            if column is not None:
                index = self.index(row, column)
                self.dataChanged.emit(index, index,
                                      [Qt.ItemDataRole.DisplayRole,
                                       Qt.ItemDataRole.EditRole])

        from slate.core.domain.access import can_edit_dashboard
        if not can_edit_dashboard(self.user_roles) and col_key in self._department_keys:
            # This person has no Save button, so the reversal is written too.
            self.own_status_edited.emit(shot, col_key, str(previous or ""))

        return {
            "shot": shot.shot_name,
            "column": col_key,
            "from": current,
            "to": previous,
        }

    def clear_undo(self):
        """Forget pending undo steps, e.g. after a refresh replaces the shots."""
        self._undo_stack.clear()

    def _owns_department(self, shot, col_key: str) -> bool:
        """
        True when this column is a department the current user is named on.

        Used to give an artist exactly one editable cell per shot they work on,
        without opening up anything else.
        """
        from slate.core.domain.access import can_edit_own_status

        if shot is None or col_key not in self._department_keys:
            return False
        if not self.user_identities or not can_edit_own_status(self.user_roles):
            return False

        try:
            assigned = str(shot.dept(col_key).artist or "").strip().lower()
        except Exception:
            return False
        return bool(assigned) and assigned in self.user_identities

    def update_data(self, shots: List[Shot]):
        self.clear_undo()
        self.beginResetModel()
        self.shots = shots or []
        self._rebuild_display_items()
        self.endResetModel()

        # Re-apply the user's chosen sort after a refresh.
        if self._sort_column >= 0:
            self.sort(self._sort_column, self._sort_order)

    def get_column_key(self, col_idx: int) -> str:
        if 0 <= col_idx < len(self.COLUMNS):
            return self.COLUMNS[col_idx][0]
        return ""

    def has_data_in_column(self, col_idx: int) -> bool:
        if not self.shots:
            return False
        getter = self.COLUMNS[col_idx][2]
        for shot in self.shots:
            val = getter(shot)
            if val and val != "-":
                return True
        return False
