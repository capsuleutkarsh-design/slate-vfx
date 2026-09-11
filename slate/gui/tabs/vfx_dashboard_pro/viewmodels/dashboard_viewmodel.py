"""
DashboardViewModel
==================
MVVM ViewModel for VFX Dashboard Pro.
Decouples filtering, sorting, sequence/status grouping, progress metrics,
and batch editing logic from PySide6 GUI widgets, enabling headless unit testing
and clean architectural separation.
"""

from typing import List, Dict, Any, Optional, Set, Callable
from collections import defaultdict
from PySide6.QtCore import QObject, Signal


class DashboardViewModel(QObject):
    """
    Manages state, filtering, and data transformations for VFX Dashboard.
    Emits signals when displayed shots, grouping, or metrics change.
    """

    # Signals
    shots_changed = Signal()
    metrics_updated = Signal(dict)
    groups_changed = Signal(dict)
    filter_applied = Signal(int)  # Count of displayed shots

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._all_shots: List[Any] = []
        self._displayed_shots: List[Any] = []
        self._search_text: str = ""
        self._status_filter: str = "All Status"
        self._group_by_mode: str = "None"
        self._artist_scope: Optional[str] = None
        self._header_filters: Dict[int, str] = {}
        self._groups: Dict[str, Dict[str, Any]] = {}
        self._collapsed_groups: Set[str] = set()

    # --- Property Accessors ---

    @property
    def all_shots(self) -> List[Any]:
        return self._all_shots

    @all_shots.setter
    def all_shots(self, shots: List[Any]):
        self._all_shots = list(shots or [])
        self.apply_filters()

    @property
    def displayed_shots(self) -> List[Any]:
        return self._displayed_shots

    @property
    def search_text(self) -> str:
        return self._search_text

    @property
    def status_filter(self) -> str:
        return self._status_filter

    @property
    def group_by_mode(self) -> str:
        return self._group_by_mode

    @property
    def groups(self) -> Dict[str, Dict[str, Any]]:
        return self._groups

    @property
    def artist_scope(self) -> Optional[str]:
        return self._artist_scope

    @artist_scope.setter
    def artist_scope(self, username: Optional[str]):
        self._artist_scope = username
        self.apply_filters()

    # --- Filtering & Search Logic ---

    def set_search_text(self, text: str):
        """Update search filter and re-apply."""
        cleaned = (text or "").strip().lower()
        if cleaned != self._search_text:
            self._search_text = cleaned
            self.apply_filters()

    def set_status_filter(self, status: str):
        """Update status dropdown filter and re-apply."""
        cleaned = status or "All Status"
        if cleaned != self._status_filter:
            self._status_filter = cleaned
            self.apply_filters()

    def set_header_filter(self, col_idx: int, filter_val: str):
        """Set column header filter value."""
        if not filter_val:
            self._header_filters.pop(col_idx, None)
        else:
            self._header_filters[col_idx] = str(filter_val).strip().lower()
        self.apply_filters()

    def clear_header_filters(self):
        self._header_filters.clear()
        self.apply_filters()

    def apply_filters(self):
        """
        Filters all_shots based on current search text, status, artist scope,
        and column header filters.
        """
        filtered = []
        for shot in self._all_shots:
            # 1. Artist Scoping (if restricted)
            if self._artist_scope:
                assigned = getattr(shot, "assigned_artist", "") or getattr(shot, "artist", "")
                if assigned.strip().lower() != self._artist_scope.lower():
                    continue

            # 2. Status Dropdown Filter
            status = getattr(shot, "status", "") or ""
            if self._status_filter != "All Status" and status != self._status_filter:
                continue

            # 3. Text Search (matches shot name, sequence, reel, sow, assigned artist)
            if self._search_text:
                name = str(getattr(shot, "name", "") or getattr(shot, "shot_name", "")).lower()
                seq = str(getattr(shot, "sequence", "") or "").lower()
                reel = str(getattr(shot, "reel", "") or "").lower()
                sow = str(getattr(shot, "sow", "") or "").lower()
                artist = str(getattr(shot, "assigned_artist", "") or getattr(shot, "artist", "")).lower()

                if not any(self._search_text in field for field in (name, seq, reel, sow, artist)):
                    continue

            filtered.append(shot)

        self._displayed_shots = filtered
        self._compute_grouping()
        self._compute_metrics()

        self.shots_changed.emit()
        self.filter_applied.emit(len(self._displayed_shots))

    # --- Grouping Engine ---

    def set_group_by_mode(self, mode: str):
        """Set grouping: 'None', 'Reel / Seq', 'Status', 'Artist', 'Priority'."""
        self._group_by_mode = mode or "None"
        self._compute_grouping()
        self.groups_changed.emit(self._groups)
        self.shots_changed.emit()

    def toggle_group_collapse(self, group_key: str) -> bool:
        """Toggle collapsed state of a group. Returns True if now collapsed."""
        if group_key in self._collapsed_groups:
            self._collapsed_groups.remove(group_key)
            collapsed = False
        else:
            self._collapsed_groups.add(group_key)
            collapsed = True
        self._compute_grouping()
        self.groups_changed.emit(self._groups)
        self.shots_changed.emit()
        return collapsed

    def is_group_collapsed(self, group_key: str) -> bool:
        return group_key in self._collapsed_groups

    def _compute_grouping(self):
        """Group displayed shots by active mode and calculate roll-up metrics."""
        self._groups.clear()
        if self._group_by_mode in ("None", "", None):
            return

        grouped = defaultdict(list)
        for shot in self._displayed_shots:
            if self._group_by_mode == "Reel / Seq":
                key = str(getattr(shot, "reel", "") or getattr(shot, "sequence", "") or "Unassigned").strip()
            elif self._group_by_mode == "Status":
                key = str(getattr(shot, "status", "") or "YTS").strip().upper()
            elif self._group_by_mode == "Artist":
                key = str(getattr(shot, "assigned_artist", "") or getattr(shot, "artist", "") or "Unassigned").strip()
            elif self._group_by_mode == "Priority":
                key = f"Priority {getattr(shot, 'priority', 3)}"
            else:
                key = "All"
            grouped[key].append(shot)

        for group_name, members in grouped.items():
            total = len(members)
            approved = sum(1 for s in members if str(getattr(s, "status", "")).upper() in ("APPROVED", "DONE"))
            frames = sum(int(getattr(s, "duration", 0) or getattr(s, "frame_count", 0) or 0) for s in members)
            bids = sum(float(getattr(s, "bid_days", 0) or 0.0) for s in members)
            pct = int((approved / total * 100) if total > 0 else 0)

            self._groups[group_name] = {
                "title": group_name,
                "count": total,
                "approved_count": approved,
                "approved_pct": pct,
                "total_frames": frames,
                "total_bids": bids,
                "is_collapsed": group_name in self._collapsed_groups,
                "shots": members
            }

    # --- Metrics Roll-Up ---

    def _compute_metrics(self):
        """Calculates project overview metrics from all_shots and displayed_shots."""
        total = len(self._all_shots)
        if total == 0:
            metrics = {
                "total_shots": 0,
                "approved_count": 0,
                "approved_pct": 0,
                "wip_count": 0,
                "retake_count": 0,
                "total_frames": 0,
                "total_bid_days": 0.0,
                "total_actual_days": 0.0,
            }
            self.metrics_updated.emit(metrics)
            return

        approved = sum(1 for s in self._all_shots if str(getattr(s, "status", "")).upper() in ("APPROVED", "DONE"))
        wip = sum(1 for s in self._all_shots if str(getattr(s, "status", "")).upper() == "WIP")
        retake = sum(1 for s in self._all_shots if str(getattr(s, "status", "")).upper() == "RETAKE")
        frames = sum(int(getattr(s, "duration", 0) or getattr(s, "frame_count", 0) or 0) for s in self._all_shots)
        bids = sum(float(getattr(s, "bid_days", 0) or 0.0) for s in self._all_shots)
        actuals = sum(float(getattr(s, "actual_days", 0) or 0.0) for s in self._all_shots)
        pct = int(round((approved / total) * 100))

        metrics = {
            "total_shots": total,
            "approved_count": approved,
            "approved_pct": pct,
            "wip_count": wip,
            "retake_count": retake,
            "total_frames": frames,
            "total_bid_days": bids,
            "total_actual_days": actuals,
        }
        self.metrics_updated.emit(metrics)

    # --- Batch Operations ---

    def apply_batch_update(self, shot_names: List[str], updates: Dict[str, Any]) -> int:
        """
        Applies batch field updates to all matching shots in all_shots.
        Returns count of modified shots.
        """
        if not shot_names or not updates:
            return 0

        target_set = set(shot_names)
        updated_count = 0

        for shot in self._all_shots:
            name = getattr(shot, "name", None) or getattr(shot, "shot_name", None)
            if name in target_set:
                for k, v in updates.items():
                    if hasattr(shot, k):
                        setattr(shot, k, v)
                shot._modified = True
                updated_count += 1

        if updated_count > 0:
            self.apply_filters()

        return updated_count
