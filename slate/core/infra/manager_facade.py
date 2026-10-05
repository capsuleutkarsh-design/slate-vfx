"""
What PostgresManager and SQLiteManager share word for word.

Each manager carried its own copy of these: the forwards to the repositories,
maintenance and the change history. A copy that is edited on one side only
drifts, and most tests run on SQLite - so they can pass on a copy the studio's
PostgreSQL never runs. They live here once. What really differs between the
two (connections, running statements, the schema) stays in each manager,
which sets project_repo, stock_repo, tracking_repo and user_repo.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .db_results import DatabaseUnavailableError


class ManagerFacade:
    # --- PROJECT MANAGEMENT ---

    def get_all_projects(self, limit: Optional[int] = 1000):
        return self.project_repo.get_all_projects(limit=limit)
        
    def get_all_projects_summary(self, limit: Optional[int] = 1000) -> List[Dict[str, Any]]:
        return self.project_repo.get_all_projects_summary(limit=limit)

    def record_project(self, name: str, template_used: str, target_directory: str, total_folders: int = 0) -> int:
        return self.project_repo.record_project(name, template_used, target_directory, total_folders)

    def start_operation(self, project_id: int, operation_type: str) -> int:
        return self.project_repo.start_operation(project_id, operation_type)

    def update_operation(self, op_id: int, duration: float, items: int, errors: int, success: bool) -> None:
        self.project_repo.update_operation(op_id, duration, items, errors, success)

    def record_task_detail(self, op_id: int, name: str, src: str, dst: str, size: int, duration: float, status: str, error: str = "") -> None:
        self.project_repo.record_task_detail(op_id, name, src, dst, size, duration, status, error)

    # --- MAINTENANCE ---

    def perform_maintenance(self, days_to_keep=30):
        # Postgres is robust, but cleaning old logs is still good
        try:
            cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            self.execute_query("DELETE FROM task_details WHERE timestamp < %s", (cutoff,), fetch="none")
            
            # VACUUM in Postgres cannot run inside a transaction block easily via execute_query
            # but usually autovacuum handles this. We can skip explicit vacuum for now.
        except Exception as e:
            logging.exception(f"Maintenance error: {e}")

    def cleanup_stale_sessions(self):
        end = datetime.now().isoformat()
        q = """
            UPDATE operations 
            SET end_time = %s, success = 0, errors = errors + 1 
            WHERE end_time IS NULL
        """
        self.execute_query(q, (end,), fetch="none")

    # --- STOCK LIBRARY ---

    def add_stock_asset(self, path: str, thumb_path: str = "", proxy_path: str = "", tags: Optional[List[str]] = None, metadata: dict = None) -> int:
        return self.stock_repo.add_stock_asset(path, thumb_path, proxy_path, tags, metadata)

    def add_stock_assets_batch(self, assets_list: List[Dict[str, Any]]) -> None:
        self.stock_repo.add_stock_assets_batch(assets_list)

    def update_stock_asset_paths(self, asset_id, thumb_path=None, proxy_path=None, file_path=None):
        self.stock_repo.update_stock_asset_paths(asset_id, thumb_path, proxy_path, file_path)

    def update_asset_tags(self, asset_id, new_tags):
        return self.stock_repo.update_asset_tags(asset_id, new_tags)

    def update_asset_metadata(self, asset_id, metadata_str, tags_str):
        return self.stock_repo.update_asset_metadata(asset_id, metadata_str, tags_str)

    def count_stock_assets(self, search_query=None, file_types=None, asset_ids=None) -> int:
        """How many assets match the filter, so the count agrees with the list."""
        return self.stock_repo.count_stock_assets(search_query, file_types, asset_ids)

    def list_stock_paths(self):
        """Just the paths, for the ingest to tell what it already holds."""
        return self.stock_repo.list_stock_paths()

    def get_stock_count(self) -> int:
        """Returns total number of assets in the stock_library table."""
        return self.stock_repo.get_stock_count()

    def get_all_stock_assets(self, limit: int = None, offset: int = 0, search_query: str = None, file_types: List[str] = None, asset_ids: List[str] = None) -> List[Dict]:
        return self.stock_repo.get_all_stock_assets(limit, offset, search_query, file_types, asset_ids)

    def get_stock_file_types(self) -> List[str]:
        """Returns list of unique file extensions in the library."""
        return self.stock_repo.get_stock_file_types()

    def get_stock_tags(self) -> List[str]:
        """Returns list of unique tags in the library."""
        return self.stock_repo.get_stock_tags()

    def remove_stock_asset(self, asset_id) -> bool:
        """Delete one stock asset by database id."""
        return self.stock_repo.remove_stock_asset(asset_id)

    def remove_stock_asset_by_path(self, file_path: str) -> bool:
        """Delete one stock asset by file path."""
        return self.stock_repo.remove_stock_asset_by_path(file_path)

    def clear_stock_library(self):
        return self.stock_repo.clear_stock_library()

    def clear_stock_assets(self):
        """Legacy compatibility alias."""
        return self.clear_stock_library()

    # --- DASHBOARD / TRACKING ---

    def save_tracking_project(self, code: str, name: str, config_json: str):
        return self.tracking_repo.save_tracking_project(code, name, config_json)

    def get_tracking_project(self, code: str) -> Optional[Dict]:
        return self.tracking_repo.get_tracking_project(code)

    def get_all_tracking_projects(self) -> List[Dict]:
        return self.tracking_repo.get_all_tracking_projects()

    def save_tracking_shots(self, project_code: str, shots_data: List[Tuple[str, str, int, str]]):
        return self.tracking_repo.save_tracking_shots(project_code, shots_data)

    def get_tracking_shots(self, project_code: str) -> List[Dict]:
        return self.tracking_repo.get_tracking_shots(project_code)

    def update_tracking_shot_safe(self, project_code: str, shot_name: str, data_json: str,
                                  current_version: int, reel: str = None) -> bool:
        # The reel is part of a shot's identity - two reels may hold a shot of
        # the same name. This wrapper used to drop it, so every dashboard save
        # against PostgreSQL failed outright with a TypeError. The repository
        # below has always accepted it.
        return self.tracking_repo.update_tracking_shot_safe(
            project_code, shot_name, data_json, current_version, reel=reel)

    def _get_tracking_tasks_columns(self) -> set:
        return self.tracking_repo._get_tracking_tasks_columns()

    def get_tracking_tasks(self, project_code: str) -> List[Dict]:
        return self.tracking_repo.get_tracking_tasks(project_code)

    def save_tracking_tasks(self, project_code: str, tasks_data: List[Dict]):
        return self.tracking_repo.save_tracking_tasks(project_code, tasks_data)

    def sync_users(self, users_dict: Dict[str, Any]):
        return self.user_repo.sync_users(users_dict)

    def get_user_profile_pic(self, username: str) -> Optional[str]:
        return self.user_repo.get_user_profile_pic(username)

    def update_user_profile_pic(self, username: str, path: str) -> bool:
        return self.user_repo.update_user_profile_pic(username, path)

    def get_user_id(self, name_or_user: str) -> Optional[int]:
        return self.user_repo.get_user_id(name_or_user)


    def log_change_event(self, project_code, entity_type, entity_id, user_id, action_type,
                         field, old_val, new_val, **shot):
        """
        One line of change history. user_id is the author's username (see
        change_history.py); shot may carry shot_id, shot_name, reel and
        department. Returns the WriteResult.
        """
        from .change_history import log_change
        return log_change(self, project_code, entity_type, entity_id, user_id, action_type,
                          field, old_val, new_val, **shot)

    def get_history(self, project_code=None, shot_name=None, limit=200, **shot):
        """History, newest first - see change_history.read_history()."""
        from .change_history import read_history
        try:
            return read_history(self, project_code, shot_name, limit, **shot)
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logging.exception(f"Failed to fetch history for project={project_code}, shot={shot_name}: {e}")
            return []
