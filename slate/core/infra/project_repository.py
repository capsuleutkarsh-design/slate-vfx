from datetime import datetime
from typing import Any, Dict, List, Optional


class ProjectRepository:
    """Project/operation persistence methods extracted from PostgresManager."""

    DEFAULT_PROJECT_LIMIT = 1000
    MAX_PROJECT_LIMIT = 5000

    def __init__(self, db):
        self.db = db

    def _sanitize_limit(self, limit: Optional[int], default: int = DEFAULT_PROJECT_LIMIT) -> int:
        try:
            safe_limit = max(1, int(limit))
        except (TypeError, ValueError):
            safe_limit = default
        return min(safe_limit, self.MAX_PROJECT_LIMIT)

    def get_all_projects(self, limit: Optional[int] = DEFAULT_PROJECT_LIMIT) -> List[Dict[str, Any]]:
        safe_limit = self._sanitize_limit(limit)

        q = "SELECT * FROM projects ORDER BY created_at DESC LIMIT %s"
        return self.db.execute_query(q, (safe_limit,)) or []

    def get_all_projects_summary(self, limit: Optional[int] = DEFAULT_PROJECT_LIMIT) -> List[Dict[str, Any]]:
        safe_limit = self._sanitize_limit(limit)
        q = "SELECT id, name, created_at FROM projects ORDER BY id DESC LIMIT %s"
        return [dict(r) for r in (self.db.execute_query(q, (safe_limit,)) or [])]

    def record_project(
        self,
        name: str,
        template_used: str,
        target_directory: str,
        total_folders: int = 0,
    ) -> int:
        q = """
            INSERT INTO projects (name, template_used, target_directory, total_folders)
            VALUES (%s, %s, %s, %s) RETURNING id
        """
        res = self.db.execute_query(
            q,
            (name, template_used, str(target_directory), total_folders),
            fetch="lastrowid",
        )
        return res or 0

    def start_operation(self, project_id: int, operation_type: str) -> int:
        start = datetime.now().isoformat()
        q = """
            INSERT INTO operations (project_id, operation_type, start_time)
            VALUES (%s, %s, %s) RETURNING id
        """
        res = self.db.execute_query(q, (project_id, operation_type, start), fetch="lastrowid")
        return res or 0

    def update_operation(self, op_id: int, duration: float, items: int, errors: int, success: bool) -> None:
        if not op_id:
            return
        end = datetime.now().isoformat()
        q = """
            UPDATE operations
            SET end_time=%s, duration=%s, items_processed=%s, errors=%s, success=%s
            WHERE id=%s
        """
        self.db.execute_query(q, (end, duration, items, errors, int(bool(success)), op_id), fetch="none")

    def record_task_detail(
        self,
        op_id: int,
        name: str,
        src: str,
        dst: str,
        size: int,
        duration: float,
        status: str,
        error: str = "",
    ) -> None:
        q = """
            INSERT INTO task_details (operation_id, item_name, source_path, dest_path, file_size, duration, status, error_msg)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        self.db.execute_query(
            q,
            (op_id, name, str(src), str(dst), size, duration, status, error),
            fetch="none",
        )

    def record_task_details(self, rows) -> None:
        """
        Many task rows in a few statements: a 5,000-frame ingest used to make
        5,000 round trips to the database for its bookkeeping alone.

        Each row is (op_id, name, src, dst, size, duration, status[, error]).
        """
        rows = list(rows or [])
        columns = "(operation_id, item_name, source_path, dest_path, file_size, duration, status, error_msg)"
        for start in range(0, len(rows), 100):
            chunk = rows[start:start + 100]
            params = []
            for row in chunk:
                row = tuple(row) + ("",) * max(0, 8 - len(row))
                params.extend((row[0], row[1], str(row[2]), str(row[3]), row[4], row[5], row[6], row[7] or ""))
            values = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(chunk))
            self.db.execute_query(
                f"INSERT INTO task_details {columns} VALUES {values}", tuple(params), fetch="none")
