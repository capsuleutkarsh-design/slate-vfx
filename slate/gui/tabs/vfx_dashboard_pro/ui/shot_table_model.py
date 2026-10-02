"""
The shots of one project, one row per shot, for the dashboard grid.

This is the source of a three-layer stack:

    ShotTableModel      every loaded shot; reads, edits, pending changes, undo
    ShotFilterProxy     what the search, filters and scope let through, sorted
    ShotGroupModel      the rows the grid shows, with group header rows

(shot_proxy_models.py has the other two.) Filtering used to rebuild the whole
model - and 262 board cards - on every keystroke; now a filter change is the
proxy re-checking rows.

Pending changes
---------------
Every edit - a grid cell, a board drop, a batch edit, the detail panel's
Apply - goes through ``apply_edit`` and becomes one undoable step. Each shot
remembers what it looked like when it was loaded (its baseline), so "unsaved"
means "differs from what is in the database", not "somebody touched it": six
edits and six undos leave nothing to save. The one "Save N changes" button
writes exactly the shots that differ.

Somebody without full edit rights (an artist) has no Save button: the one
cell they may change, their own department's status, is announced through
``own_status_edited`` and written straight away by the dashboard, with an
Undo on the confirmation.
"""

import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from slate.core.domain import shot_status
from slate.core.domain.departments import load_departments
from slate.core.infra.gate import Gate
from ..models.shot_model import Shot

# Roles the grid, the proxies and the delegates share.
SHOT_ROLE = Qt.ItemDataRole.UserRole + 1          # the Shot object behind a row
SORT_ROLE = Qt.ItemDataRole.UserRole + 2          # what the column sorts by (None = blank)
MODIFIED_ROLE = Qt.ItemDataRole.UserRole + 3      # this cell differs from the database
HEADER_SHORT_ROLE = Qt.ItemDataRole.UserRole + 4  # a header's short label, for narrow columns
COLUMN_KEY_ROLE = Qt.ItemDataRole.UserRole + 5    # the stable key of a column


def natural_key(text) -> str:
    """'SH2' before 'SH10', 'R01' before 'r02': numbers compare as numbers."""
    return re.sub(r"\d+", lambda m: m.group().zfill(12), str(text or "").strip().lower())


def _iso(value) -> Optional[str]:
    from slate.core.domain.dates import parse_date
    d = parse_date(value)
    return d.isoformat() if d else None


def snapshot(shot) -> Dict[str, Any]:
    """What a shot holds, for comparing with its baseline (version excluded)."""
    data = shot.to_dict()
    data.pop("version", None)
    return data


def restore(shot, data: Dict[str, Any]) -> None:
    """Put a snapshot's values back onto the same Shot object."""
    fresh = Shot.from_dict(dict(data))
    for name in Shot.__dataclass_fields__:
        if name.startswith("_") or name in ("id", "version"):
            continue
        setattr(shot, name, getattr(fresh, name))
    # from_dict upper-cases a status; put back exactly what was there.
    if "status" in data:
        shot.status = data["status"]


def changed_fields(shot) -> List[tuple]:
    """
    [(field, before, after)] for what differs from the baseline.

    Department fields are spelled "departments.comp.status" so they can be
    applied to another copy of the shot (a conflict re-applies only these).
    """
    before = getattr(shot, "_baseline", None)
    if before is None:
        return []
    after = snapshot(shot)
    out = []
    for key in sorted(set(before) | set(after)):
        if key == "departments":
            old_depts = before.get(key) or {}
            new_depts = after.get(key) or {}
            for dept in sorted(set(old_depts) | set(new_depts)):
                old = old_depts.get(dept) or {}
                new = new_depts.get(dept) or {}
                for leaf in sorted(set(old) | set(new)):
                    if old.get(leaf) != new.get(leaf):
                        out.append((f"departments.{dept}.{leaf}", old.get(leaf), new.get(leaf)))
        elif before.get(key) != after.get(key):
            out.append((key, before.get(key), after.get(key)))
    return out


