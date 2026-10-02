"""
Filtering, sorting and grouping on top of ShotTableModel.

    ShotTableModel  ->  ShotFilterProxy  ->  ShotGroupModel  ->  the grid

ShotFilterProxy decides which shots are on screen (search, status, scope,
column filters, the Filters dialog) and in what order. A filter change is the
proxy re-checking rows; nothing is rebuilt.

ShotGroupModel adds the group header rows of "Group: Reel / Status / Artist /
Priority". Groups come in a fixed order - reels and artists naturally, statuses
in workflow order, priorities by number - whatever column the rows inside are
sorted by. With no grouping it passes rows straight through.
"""

from contextlib import contextmanager
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractProxyModel, QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt,
)

from slate.core.domain import shot_status
from .group_header_delegate import GROUP_HEADER_ROLE
from .shot_table_model import SHOT_ROLE, SORT_ROLE, natural_key


class ShotFilterProxy(QSortFilterProxyModel):
    """Which shots pass the current filters, and their order."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._predicate: Optional[Callable] = None
        self.setSortRole(SORT_ROLE)
        # Rows do not jump while somebody is editing them; a filter or sort is
        # applied when it is asked for.
        self.setDynamicSortFilter(False)

    def set_predicate(self, predicate: Optional[Callable]):
        self._predicate = predicate
        self.invalidateFilter()

    def refilter(self):
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if self._predicate is None:
            return True
        model = self.sourceModel()
        shot = model.get_shot_at(source_row) if hasattr(model, "get_shot_at") else None
        if shot is None:
            return False
        try:
            return bool(self._predicate(shot))
        except Exception:
            return True

    def lessThan(self, left, right):
        a = left.data(SORT_ROLE)
        b = right.data(SORT_ROLE)
        # Blank cells sort last in both directions: a shot with no target is
        # not "earliest", it is unscheduled.
        if a is None or b is None:
            if a is None and b is None:
                return self._tie(left, right)
            descending = self.sortOrder() == Qt.SortOrder.DescendingOrder
            return (a is None) if descending else (b is None)
        if a == b:
            return self._tie(left, right)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

    def _tie(self, left, right):
        # Equal keys keep reel/shot order, so a sort never shuffles the rest.
        model = self.sourceModel()
        sa = model.get_shot_at(left.row())
        sb = model.get_shot_at(right.row())
        if sa is None or sb is None:
            return left.row() < right.row()
        ka = (natural_key(sa.reel_episode), natural_key(sa.shot_name))
        kb = (natural_key(sb.reel_episode), natural_key(sb.shot_name))
        if ka == kb:
            return left.row() < right.row()
        less = ka < kb
        # In a descending sort Qt reverses lessThan; keep ties ascending.
        return (not less) if self.sortOrder() == Qt.SortOrder.DescendingOrder else less

    def shots(self) -> list:
        return [self.index(r, 0).data(SHOT_ROLE) for r in range(self.rowCount())]


GROUP_MODES = ("None", "Reel / Sequence", "Status", "Artist", "Priority")


def group_key_of(shot, mode: str):
    """(sort key, key, title) of the group a shot belongs to."""
    if mode == "Reel / Sequence":
        reel = str(shot.reel_episode or "").strip()
        return ((1, "") if not reel else (0, natural_key(reel)), reel or "", reel or "No reel")
    if mode == "Status":
        text = shot_status.canonical(shot.status)
        return (shot_status.order_key(text), text, shot_status.label(text))
    if mode == "Artist":
        name = str(shot.assigned_artist or "").strip()
        return ((1, "") if not name else (0, natural_key(name)), name, name or "Unassigned")
    if mode == "Priority":
        try:
            number = int(shot.priority)
        except (TypeError, ValueError):
            number = 99
        return ((number,), str(number), shot_status.priority_label(number))
    return ((0,), "", "All shots")


class ShotGroupModel(QAbstractProxyModel):
    """
    The rows the grid shows: the filter proxy's rows, with a header row at the
    top of each group when a grouping is chosen.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.group_by = "None"
        self.collapsed_groups = set()
        # Each row: ("shot", source row) or ("header", info dict).
        self._rows: List[tuple] = []
        self._from_source: Dict[int, int] = {}
        self._bulk = 0
        self._saved = None

    # ------------------------------------------------------------ wiring
    def setSourceModel(self, model):
        old = self.sourceModel()
        if old is not None:
            for name in ("dataChanged", "headerDataChanged", "modelAboutToBeReset", "modelReset",
                         "layoutAboutToBeChanged", "layoutChanged", "rowsAboutToBeInserted",
                         "rowsInserted", "rowsAboutToBeRemoved", "rowsRemoved",
                         "rowsAboutToBeMoved", "rowsMoved"):
                try:
                    getattr(old, name).disconnect(self)
                except Exception:
                    pass
        self.beginResetModel()
        super().setSourceModel(model)
        if model is not None:
            model.dataChanged.connect(self._on_data_changed)
            model.headerDataChanged.connect(self.headerDataChanged)
            model.modelAboutToBeReset.connect(self._begin_reset)
            model.modelReset.connect(self._end_reset)
            for about, done in (("layoutAboutToBeChanged", "layoutChanged"),
                                ("rowsAboutToBeInserted", "rowsInserted"),
                                ("rowsAboutToBeRemoved", "rowsRemoved"),
                                ("rowsAboutToBeMoved", "rowsMoved")):
                getattr(model, about).connect(self._begin_change)
                getattr(model, done).connect(self._end_change)
        self._rebuild()
        self.endResetModel()

    def _begin_reset(self, *args):
        if self._bulk:
            return
        self.beginResetModel()

    def _end_reset(self, *args):
        if self._bulk:
            return
        self._rebuild()
        self.endResetModel()

    def _begin_change(self, *args):
        if self._bulk:
            return
        self._capture()

    def _end_change(self, *args):
        if self._bulk:
            return
        self._release()

    @contextmanager
    def bulk(self):
        """One layout change for a whole re-filter or re-sort underneath."""
        if self._bulk == 0:
            self._capture()
        self._bulk += 1
        try:
            yield
        finally:
            self._bulk -= 1
            if self._bulk == 0:
                self._release()

    def _capture(self):
        """Before the rows change: remember what every persistent index points at."""
        self.layoutAboutToBeChanged.emit()
        saved = []
        for index in self.persistentIndexList():
            kind, payload = self._rows[index.row()] if 0 <= index.row() < len(self._rows) else (None, None)
            if kind == "shot":
                source = self.sourceModel().index(payload, index.column())
                saved.append((index, "shot", QPersistentModelIndex(source)))
            elif kind == "header":
                saved.append((index, "header", (payload.get("group_key"), index.column())))
            else:
                saved.append((index, None, None))
        self._saved = saved

    def _release(self):
        """After: rebuild the rows and move every persistent index with its shot."""
        self._rebuild()
        old, new = [], []
        for index, kind, target in self._saved or []:
            old.append(index)
            if kind == "shot" and target.isValid():
                row = self._from_source.get(target.row(), -1)
                new.append(self.index(row, target.column()) if row >= 0 else QModelIndex())
            elif kind == "header":
                key, column = target
                row = next((r for r, (k, p) in enumerate(self._rows)
                            if k == "header" and p.get("group_key") == key), -1)
                new.append(self.index(row, column) if row >= 0 else QModelIndex())
            else:
                new.append(QModelIndex())
        self._saved = None
        if old:
            self.changePersistentIndexList(old, new)
        self.layoutChanged.emit()

    def _on_data_changed(self, top_left, bottom_right, roles=()):
        if self._bulk:
            return
        if not top_left.isValid():
            return
        rows = [self._from_source.get(r, -1) for r in range(top_left.row(), bottom_right.row() + 1)]
        rows = [r for r in rows if r >= 0]
        if not rows:
            return
        self.dataChanged.emit(self.index(min(rows), top_left.column()),
                              self.index(max(rows), bottom_right.column()), roles)

    # ------------------------------------------------------------ grouping
    def set_group_by(self, mode: str):
        """'None', 'Reel / Sequence', 'Status', 'Artist' or 'Priority'."""
        mode = mode if mode in GROUP_MODES else "None"
        if mode == self.group_by:
            return
        self.beginResetModel()
        self.group_by = mode
        self.collapsed_groups = set()
        self._rebuild()
        self.endResetModel()

    def toggle_group_collapse(self, group_key):
        if group_key in self.collapsed_groups:
            self.collapsed_groups.discard(group_key)
        else:
            self.collapsed_groups.add(group_key)
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    def set_all_collapsed(self, collapsed: bool):
        if collapsed:
            self.collapsed_groups = {p.get("group_key") for k, p in self._group_headers()}
        else:
            self.collapsed_groups = set()
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    def _group_headers(self):
        return [(k, p) for k, p in self._rows if k == "header"]

    def _rebuild(self):
        self._rows = []
        self._from_source = {}
        source = self.sourceModel()
        if source is None:
            return
        count = source.rowCount()
        if self.group_by in ("None", "", None):
            self._rows = [("shot", r) for r in range(count)]
            self._from_source = {r: r for r in range(count)}
            return

        groups: Dict = {}
        order = []
        for r in range(count):
            shot = source.index(r, 0).data(SHOT_ROLE)
            if shot is None:
                continue
            sort_key, key, title = group_key_of(shot, self.group_by)
            if key not in groups:
                groups[key] = {"sort": sort_key, "title": title, "rows": []}
                order.append(key)
            groups[key]["rows"].append((r, shot))

        for key in sorted(order, key=lambda k: groups[k]["sort"]):
            group = groups[key]
            members = [shot for _, shot in group["rows"]]
            total_frames = 0
            total_bids = 0.0
            for s in members:
                try:
                    total_frames += int(float(s.edit_frames or 0))
                except (TypeError, ValueError):
                    pass
                for dept in getattr(s, "departments", {}).values():
                    try:
                        total_bids += float(getattr(dept, "bid_days", 0) or 0)
                    except (TypeError, ValueError):
                        pass
            counted = [s for s in members if not shot_status.is_omitted(s.status)]
            info = {
                "type": "header",
                "group_key": key,
                "title": group["title"],
                "count": len(members),
                "approved_count": sum(1 for s in counted if shot_status.is_done(s.status)),
                "counted": len(counted),
                "total_frames": total_frames,
                "total_bids": total_bids,
                "is_collapsed": key in self.collapsed_groups,
                # Grouped by status, "3/33 approved" says nothing new.
                "show_progress": self.group_by != "Status",
            }
            self._rows.append(("header", info))
            if key in self.collapsed_groups:
                continue
            for r, _shot in group["rows"]:
                self._from_source[r] = len(self._rows)
                self._rows.append(("shot", r))

    # ------------------------------------------------------------ queries
    def is_group_header(self, row: int) -> bool:
        return 0 <= row < len(self._rows) and self._rows[row][0] == "header"

    def get_group_info(self, row: int) -> Optional[dict]:
        if self.is_group_header(row):
            return self._rows[row][1]
        return None

    def get_header_rows(self) -> List[int]:
        return [r for r, (kind, _) in enumerate(self._rows) if kind == "header"]

    def get_shot_at(self, row: int):
        if 0 <= row < len(self._rows) and self._rows[row][0] == "shot":
            return self.sourceModel().index(self._rows[row][1], 0).data(SHOT_ROLE)
        return None

    def row_of_shot(self, shot) -> int:
        for r, (kind, payload) in enumerate(self._rows):
            if kind == "shot" and self.sourceModel().index(payload, 0).data(SHOT_ROLE) is shot:
                return r
        return -1

    # ------------------------------------------------------------ proxy API
    def index(self, row, column, parent=QModelIndex()):
        if parent.isValid() or not (0 <= row < len(self._rows)):
            return QModelIndex()
        if not (0 <= column < self.columnCount()):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index=QModelIndex()):
        return QModelIndex()

    def sibling(self, row, column, index):
        return self.index(row, column)

    def hasChildren(self, parent=QModelIndex()):
        return (not parent.isValid()) and bool(self._rows)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        source = self.sourceModel()
        return 0 if (parent.isValid() or source is None) else source.columnCount()

    def mapToSource(self, proxy_index):
        if not proxy_index.isValid() or not (0 <= proxy_index.row() < len(self._rows)):
            return QModelIndex()
        kind, payload = self._rows[proxy_index.row()]
        if kind != "shot":
            return QModelIndex()
        return self.sourceModel().index(payload, proxy_index.column())

    def mapFromSource(self, source_index):
        if not source_index.isValid():
            return QModelIndex()
        row = self._from_source.get(source_index.row(), -1)
        if row < 0:
            return QModelIndex()
        return self.index(row, source_index.column())

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if self.is_group_header(index.row()):
            info = self._rows[index.row()][1]
            if role == GROUP_HEADER_ROLE:
                return info
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 0:
                return info.get("title", "")
            return None
        return super().data(index, role)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        if self.is_group_header(index.row()):
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return super().flags(index)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or self.is_group_header(index.row()):
            return False
        return self.sourceModel().setData(self.mapToSource(index), value, role)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        source = self.sourceModel()
        if source is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return source.headerData(section, orientation, role)
        return None

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        source = self.sourceModel()
        if source is None:
            return
        with self.bulk():
            source.sort(column, order)
