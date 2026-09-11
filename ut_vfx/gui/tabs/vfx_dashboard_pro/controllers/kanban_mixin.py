import logging
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import StaleDataError

class DashboardKanbanMixin:
    """
    Mixin class to handle Kanban board interactions in VFX Dashboard.
    """
    def update_kanban(self):
        self.kanban_board.clear()
        for shot in self.displayed_shots:
            status = "Not Started"
            if shot.status.lower() in ["wip", "in progress", "review", "retake"]:
                status = "In Progress"
            elif shot.status.lower() in ["final", "approved", "done", "delivered"]:
                status = "Final"
            
            task_data = {
                "id": shot.id,
                "shot_code": shot.shot_name,
                "task_name": shot.description or shot.sow or "Shot Task",
                "assignee": shot.assigned_artist,
                "status": status,
                "original_status": shot.status 
            }
            self.kanban_board.add_task(task_data)

    def toggle_view_mode(self, checked):
        if not hasattr(self, "view_toggle_btn"):
            return
        if checked:
            self.view_toggle_btn.setText("List View")
            self.users_list.show() 
        else:
            self.view_toggle_btn.setText("Board View")
            self.users_list.hide() 
        self._update_empty_state()

    def on_kanban_status_changed(self, task_id, new_status_key):
        """Handle drag-and-drop from Kanban."""
        if not self.data_handler or not self.current_project:
            return
        if not self._user_can_edit():
            self._notify("Read-only: only Supervisor/Developer/Admin can edit shot data.", "warning", 5000)
            self.refresh_data()
            return

        db_status = "Ready"
        if new_status_key == "In Progress":
            db_status = "WIP"
        elif new_status_key == "Final":
            db_status = "Final"
        
        target_shot = next((s for s in self.all_shots if s.id == task_id), None)
        if not target_shot:
            logging.warning(f"Kanban update failed: shot ID {task_id} not found in memory.")
            return

        if target_shot.status == db_status:
            return

        logging.info(f"Kanban: moving {target_shot.shot_name} to {db_status}")
        
        try:
            success = self.data_handler.update_shot_field(
                target_shot.shot_name, 
                "status", 
                db_status, 
                target_shot.version
            )
            if success:
                target_shot.status = db_status
                target_shot.version += 1 
                self._mirror_shots_to_excel([target_shot])
                self.update_table() 
            else:
                self._notify("Could not update status. Data might be stale.", "warning", 5000)
                self.refresh_data()
        except StaleDataError as e:
            self._notify(str(e), "warning")
            self.refresh_data()
        except PermissionError as e:
            self._notify(str(e), "warning", 5000)
        except Exception as e:
            self._notify("Failed to update status.", "error", 6000, details=f"Failed to update status: {e}")

    def on_kanban_double_clicked(self, task_id):
        target_shot = next((s for s in self.all_shots if s.id == task_id), None)
        if target_shot:
            self.open_detail_dock(target_shot)
        else:
            logging.error(f"Error: Shot with ID {task_id} not found.")

    def on_task_assigned(self, task_id, username):
        if not self.data_handler or not self.current_project:
            return
        if not self._user_can_edit():
            self._notify("Read-only: only Supervisor/Developer/Admin can assign artists.", "warning", 5000)
            self.refresh_data()
            return

        target_shot = next((s for s in self.all_shots if s.id == task_id), None)
        if not target_shot:
            return

        logging.info(f"Assigning {username} to {target_shot.shot_name}")
        
        try:
            success = self.data_handler.update_shot_field(
                target_shot.shot_name, 
                "assigned_artist",
                username, 
                target_shot.version
            )
            
            if success:
                target_shot.assigned_artist = username
                target_shot.version += 1
                self._mirror_shots_to_excel([target_shot])
                self.update_table() 
                self._notify(f"Assigned {username} to {target_shot.shot_name}", "success", 3000)
            else:
                self._notify("Could not assign artist.", "warning", 5000)
        except StaleDataError as e:
            self._notify(str(e), "warning")
            self.refresh_data()
        except PermissionError as e:
            self._notify(str(e), "warning", 5000)
        except Exception as e:
            self._notify("Failed to assign artist.", "error", 6000, details=f"Failed to assign artist: {e}")
