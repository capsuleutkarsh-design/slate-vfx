"""
What is on screen: search, status filter, scope, column filters and the
Filters dialog, applied through the filter proxy (ui/shot_proxy_models.py).

A filter change used to rebuild the whole grid model and all 262 board cards
on the UI thread (0.4 s a keystroke). Now the proxy re-checks its rows; the
board is rebuilt only while it is on screen.

Search looks in every column - shot, reel, status, artists, scope of work,
type, target, version, priority, every department's status and artist, and
the description - and ignores leading/trailing spaces. The status filter lists
the workflow in order, matches 'Ready' and 'READY' as one, and has a "No
status" entry. Whatever narrows the grid is shown in the "N filters on -
Clear" chip, and a project switch starts clean.
"""

from typing import List, Optional

from PySide6.QtCore import Qt

from slate.core.domain import shot_status
from slate.core.domain.departments import load_departments
from slate.gui.tabs.vfx_dashboard_pro.ui.header_filter_view import BLANK_VALUE

# The Filters dialog's fields, mapped to what they read on a shot. Every field
# offered here resolves to something real - 'Client Feedback contains warmer'
# used to look for an attribute shots do not have and matched nothing.
ADVANCED_FIELDS = {
    "Shot name": lambda s: s.shot_name,
    "Reel / episode": lambda s: s.reel_episode,
    "Status": lambda s: s.status,
    "Assigned artist": lambda s: s.assigned_artist,
    "Any department artist": lambda s: " ".join(d.artist or "" for d in s.departments.values()),
    "Any department status": lambda s: " ".join(d.status or "" for d in s.departments.values()),
    "Priority": lambda s: shot_status.priority_label(s.priority),
    "Type": lambda s: s.shot_type,
    "Scope of work": lambda s: s.sow,
    "Target": lambda s: s.target,
    "Version": lambda s: s.curr_version,
    "Description": lambda s: s.description,
    "Notes": lambda s: s.notes,
    "Client feedback": lambda s: " ".join(e.text or "" for e in s.feedback_client),
    "Director feedback": lambda s: " ".join(e.text or "" for e in s.feedback_director),
    "Internal feedback": lambda s: " ".join(e.text or "" for e in s.feedback_internal),
}

# Names the dialog used before, so saved rules keep working.
_OLD_FIELD_NAMES = {
    "Shot Code": "Shot name", "Sequence": "Reel / episode", "Assigned Artist": "Assigned artist",
    "Client Feedback": "Client feedback", "Internal Comment": "Internal feedback",
    "Client Status": "Status",
}


def advanced_value(shot, field: str) -> str:
    getter = ADVANCED_FIELDS.get(_OLD_FIELD_NAMES.get(field, field))
    if getter is None:
        return ""
    try:
        return str(getter(shot) or "")
    except Exception:
        return ""


def advanced_values(shot, field: str) -> List[str]:
    """
    Every way a field's value is written: the grid shows '24 Dec 2026' and
    'Low' while the shot stores '2026-12-24' and 3, and a rule may use either.
    """
    name = _OLD_FIELD_NAMES.get(field, field)
    texts = [advanced_value(shot, name)]
    if name == "Target" and shot.target:
        from slate.core.domain.dates import format_date
        texts += [str(shot.target), format_date(shot.target)]
    elif name == "Priority":
        texts.append(str(shot.priority))
    return [t.strip().lower() for t in texts if t is not None]


# The operators as the dialog writes them now, and as older saved rules did.
OPERATOR_NAMES = {"Contains": "contains", "Equals": "is", "Not Equals": "is not",
                  "Does Not Contain": "does not contain", "Is Empty": "is empty",
                  "Is Not Empty": "is not empty"}


def rule_passes(shot, rule) -> bool:
    op = OPERATOR_NAMES.get(rule.get("operator"), rule.get("operator"))
    value = str(rule.get("value", "")).strip().lower()
    texts = advanced_values(shot, rule.get("field", ""))
    if op == "is empty":
        return not any(texts)
    if op == "is not empty":
        return any(texts)
    if op == "is":
        return value in texts
    if op == "is not":
        return value not in texts
    if op == "contains":
        return any(value in t for t in texts)
    if op == "does not contain":
        return not any(value in t for t in texts)
    return True


def search_text_of(shot) -> str:
    """Everything a person might search a shot by, in one lower-case string."""
    from slate.core.domain.dates import format_date
    parts = [
        shot.shot_name, shot.reel_episode, shot.status, shot_status.label(shot.status),
        shot.assigned_artist, shot.sow, shot.shot_type, shot.target, format_date(shot.target),
        shot.curr_version, shot.description, shot.in_os, shot.scan_status, shot.edit_status,
        shot_status.priority_label(shot.priority),
    ]
    for dept in getattr(shot, "departments", {}).values():
        parts.extend([dept.status, dept.artist, dept.target])
    return " ".join(str(p) for p in parts if p).lower()