def get_field(shot, path: str):
    if path.startswith("departments."):
        _, dept, leaf = path.split(".", 2)
        return getattr(shot.dept(dept), leaf, None)
    return getattr(shot, path, None)


def set_field(shot, path: str, value) -> None:
    if path.startswith("departments."):
        _, dept, leaf = path.split(".", 2)
        setattr(shot.dept(dept), leaf, value)
        return
    setattr(shot, path, value)


# What a field is called when a person reads about it ("Comp status").
_FIELD_NAMES = {
    "status": "status", "assigned_artist": "artist", "sow": "scope of work",
    "target": "target", "shot_type": "type", "priority": "priority",
    "curr_version": "version", "edit_frames": "frames", "in_os": "IN/OS",
    "scan_status": "scan status", "edit_status": "edit status", "is_hero": "hero",
    "similar_to": "linked heroes", "description": "description", "notes": "notes",
}


def field_label(path: str) -> str:
    if path.startswith("departments."):
        _, dept, leaf = path.split(".", 2)
        name = next((d.name for d in load_departments() if d.key == dept), dept.title())
        leaf_name = {"bid_days": "bid", "artist": "artist", "status": "status",
                     "target": "target", "eta": "ETA", "wip_date": "WIP date"}.get(leaf, leaf)
        return f"{name} {leaf_name}"
    return _FIELD_NAMES.get(path, path.replace("_", " "))


