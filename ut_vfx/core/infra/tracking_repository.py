import json
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from psycopg2.extras import execute_values


def _reel_of(data_json) -> str:
    """
    A shot's reel, read out of its stored JSON.

    The reel is part of a shot's identity, and it already travels inside the
    shot payload - so the database layer lifts it out rather than every caller
    having to pass it separately.
    """
    try:
        data = json.loads(data_json) if isinstance(data_json, str) else (data_json or {})
        return str(data.get("reel_episode") or "").strip()
    except Exception:
        return ""



class TrackingRepository:
    """Tracking project, shots, and tasks persistence methods extracted from PostgresManager."""

    def __init__(self, db):
        self.db = db

    def save_tracking_project(self, code: str, name: str, config_json: str) -> bool:
        """
        Create or update a project. Returns whether it was actually written.

        Two bugs lived here. The `active` column is an integer, and this passed
        a Python boolean - PostgreSQL refuses that outright, while SQLite quietly
        accepts it as 1. And the result was thrown away, so the refusal was
        silent: creating a project on PostgreSQL did nothing at all and said
        nothing about it.
        """
        q = """
            INSERT INTO tracking_projects (code, name, config_json, active)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                config_json = EXCLUDED.config_json,
                active = EXCLUDED.active,
                last_updated = CURRENT_TIMESTAMP
        """
        return bool(self.db.execute_update(q, (code, name, config_json, 1)))

    def get_tracking_project(self, code: str) -> Optional[Dict]:
        q = "SELECT config_json FROM tracking_projects WHERE code=%s"
        res = self.db.execute_query(q, (code,), fetch="one")
        if res and res.get('config_json'):
            val = res['config_json']
            return json.loads(val) if isinstance(val, str) else val
        return None

    def get_all_tracking_projects(self) -> List[Dict]:
        q = """
            SELECT config_json
            FROM tracking_projects
            WHERE LOWER(COALESCE(active::text, '')) IN ('1', 't', 'true', 'y', 'yes')
            ORDER BY code
        """
        rows = self.db.execute_query(q) or []
        res = []
        for r in rows:
            val = r.get('config_json')
            if val:
                res.append(json.loads(val) if isinstance(val, str) else val)
        return res

    def delete_tracking_project(self, code: str) -> bool:
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM tracking_tasks WHERE project_code=%s", (code,))
                    cur.execute("DELETE FROM tracking_shots WHERE project_code=%s", (code,))
                    cur.execute("DELETE FROM tracking_projects WHERE code=%s", (code,))
                    cur.execute("DELETE FROM change_history WHERE project_code=%s", (code,))
                    conn.commit()
            return True
        except Exception as e:
            logging.exception(f"Delete Failed: {e}")
            return False

    def save_tracking_shots(self, project_code: str, shots_data: List[Tuple[str, str, int, str]]):
        if not shots_data: return
        try:
            timestamp = datetime.now().isoformat()
            sql = """
                INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, data_json, last_updated, version)
                VALUES %s
                ON CONFLICT (project_code, reel, shot_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    data_json = EXCLUDED.data_json,
                    last_updated = EXCLUDED.last_updated,
                    version = tracking_shots.version + 1
            """
            values = [(project_code, _reel_of(s[3]), s[0], s[1], s[2], s[3], timestamp, 1)
                      for s in shots_data]
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    execute_values(cur, sql, values)
                    conn.commit()
            return True
        except Exception as e:
            logging.exception(f"Save Shots Failed: {e}")
            return False

    def get_tracking_shots(self, project_code: str) -> List[Dict]:
        q = "SELECT id, data_json, version FROM tracking_shots WHERE project_code=%s"
        rows = self.db.execute_query(q, (project_code,)) or []
        results = []
        for r in rows:
            val = r.get('data_json')
            if val:
                d = json.loads(val) if isinstance(val, str) else val
                d['version'] = r['version']
                d['id'] = r['id']
                results.append(d)
        return results

    def update_tracking_shot_safe(self, project_code: str, shot_name: str, data_json: str, current_version: int, reel: str = None) -> bool:
        timestamp = datetime.now().isoformat()
        q = """
            UPDATE tracking_shots
            SET data_json=%s, version=version+1, last_updated=%s
            WHERE project_code=%s AND reel=%s AND shot_name=%s AND version=%s
        """
        if reel is None:
            reel = _reel_of(data_json)
        return (self.db.execute_query(
            q, (data_json, timestamp, project_code, reel, shot_name, current_version),
            fetch="rowcount") or 0) > 0

    def _get_tracking_tasks_columns(self) -> set:
        cache = getattr(self, "_tracking_tasks_columns_cache", None)
        if cache:
            return cache

        rows = self.db.execute_query(
            "SELECT column_name FROM information_schema.columns WHERE table_name='tracking_tasks'"
        ) or []
        cols = {r["column_name"] for r in rows if isinstance(r, dict) and r.get("column_name")}
        self._tracking_tasks_columns_cache = cols
        return cols

    def get_tracking_tasks(self, project_code: str) -> List[Dict]:
        cols = self._get_tracking_tasks_columns()
        if "artist_name" in cols:
            artist_select = "t.artist_name AS artist"
        elif "artist" in cols:
            artist_select = "t.artist AS artist_name"
        else:
            artist_select = "NULL AS artist"

        q = """
            SELECT t.*, {artist_select}
            FROM tracking_tasks t
            JOIN tracking_shots s ON t.shot_id = s.id
            WHERE s.project_code = %s
        """.format(artist_select=artist_select)
        rows = self.db.execute_query(q, (project_code,)) or []
        return [dict(r) for r in rows]

    def save_tracking_tasks(self, project_code: str, tasks_data: List[Dict]):
        if not tasks_data: return
        try:
            cols = self._get_tracking_tasks_columns()
            artist_col = "artist" if "artist" in cols else "artist_name"
            has_project_code = "project_code" in cols

            insert_columns = ["shot_id"]
            if has_project_code:
                insert_columns.append("project_code")
            insert_columns.extend(["department", "status", artist_col, "artist_id", "bid_days", "target_date"])
            conflict_target = "(project_code, shot_id, department)" if has_project_code else "(shot_id, department)"

            update_assignments = [
                "status = EXCLUDED.status",
                f"{artist_col} = EXCLUDED.{artist_col}",
                "artist_id = EXCLUDED.artist_id",
                "bid_days = EXCLUDED.bid_days",
                "target_date = EXCLUDED.target_date",
            ]
            if has_project_code:
                update_assignments.append("project_code = EXCLUDED.project_code")

            sql = """
                INSERT INTO tracking_tasks ({columns})
                VALUES %s
                ON CONFLICT {conflict_target} DO UPDATE SET
                    {updates}
            """.format(
                columns=", ".join(insert_columns),
                conflict_target=conflict_target,
                updates=", ".join(update_assignments),
            )
            values = []
            for t in tasks_data:
                row_values = [t["shot_id"]]
                if has_project_code:
                    row_values.append(project_code)
                row_values.extend(
                    [
                        t["department"],
                        t.get("status", ""),
                        t.get("artist", t.get("artist_name", "")),
                        t.get("artist_id"),
                        t.get("bid_days", 0.0),
                        t.get("target", t.get("target_date", "")),
                    ]
                )
                values.append(tuple(row_values))
            
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    execute_values(cur, sql, values)
                    conn.commit()
            return True
        except Exception as e:
            logging.exception(f"Save Tasks Failed: {e}")
            return False