class DashboardFilterMixin:
    """Mixed into DashboardWidget."""

    # ------------------------------------------------------------ the rule
    def _status_filter_value(self):
        combo = getattr(self, "status_filter", None)
        if combo is None or combo.currentIndex() <= 0:
            return None
        return combo.currentData()

    def _scope_mode(self) -> str:
        combo = getattr(self, "scope_combo", None)
        return (combo.currentData() if combo is not None else None) or "all"

    @staticmethod
    def _pick_key(shot):
        """A shot's database id (it survives a live update swapping the object), or the object."""
        sid = getattr(shot, "id", -1)
        return int(sid) if sid is not None and int(sid) >= 0 else ("obj", id(shot))

    def _shot_passes_filters(self, shot) -> bool:
        # 'Show only' narrows what the other filters let through; it does not replace them.
        only = getattr(self, "_only_shots", None)
        if only and self._pick_key(shot) not in only:
            return False

        status = self._status_filter_value()
        if status is not None and shot_status.canonical(shot.status) != status:
            return False

        scope = self._scope_mode()
        if scope == "my_shots":
            identities = self._artist_identity_candidates()
            try:
                names = shot.get_all_artists()
            except Exception:
                names = [getattr(shot, "assigned_artist", "")]
            if not any(str(n or "").strip().lower() in identities for n in names if n):
                return False
        elif str(scope).startswith("dept:"):
            if not self._has_department_work(shot, str(scope).split(":", 1)[1]):
                return False

        needle = getattr(self, "_search_needle", "")
        if needle and needle not in search_text_of(shot):
            return False

        header = getattr(self, "header_view", None)
        if header is not None and header.active_filters:
            model = self.table_model
            for key, allowed in header.active_filters.items():
                column = model.column_index(key)
                if column < 0:
                    continue
                text = str(model.COLUMNS[column][2](shot) or "").strip()
                if text in ("", "-"):
                    text = BLANK_VALUE
                if text not in allowed:
                    return False

        rules = getattr(self, "advanced_query_rules", None) or []
        if rules:
            match_type = getattr(self, "advanced_query_match_type", "AND")
            results = (rule_passes(shot, rule) for rule in rules)
            if match_type == "OR":
                if not any(results):
                    return False
            elif not all(results):
                return False
        return True

    @staticmethod
    def _has_department_work(shot, family: str) -> bool:
        for dept_def in load_departments():
            if dept_def.family != family:
                continue
            dept = shot.dept(dept_def.key)
            st = shot_status.normalise(getattr(dept, "status", ""))
            if st and st not in ("N/A", "OMIT", "-", "NONE"):
                return True
            # A shot assigned to this department, or bid for it, counts as its
            # work even before anybody has set a status.
            if str(getattr(dept, "artist", "") or "").strip():
                return True
            try:
                if float(getattr(dept, "bid_days", 0) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        return False

    # ------------------------------------------------------------ inputs
    def populate_filters(self):
        """The status list: the workflow in order, 'No status', and anything else stored."""
        combo = getattr(self, "status_filter", None)
        if combo is None:
            return
        current = self._status_filter_value()
        present = {shot_status.canonical(s.status) for s in (self.all_shots or [])}
        extras = sorted(v for v in present if v and v not in shot_status.WORKFLOW)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All statuses", None)
        for value in list(shot_status.WORKFLOW) + extras:
            combo.addItem(shot_status.label(value), value)
            combo.setItemData(combo.count() - 1, shot_status.describe(value),
                              Qt.ItemDataRole.ToolTipRole)
        combo.addItem(shot_status.NO_STATUS, "")
        index = 0
        if current is not None:
            for i in range(1, combo.count()):
                if combo.itemData(i) == current:
                    index = i
                    break
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    # Kept for older callers.
    def update_combo(self, combo, items, default_text):
        if not combo:
            return
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(default_text)
        combo.addItems(items)
        if current in items:
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def on_status_filter_changed(self, *_):
        self.apply_filters()

    def on_stat_clicked(self, status):
        """A counter was clicked: show only that status, or (None) all of them."""
        combo = self.status_filter
        if status is None:
            combo.setCurrentIndex(0)
            return
        for i in range(1, combo.count()):
            if combo.itemData(i) == status:
                combo.setCurrentIndex(i)
                return

    def on_header_filter_changed(self, *_):
        self.apply_filters()

    def column_filter_values(self, key: str) -> List[str]:
        """Every value of a column across the whole project, in natural order."""
        model = self.table_model
        column = model.column_index(key)
        if column < 0:
            return []
        getter = model.COLUMNS[column][2]
        found = {}
        blank = False
        for shot in self.all_shots or []:
            text = str(getter(shot) or "").strip()
            if text in ("", "-"):
                blank = True
                continue
            if text not in found:
                found[text] = model._sort_value(shot, key)
        def order(item):
            text, key_value = item
            if key_value is None:
                return (1, "", text.lower())
            if isinstance(key_value, (int, float)):
                return (0, f"{key_value:020.4f}", text.lower())
            return (0, str(key_value), text.lower())
        values = [text for text, _ in sorted(found.items(), key=order)]
        if blank:
            values.append(BLANK_VALUE)
        return values

    def on_project_data_updated(self):
        """
        Called by PollWorker - the fallback when the change feed is not
        working - when something in the project changed. It cannot say what,
        so the project is read once and merged: unsaved edits are kept and the
        selection stays (see live_update_mixin).
        """
        sender = self.sender()
        if sender is not None and getattr(self, "poll_worker", None) and sender is not self.poll_worker:
            return
        if getattr(self, "_is_closing", False):
            return
        self.log("Merging an external update...")
        self._queue_live_full()

    def _schedule_filter_update(self):
        """Triggered by search input text changes, debounced to avoid UI freezes."""
        if hasattr(self, '_search_debounce_timer'):
            self._search_debounce_timer.start()
        else:
            self.apply_filters()

    # ------------------------------------------------------------ applying
    def active_filter_count(self) -> int:
        count = 0
        if self._search_needle_now():
            count += 1
        if self._status_filter_value() is not None:
            count += 1
        header = getattr(self, "header_view", None)
        if header is not None:
            count += len(header.active_filters)
        count += len(getattr(self, "advanced_query_rules", None) or [])
        if getattr(self, "_only_shots", None):
            count += 1
        return count

    def _search_needle_now(self) -> str:
        box = getattr(self, "search_input", None)
        return " ".join((box.text() if box is not None else "").split()).lower()

    def apply_filters(self):
        if getattr(self, "filter_proxy", None) is None:
            return
        self._search_needle = self._search_needle_now()
        with self.group_model.bulk():
            self.filter_proxy.refilter()
        self._after_filter()

    def _after_filter(self):
        self.displayed_shots = self.filter_proxy.shots()
        self._apply_table_spans()
        if getattr(self, "stats_widget", None) is not None:
            self.stats_widget.update_stats(self.displayed_shots)
            self.stats_widget.set_active(self._status_filter_value())
        self._update_filter_chip()
        self._update_empty_state()
        self._update_shown_count()
        self._board_dirty = True
        if self._board_visible():
            self.update_kanban()
        fit = getattr(self, "_fit_toolbar", None)
        if fit is not None:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, fit)

    def _update_filter_chip(self):
        chip = getattr(self, "filter_chip", None)
        if chip is None:
            return
        count = self.active_filter_count()
        if count:
            if getattr(self, "_only_shots", None):
                chip.setText(f"Showing {len(self._only_shots)} picked shot(s) · Clear")
            else:
                chip.setText(f"{count} filter{'s' if count != 1 else ''} on · Clear")
            chip.show()
        else:
            chip.hide()
        button = getattr(self, "advanced_query_btn", None)
        if button is not None:
            rules = len(getattr(self, "advanced_query_rules", None) or [])
            button.setText(f"Filters ({rules})" if rules else "Filters…")

    def clear_all_filters(self, apply: bool = True):
        """Search, status, column filters, Filters rules and 'show only': all off."""
        self._only_shots = None
        box = getattr(self, "search_input", None)
        if box is not None:
            # Not blocked: the box's own clear button must see the change.
            box.clear()
            timer = getattr(self, "_search_debounce_timer", None)
            if timer is not None:
                timer.stop()
        combo = getattr(self, "status_filter", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        header = getattr(self, "header_view", None)
        if header is not None:
            header.clear_filters(emit=False)
        frozen = getattr(self, "frozen_header", None)
        if frozen is not None:
            frozen.viewport().update()
        self.advanced_query_rules = []
        if apply:
            self.apply_filters()

    def show_only_shots(self, shots, label: str = ""):
        """Narrow the grid to these shots (removable from the filter chip)."""
        self._only_shots = {self._pick_key(s) for s in shots or []}
        self.apply_filters()

    def _update_shown_count(self):
        label = getattr(self, "shown_label", None)
        if label is None:
            return
        if not getattr(self, "current_project", None):
            label.setText("")
            return
        shown = len(self.displayed_shots or [])
        total = len(self.all_shots or [])
        label.setText(f"Showing {shown} of {total} shots" if shown != total
                      else f"{total} shot{'s' if total != 1 else ''}")
