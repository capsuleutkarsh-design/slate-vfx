"""
The board view of the dashboard.

A drop or a drag-to-assign on the board is an edit like any other: it waits
for the one "Save N changes", is counted, and Ctrl+Z takes it back. It used to
write to the database at once, with no undo, through a path that skipped the
department scope - a Roto lead could move a shot to Final from here.

The board is rebuilt only while it is on screen; filtering in the list view no
longer rebuilds 262 cards on every keystroke.
"""

import logging

from slate.core.domain import shot_status
from slate.core.infra.gate import Gate


class DashboardKanbanMixin:
    """Mixed into DashboardWidget."""

    NO_STATUS_KEY = ""

    def _board_visible(self) -> bool:
        board = getattr(self, "kanban_board", None)
        stack = getattr(self, "view_stack", None)
        return bool(board is not None and stack is not None and stack.currentWidget() is board)

    def _board_columns(self):
        present = {shot_status.canonical(s.status) for s in (self.displayed_shots or [])}
        columns = [(key, key, Gate.status_color(key)) for key in shot_status.WORKFLOW]
        extras = sorted(v for v in present if v and v not in shot_status.WORKFLOW)
        columns += [(key, key, Gate.status_color(key)) for key in extras]
        if "" in present:
            columns.append((self.NO_STATUS_KEY, shot_status.NO_STATUS, Gate.TEXT_DIM))
        return columns

    def _board_edit_state(self):
        """(editable, reason) for drag and drop on the board."""
        if not self._user_can_edit():
            return False, ""
        if self._is_department_scoped():
            return False, ("The board changes a shot's own status and artist, which is a "
                           "coordinator's job. Set your department's statuses in the list view.")
        return True, ""

    def update_kanban(self):
        board = getattr(self, "kanban_board", None)
        if board is None:
            return
        self._board_dirty = False
        self._board_shots = list(self.displayed_shots or [])
        board.set_columns(self._board_columns())
        editable, reason = self._board_edit_state()
        board.set_editable(editable, reason)
        board.clear()
        for index, shot in enumerate(self._board_shots):
            board.add_task({
                "id": index,
                "shot_code": shot.shot_name,
                "reel": shot.reel_episode,
                "task_name": shot.description or shot.sow or "",
                "assignee": shot.assigned_artist,
                "status": shot.status,
                "column": shot_status.canonical(shot.status),
                "modified": bool(getattr(shot, "_modified", False)),
            })
        self._refresh_people_list()

    def _refresh_people_list(self):
        users_list = getattr(self, "users_list", None)
        if users_list is None:
            return
        counts = {}
        for shot in self.all_shots or []:
            if shot_status.is_done(shot.status) or shot_status.is_omitted(shot.status):
                continue
            name = str(shot.assigned_artist or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        users_list.populate(self._get_user_list(), counts)

    def toggle_view_mode(self, checked):
        board_mode = bool(checked)
        if board_mode:
            editable, _ = self._board_edit_state()
            self.users_list.setVisible(editable)
        else:
            self.users_list.hide()
        self._update_empty_state()
        if board_mode and getattr(self, "_board_dirty", True):
            self.update_kanban()

    def _board_shot(self, task_id):
        shots = getattr(self, "_board_shots", None) or []
        if isinstance(task_id, int) and 0 <= task_id < len(shots):
            return shots[task_id]
        return None

    def on_kanban_status_changed(self, task_id, new_status_key):
        """A card was dropped on a column: that column's status, as a pending edit."""
        shot = self._board_shot(task_id)
        if shot is None or not self.current_project:
            return
        editable, reason = self._board_edit_state()
        if not editable:
            self._notify(reason or "You don't have permission to change a shot's status.", "warning")
            return
        new_status = "" if new_status_key == self.NO_STATUS_KEY else new_status_key
        if shot_status.canonical(shot.status) == shot_status.canonical(new_status):
            return
        old = shot.status
        changed = self.table_model.apply_edit(
            [shot], lambda s: setattr(s, "status", new_status),
            f"status change on {shot.shot_name}")
        if changed:
            self._notify(
                f"{shot.shot_name}: {shot_status.label(old)} → {shot_status.label(new_status)}. "
                "Not saved yet.", "info", 4000, action=self._undo_action())

    def on_kanban_double_clicked(self, task_id):
        shot = self._board_shot(task_id)
        if shot is not None:
            self.open_detail_dock(shot)
        else:
            logging.error("Board card %s has no shot behind it.", task_id)

    def on_task_assigned(self, task_id, username):
        """A person was dropped on a card: they become its artist, as a pending edit."""
        shot = self._board_shot(task_id)
        if shot is None or not self.current_project:
            return
        editable, reason = self._board_edit_state()
        if not editable:
            self._notify(reason or "You don't have permission to assign artists.", "warning")
            return
        if str(shot.assigned_artist or "") == str(username or ""):
            return
        changed = self.table_model.apply_edit(
            [shot], lambda s: setattr(s, "assigned_artist", username),
            f"artist change on {shot.shot_name}")
        if changed:
            self._notify(f"{username} assigned to {shot.shot_name}. Not saved yet.", "info", 4000,
                         action=self._undo_action())
