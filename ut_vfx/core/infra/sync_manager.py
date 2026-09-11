import logging
import threading
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class SyncManager:
    """
    Two-Way Synchronization Engine between local SQLite fallback and remote PostgreSQL.
    Extracts offline edits from SQLite and pushes them to Postgres, then refreshes local DB.
    """

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.sync_lock = threading.RLock()
        self.is_syncing = False

    def trigger_sync(self, task_info=None):
        """
        Triggers the synchronization process.
        Returns a boolean indicating success.
        """
        if self.is_syncing:
            logger.warning("Sync already in progress.")
            return False

        with self.sync_lock:
            self.is_syncing = True
            
        try:
            success = self._perform_sync(task_info)
            return success
        except Exception as e:
            logger.exception(f"Sync failed: {e}")
            if task_info:
                task_info.status = "Failed"
                task_info.error_message = str(e)
            return False
        finally:
            with self.sync_lock:
                self.is_syncing = False

    def _perform_sync(self, task_info) -> bool:
        if task_info:
            task_info.status = "Initializing Sync..."
            task_info.progress = 0

        # We must instantiate the explicit managers
        from ut_vfx.core.infra.sqlite_manager import SQLiteManager
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        sqlite_db = SQLiteManager()
        postgres_db = PostgresManager()

        # Check Postgres Connection
        try:
            with postgres_db.get_connection() as conn:
                pass
        except Exception as e:
            logger.error("Cannot sync: PostgreSQL is unreachable.")
            if task_info:
                task_info.status = "Offline"
                task_info.error_message = "PostgreSQL Unreachable"
            return False

        # --- Phase 1: Push Local to Remote ---
        if task_info:
            task_info.status = "Pushing local changes..."
            task_info.progress = 20
        
        self._sync_table(sqlite_db, postgres_db, "stock_library", ["file_path"], "ingest_date")
        self._sync_table(sqlite_db, postgres_db, "tracking_shots", ["project_code", "shot_name"], "last_updated")
        self._sync_table(sqlite_db, postgres_db, "users", ["username"], "created_at")

        # --- Phase 2: Pull Remote to Local ---
        if task_info:
            task_info.status = "Pulling remote changes..."
            task_info.progress = 60

        self._sync_table(postgres_db, sqlite_db, "stock_library", ["file_path"], "ingest_date")
        self._sync_table(postgres_db, sqlite_db, "tracking_shots", ["project_code", "shot_name"], "last_updated")
        self._sync_table(postgres_db, sqlite_db, "users", ["username"], "created_at")

        if task_info:
            task_info.status = "Completed"
            task_info.progress = 100

        return True

    def _sync_table(self, source_db, target_db, table_name: str, unique_keys: List[str], timestamp_col: str):
        """
        Generic single-table sync function.
        Reads all records from source, and UPSERTs into target.
        """
        try:
            source_records = source_db.execute_query(f"SELECT * FROM {table_name}")
            if not source_records:
                return

            if isinstance(source_records[0], tuple):
                # We need column names
                # For simplicity, if we don't have row dicts, we fetch them via a raw wrapper
                logger.warning(f"Table {table_name} returned tuples instead of dicts, skipping generic sync.")
                return

            for record in source_records:
                record_dict = dict(record) if not isinstance(record, dict) else record
                if 'id' in record_dict:
                    del record_dict['id'] # Don't sync internal IDs
                
                # Check if exists in target
                where_clauses = [f"{k} = ?" for k in unique_keys]
                where_vals = [record_dict.get(k) for k in unique_keys]
                
                # We need to translate '?' to '%s' if target is postgres
                from ut_vfx.core.infra.postgres_manager import PostgresManager
                is_pg = isinstance(target_db, PostgresManager)
                placeholder = "%s" if is_pg else "?"
                
                where_str = " AND ".join([f"{k} = {placeholder}" for k in unique_keys])
                query = f"SELECT {timestamp_col} FROM {table_name} WHERE {where_str}"
                
                target_result = target_db.execute_query(query, tuple(where_vals))
                
                should_update = False
                should_insert = False
                
                if target_result:
                    # Exists, check timestamp
                    target_ts = target_result[0][0] if isinstance(target_result[0], tuple) else target_result[0].get(timestamp_col)
                    source_ts = record_dict.get(timestamp_col)
                    
                    if source_ts and target_ts:
                        # simple string comparison works for ISO dates
                        if str(source_ts) > str(target_ts):
                            should_update = True
                else:
                    should_insert = True
                
                if should_insert:
                    cols = list(record_dict.keys())
                    vals = [record_dict[c] for c in cols]
                    placeholders = ", ".join([placeholder] * len(cols))
                    col_str = ", ".join(cols)
                    ins_query = f"INSERT INTO {table_name} ({col_str}) VALUES ({placeholders})"
                    target_db.execute_write(ins_query, tuple(vals))
                    
                elif should_update:
                    cols = list(record_dict.keys())
                    vals = [record_dict[c] for c in cols]
                    set_str = ", ".join([f"{c} = {placeholder}" for c in cols])
                    vals.extend(where_vals)
                    upd_query = f"UPDATE {table_name} SET {set_str} WHERE {where_str}"
                    target_db.execute_write(upd_query, tuple(vals))
                    
        except Exception as e:
            logger.error(f"Error syncing table {table_name}: {e}")