def display_value(path: str, value) -> str:
    if value in (None, ""):
        return "(empty)"
    if path == "priority":
        return shot_status.priority_label(value)
    if path.endswith("target") or path == "target":
        from slate.core.domain.dates import format_date
        return format_date(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class ShotTableModel(QAbstractTableModel):
    """
    Every shot of the open project, one row each, with all the grid columns.
    """

    # How many edits can be taken back.
    UNDO_LIMIT = 100

    # (shot, department key, new status, previous status): somebody without
    # full edit rights changed a department status they own; the dashboard
    # writes it at once.
    own_status_edited = Signal(object, str, str, str)
    # Something was staged or undone: (shots touched, description).
    edits_changed = Signal()

    # Columns that always exist, in the order they appear before and after
    # the per-department status columns. The getter is what the cell shows.
    LEADING_COLUMNS = [
        ("reel", "Reel/EP", lambda s: s.reel_episode or "-"),
        ("shot_name", "Shot Name", lambda s: s.shot_name),
        ("status", "Status", lambda s: s.status or ""),
        ("artist", "Artist", lambda s: s.assigned_artist or "-"),
        ("sow", "SOW", lambda s: " ".join(str(s.sow or "").split()) or "-"),
        ("frames", "Frames", lambda s: str(int(s.edit_frames)) if s.edit_frames else "-"),
        # What the client actually delivered, recorded by the ingest. Separate
        # from Frames, which is a length somebody types in.
        ("plate_range", "Plate Range", lambda s: s.frame_range_text or "-"),
        ("target", "Target", lambda s: ShotTableModel._target_text(s.target)),
        ("type", "Type", lambda s: s.shot_type or "-"),
        ("priority", "Priority", lambda s: shot_status.priority_label(s.priority)),
        ("version", "Version", lambda s: s.curr_version or "-"),
    ]

    TRAILING_COLUMNS = [
        ("in_os", "IN/OS", lambda s: s.in_os or "-"),
        ("scan", "Scan Status", lambda s: s.scan_status or "-"),
        ("edit", "Edit Status", lambda s: s.edit_status or "-"),
    ]

    # What each fixed column means, for the header tooltip.
    COLUMN_TIPS = {
        "reel": "Reel or episode the shot belongs to",
        "shot_name": "Shot name",
        "status": "Where the shot is in the workflow",
        "artist": "Artist responsible for the shot",
        "sow": "Scope of work",
        "frames": "Cut length in frames, as typed by production",
        "plate_range": "First and last frame of the delivered plate, measured at ingest",
        "target": "Target date. Red when overdue, amber when due within a week",
        "type": "Shot type",
        "priority": "Priority: Urgent, High, Normal or Low",
        "version": "Current version",
        "in_os": "IN/OS",
        "scan": "Scan status",
        "edit": "Edit status",
    }

    # Identity, plus what the ingest measured off the plate. A cell that
    # accepts typing and then ignores it looks broken.
    READ_ONLY_COLUMNS = ("reel", "shot_name", "plate_range")

    # Centred numbers; everything else reads left to right.
    CENTRED_COLUMNS = ("frames", "priority", "version", "plate_range")

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
        "frames": "edit_frames",
        "priority": "priority",
    }

    @staticmethod
    def _target_text(value) -> str:
        from slate.core.domain.dates import format_date
        text = format_date(value)
        return text or "-"

    @staticmethod
    def build_columns():
        """
        Full column list: fixed columns plus one status column per department.

        Department columns come from slate/data/departments.json, so adding a
        department to that file adds a column here with no code change.
        """
        def dept_getter(dept_key):
            return lambda s: s.dept(dept_key).status or ""

        columns = list(ShotTableModel.LEADING_COLUMNS)
        for dept in load_departments():
            columns.append((dept.key, dept.label, dept_getter(dept.key)))
        columns.extend(ShotTableModel.TRAILING_COLUMNS)
        return columns

    def __init__(self, shots: List[Shot] = None, user_role="artist",
                 user_identities=None):
        super().__init__()
        # Names this person answers to, for deciding which department rows are
        # theirs to update.
        self.user_identities = {
            str(i).strip().lower() for i in (user_identities or []) if str(i).strip()
        }
        # Instance attribute so a departments.json edit is picked up on reload.
        self.COLUMNS = self.build_columns()
        self._departments = {d.key: d for d in load_departments()}
        self._department_keys = set(self._departments)
        self._key_to_column = {spec[0]: i for i, spec in enumerate(self.COLUMNS)}
        # For a department-scoped role (a lead): the department keys they may
        # edit. None means unrestricted; an empty set means nothing at all.
        self.department_scope = None
        # Edits that can still be taken back, oldest first. Each step is
        # (description, [(shot, snapshot before)]).
        self._undo_stack = []
        self.shots: List[Shot] = []
        self._row_of = {}

        if isinstance(user_role, list):
            self.user_roles = [str(r).lower() for r in user_role] or ["artist"]
        else:
            self.user_roles = [str(user_role or "artist").lower()]
        self.user_role = self.user_roles[0]

        self.headers = [col[1] for col in self.COLUMNS]
        self.set_shots(shots or [])

    # ------------------------------------------------------------ structure
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.shots)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def column_index(self, key: str) -> int:
        return self._key_to_column.get(key, -1)

    def get_column_key(self, col_idx: int) -> str:
        if 0 <= col_idx < len(self.COLUMNS):
            return self.COLUMNS[col_idx][0]
        return ""

    def get_shot_at(self, row: int) -> Optional[Shot]:
        if 0 <= row < len(self.shots):
            return self.shots[row]
        return None

    def row_of(self, shot) -> int:
        return self._row_of.get(id(shot), -1)

    def set_shots(self, shots: List[Shot], keep_undo: bool = False):
        """
        Show these shots. A shot arriving without a baseline is fresh from the
        database, so it is clean by definition; shots that keep their pending
        edits (a live update keeps them) keep their baseline too.
        """
        shots = list(shots or [])
        if keep_undo:
            # A live update swapped some shots: edits to shots still shown can
            # still be taken back; edits to replaced ones cannot.
            still_here = {id(s) for s in shots}
            kept = []
            for description, entries in self._undo_stack:
                entries = [(s, snap) for s, snap in entries if id(s) in still_here]
                if entries:
                    kept.append((description, entries))
            self._undo_stack = kept
        else:
            self._undo_stack = []
        for shot in shots:
            # A shot without a baseline is fresh from the database - unless it
            # already carries edits (then there is nothing true to compare to,
            # and its own flag stands).
            if getattr(shot, "_baseline", None) is None and not getattr(shot, "_modified", False):
                self._take_baseline(shot)
        self.beginResetModel()
        self.shots = shots
        self._row_of = {id(s): i for i, s in enumerate(shots)}
        self.endResetModel()
        self.edits_changed.emit()

    # Kept for older callers.
    def update_data(self, shots: List[Shot], keep_undo: bool = False):
        self.set_shots(shots, keep_undo=keep_undo)

    # ------------------------------------------------------------ baseline
    @staticmethod
    def _take_baseline(shot):
        try:
            shot._baseline = snapshot(shot)
        except Exception:
            shot._baseline = None
        shot._modified = False

    def mark_clean(self, shots: Iterable[Shot]):
        """These shots now match the database (just loaded, or just saved)."""
        touched = []
        for shot in shots or []:
            self._take_baseline(shot)
            shot._remote_changed = False
            touched.append(shot)
        self._emit_rows(touched)
        self.edits_changed.emit()

    @staticmethod
    def is_dirty(shot) -> bool:
        baseline = getattr(shot, "_baseline", None)
        if baseline is None:
            return bool(getattr(shot, "_modified", False))
        return snapshot(shot) != baseline

    def pending_shots(self) -> List[Shot]:
        return [s for s in self.shots if getattr(s, "_modified", False)]

    def _baseline_cell(self, shot, col_key):
        base = getattr(shot, "_baseline", None)
        if base is None:
            return self._read_cell(shot, col_key)
        if col_key in self._department_keys:
            return ((base.get("departments") or {}).get(col_key) or {}).get("status") or ""
        field = self._SIMPLE_FIELDS.get(col_key)
        if field is None:
            return self._read_cell(shot, col_key)
        return base.get(field)

    def cell_modified(self, shot, col_key) -> bool:
        if not getattr(shot, "_modified", False):
            return False
        now = self._read_cell(shot, col_key)
        then = self._baseline_cell(shot, col_key)
        if col_key in ("frames", "priority"):
            try:
                return float(now or 0) != float(then or 0)
            except (TypeError, ValueError):
                return now != then
        return (now or "") != (then or "")

    # ------------------------------------------------------------ reading
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.shots)):
            return None
        shot = self.shots[index.row()]
        col = index.column()
        col_key = self.COLUMNS[col][0]

        if role == Qt.ItemDataRole.DisplayRole:
            value = self.COLUMNS[col][2](shot)
            if getattr(shot, "_remote_changed", False) and col_key == "shot_name":
                return f"⚠ {value}"
            return value

        if role == Qt.ItemDataRole.EditRole:
            # The stored value, never the display text: an editor that opens
            # on "-" or a 50-character cut writes that back when it closes.
            value = self._read_cell(shot, col_key)
            if col_key == "frames":
                try:
                    return int(float(value or 0))
                except (TypeError, ValueError):
                    return 0
            if col_key == "priority":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return 3
            return "" if value is None else str(value)

        if role == SHOT_ROLE:
            return shot

        if role == COLUMN_KEY_ROLE:
            return col_key

        if role == SORT_ROLE:
            return self._sort_value(shot, col_key)

        if role == MODIFIED_ROLE:
            return self.cell_modified(shot, col_key)

        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(shot, col_key)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col_key in self.CENTRED_COLUMNS:
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.BackgroundRole:
            if getattr(shot, "_modified", False):
                return QColor(Gate.tint(Gate.WARN, 0.10))
            if self._restricted() and self._editable(shot, col_key):
                # What this person may change, out of a grid they mostly cannot.
                return QColor(Gate.tint(Gate.ACCENT, 0.10))
            return None

        if role == Qt.ItemDataRole.FontRole:
            if self.cell_modified(shot, col_key):
                font = QFont()
                font.setItalic(True)
                font.setBold(True)
                return font
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if col_key == "target":
                state = self.target_state(shot)
                if state == "overdue":
                    return QColor(Gate.BAD)
                if state == "due_soon":
                    return QColor(Gate.WARN)
            return None

        return None

    def _sort_value(self, shot, col_key):
        value = self._read_cell(shot, col_key)
        if col_key in self._department_keys or col_key == "status":
            if not str(value or "").strip():
                return None
            rank, text = shot_status.order_key(value)
            return f"{rank:03d}{text}"
        if col_key == "frames":
            try:
                number = float(value or 0)
            except (TypeError, ValueError):
                return None
            return number if number else None
        if col_key == "priority":
            try:
                return float(int(value))
            except (TypeError, ValueError):
                return None
        if col_key == "target":
            # By date; text that is not a date ('TBD') sorts with the blanks.
            return _iso(value)
        if col_key == "plate_range":
            return float(shot.first_frame) if shot.frame_count else None
        text = str(value or "").strip()
        if col_key == "reel":
            text = shot.reel_episode or ""
        if col_key == "shot_name":
            text = shot.shot_name or ""
        return natural_key(text) if text and text != "-" else None

    def target_state(self, shot) -> str:
        """'overdue', 'due_soon' or '' - finished and omitted shots are never late."""
        if shot_status.is_done(shot.status) or shot_status.is_omitted(shot.status):
            return ""
        from datetime import date
        from slate.core.domain.dates import parse_date
        target = parse_date(shot.target)
        if target is None:
            return ""
        days = (target - date.today()).days
        if days < 0:
            return "overdue"
        if days <= 7:
            return "due_soon"
        return ""

    def _tooltip(self, shot, col_key):
        lines = []
        if getattr(shot, "_remote_changed", False):
            lines.append("Someone else saved this shot after you edited it here. "
                         "Your unsaved edits are kept - check them before you save.")
        if col_key == "shot_name":
            text = f"{shot.shot_name} ({shot.reel_episode})" if shot.reel_episode else shot.shot_name
            lines.insert(0, text)
            mine = self._my_departments(shot)
            if mine:
                lines.append("On your list for: " + ", ".join(mine))
        elif col_key == "status":
            lines.insert(0, shot_status.describe(shot.status))
        elif col_key in self._department_keys:
            dept = shot.dept(col_key)
            name = self._departments[col_key].name if col_key in self._departments else col_key
            parts = [f"{name}: {shot_status.describe(dept.status)}"]
            if dept.artist:
                parts.append(f"Artist: {dept.artist}")
            if self._owns_department(shot, col_key):
                parts.append("You can set this status.")
            lines.insert(0, "\n".join(parts))
        elif col_key == "sow":
            if shot.sow:
                lines.insert(0, str(shot.sow))
        elif col_key == "target":
            state = self.target_state(shot)
            if shot.target:
                from datetime import date
                from slate.core.domain.dates import parse_date, format_date
                d = parse_date(shot.target)
                if d is None:
                    lines.insert(0, f"'{shot.target}' is not a date. Pick one to fix it.")
                elif state == "overdue":
                    lines.insert(0, f"Overdue by {(date.today() - d).days} day(s) - due {format_date(d)}")
                elif state == "due_soon":
                    lines.insert(0, f"Due {format_date(d)}")
        elif col_key == "priority":
            lines.insert(0, f"Priority: {shot_status.priority_label(shot.priority)}")
        else:
            text = self.COLUMNS[self.column_index(col_key)][2](shot)
            if text and text != "-":
                lines.insert(0, str(text))
        if self.cell_modified(shot, col_key):
            before = self._baseline_cell(shot, col_key)
            lines.append(f"Not saved yet. Was: {before if before not in (None, '') else '(empty)'}")
        return "\n".join(lines) if lines else None

    def _my_departments(self, shot) -> List[str]:
        if not self.user_identities:
            return []
        out = []
        for key, dept in self._departments.items():
            artist = str(shot.dept(key).artist or "").strip().lower()
            if artist and artist in self.user_identities:
                out.append(dept.name)
        if str(shot.assigned_artist or "").strip().lower() in self.user_identities and not out:
            out.append("the shot")
        return out

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or not (0 <= section < len(self.COLUMNS)):
            return None
        key = self.COLUMNS[section][0]
        if role == Qt.ItemDataRole.DisplayRole:
            # The full department name where it is short enough; the header
            # view falls back to the short label in a narrow column.
            if key in self._departments:
                return self._departments[key].name
            return self.headers[section]
        if role == HEADER_SHORT_ROLE:
            return self.headers[section]
        if role == COLUMN_KEY_ROLE:
            return key
        if role == Qt.ItemDataRole.ToolTipRole:
            if key in self._departments:
                dept = self._departments[key]
                return f"{dept.name} status ({dept.label})"
            return self.COLUMN_TIPS.get(key, self.headers[section])
        return None

    # ------------------------------------------------------------ editing
    def _restricted(self) -> bool:
        """This person may edit only some cells (an artist, a lead)."""
        from slate.core.domain.access import can_edit_dashboard
        return (not can_edit_dashboard(self.user_roles)) or self.department_scope is not None

    def _editable(self, shot, col_key) -> bool:
        from slate.core.domain.access import can_edit_dashboard, is_offline_fallback
        if is_offline_fallback():
            return False
        if col_key in self.READ_ONLY_COLUMNS:
            return False
        if not can_edit_dashboard(self.user_roles):
            # An artist may still set the status of a department they are
            # named on - the one field only they know the answer to.
            return self._owns_department(shot, col_key)
        if self.department_scope is not None:
            # A lead edits their own department's columns and nothing else.
            return col_key in self.department_scope
        return True

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        shot = self.get_shot_at(index.row())
        if shot is not None and self._editable(shot, self.COLUMNS[index.column()][0]):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def can_edit_freely(self) -> bool:
        from slate.core.domain.access import can_edit_dashboard
        return can_edit_dashboard(self.user_roles)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        shot = self.get_shot_at(index.row())
        if shot is None:
            return False
        col_key = self.COLUMNS[index.column()][0]

        # The same rule as flags(), enforced on the write. A model that offers
        # a cell for editing but does not check on save is a hole.
        if not self._editable(shot, col_key):
            return False

        new_value = self._coerce(col_key, value)
        if new_value is _INVALID:
            return False
        previous = self._read_cell(shot, col_key)
        if self._same(col_key, previous, new_value):
            return True          # nothing actually changed

        if not self.can_edit_freely():
            # No Save button for this person; the dashboard writes it now.
            self._write_cell(shot, col_key, new_value)
            self._emit_rows([shot])
            self.own_status_edited.emit(shot, col_key, str(new_value or ""), str(previous or ""))
            return True

        column = self.COLUMNS[index.column()][1]
        description = f"{shot.shot_name} {column}"
        self.apply_edit([shot], lambda s: self._write_cell(s, col_key, new_value), description)
        return True

    def _coerce(self, col_key, value):
        """What a typed or picked value means for this column, or _INVALID."""
        if col_key == "frames":
            text = str(value if value is not None else "").strip().replace(",", "")
            if text in ("", "-"):
                return 0.0
            try:
                number = float(text)
            except ValueError:
                return _INVALID
            if number < 0 or not number.is_integer():
                return _INVALID
            return number
        if col_key == "priority":
            if isinstance(value, int):
                return value if value in {v for v, _ in shot_status.priorities()} else _INVALID
            number = shot_status.priority_value(value)
            return _INVALID if number is None else number
        if col_key == "target":
            text = str(value if value is not None else "").strip()
            if not text or text == "-":
                return ""
            iso = _iso(text)
            # A date, stored as ISO. Text that is not a date is refused rather
            # than stored ('next friday' used to sort above 2027).
            return iso if iso else _INVALID
        text = str(value if value is not None else "").strip()
        if text == "-" and col_key not in ("sow",):
            text = ""
        if col_key == "status" or col_key in self._department_keys:
            text = shot_status.normalise(text) if text else ""
        return text

    @staticmethod
    def _same(col_key, old, new) -> bool:
        if col_key in ("frames", "priority"):
            try:
                return float(old or 0) == float(new or 0)
            except (TypeError, ValueError):
                return False
        return str(old if old is not None else "") == str(new if new is not None else "")

    def _read_cell(self, shot, col_key: str):
        """The stored value behind a cell."""
        if col_key in self._department_keys:
            return shot.dept(col_key).status
        field = self._SIMPLE_FIELDS.get(col_key)
        if field:
            return getattr(shot, field, "")
        if col_key == "reel":
            return shot.reel_episode
        if col_key == "shot_name":
            return shot.shot_name
        if col_key == "plate_range":
            return shot.frame_range_text
        return None

    def _write_cell(self, shot, col_key: str, value) -> bool:
        """Set the value behind a cell. Returns False for a read-only column."""
        if col_key in self._department_keys:
            shot.dept(col_key).status = str(value or "").strip()
            return True
        if col_key == "frames":
            shot.edit_frames = float(value or 0)
            return True
        if col_key == "priority":
            shot.priority = int(value)
            return True
        field = self._SIMPLE_FIELDS.get(col_key)
        if field:
            setattr(shot, field, str(value if value is not None else "").strip())
            return True
        return False

    # ------------------------------------------------------------ staging
    def apply_edit(self, shots: Iterable[Shot], change: Callable[[Shot], Any],
                   description: str = "") -> List[Shot]:
        """
        Make one change to these shots as a single undoable step.

        Every editor uses this - a grid cell, a board drop, a batch edit, the
        detail panel - so they all wait for the one Save, are all counted, and
        all go back with Ctrl+Z. Returns the shots that actually changed.
        """
        entries = []
        changed = []
        for shot in shots or []:
            before = snapshot(shot)
            change(shot)
            if snapshot(shot) != before:
                entries.append((shot, before))
                changed.append(shot)
                shot._modified = self.is_dirty(shot)
        if not entries:
            return []
        self._undo_stack.append((description or "edit", entries))
        # Bounded: this is for the mistake noticed immediately, not a history.
        while len(self._undo_stack) > self.UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._emit_rows(changed)
        self.edits_changed.emit()
        return changed

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def undo(self):
        """
        Take back the last edit step.

        Returns {'description', 'shots', 'shot', 'count'} or None if there was
        nothing to take back. A shot whose edits are all undone is clean again.
        """
        if not self._undo_stack:
            return None
        description, entries = self._undo_stack.pop()
        touched = []
        for shot, before in reversed(entries):
            restore(shot, before)
            shot._modified = self.is_dirty(shot)
            touched.append(shot)
        self._emit_rows(touched)
        self.edits_changed.emit()
        return {
            "description": description,
            "shots": touched,
            "shot": touched[0].shot_name if touched else "",
            "count": len(touched),
        }

    def clear_undo(self):
        """Forget pending undo steps, e.g. after a save or a reload."""
        self._undo_stack.clear()

    def discard_changes(self, shots: Optional[Iterable[Shot]] = None):
        """Put shots back to how they were loaded (all pending shots if none given)."""
        targets = list(shots) if shots is not None else self.pending_shots()
        for shot in targets:
            base = getattr(shot, "_baseline", None)
            if base is not None:
                restore(shot, base)
            shot._modified = False
        self._undo_stack = [
            (d, [(s, b) for s, b in e if s not in targets]) for d, e in self._undo_stack
        ]
        self._undo_stack = [(d, e) for d, e in self._undo_stack if e]
        self._emit_rows(targets)
        self.edits_changed.emit()

    def refresh_shots(self, shots: Iterable[Shot]):
        """Repaint these rows (their data changed outside the model)."""
        self._emit_rows(list(shots or []))

    def _emit_rows(self, shots):
        if not self.COLUMNS:
            return
        last = len(self.COLUMNS) - 1
        for shot in shots:
            row = self.row_of(shot)
            if row >= 0:
                self.dataChanged.emit(self.index(row, 0), self.index(row, last))

    # ------------------------------------------------------------ ownership
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

    def has_data_in_column(self, col_idx: int) -> bool:
        if not self.shots:
            return False
        getter = self.COLUMNS[col_idx][2]
        for shot in self.shots:
            val = getter(shot)
            if val and val != "-":
                return True
        return False


class _Invalid:
    def __repr__(self):
        return "<invalid>"


_INVALID = _Invalid()
