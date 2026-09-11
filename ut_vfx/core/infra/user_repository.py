import json
import logging
from datetime import datetime
from typing import Dict, Optional, Any
from psycopg2.extras import execute_values


class UserRepository:
    """User persistence methods extracted from PostgresManager."""

    def __init__(self, db):
        self.db = db

    def sync_users(self, users_dict: Dict[str, Any]):
        try:
            if not users_dict:
                return True
            timestamp = datetime.now().isoformat()
            
            sql = """
                INSERT INTO ut_users (username, display_name, roles, password_hash, job_title, profile_pic_path, last_synced)
                VALUES %s
                ON CONFLICT (username) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    roles = EXCLUDED.roles,
                    password_hash = CASE WHEN EXCLUDED.password_hash != '' THEN EXCLUDED.password_hash ELSE ut_users.password_hash END,
                    job_title = CASE WHEN EXCLUDED.job_title != '' THEN EXCLUDED.job_title ELSE ut_users.job_title END,
                    profile_pic_path = CASE WHEN EXCLUDED.profile_pic_path != '' THEN EXCLUDED.profile_pic_path ELSE ut_users.profile_pic_path END,
                    last_synced = EXCLUDED.last_synced
            """
            values = []
            for username, data in users_dict.items():
                roles = data.get("roles", [])
                if 'role' in data and not roles:
                    roles = [data['role']]
                elif not roles:
                    roles = ["Artist"]
                roles_str = json.dumps(roles if isinstance(roles, list) else [roles])
                values.append((
                    username,
                    data.get("display_name", ""),
                    roles_str,
                    data.get("password_hash", ""),
                    data.get("job_title", ""),
                    data.get("profile_pic_path", ""),
                    timestamp,
                ))
                
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    execute_values(cur, sql, values)
                    conn.commit()
            return True
        except Exception as e:
            logging.exception(f"Sync Users Failed: {e}")
            return False

    def get_user_profile_pic(self, username: str) -> Optional[str]:
        q = "SELECT profile_pic_path FROM ut_users WHERE username=%s"
        res = self.db.execute_query(q, (username,), fetch="one")
        return res['profile_pic_path'] if res else None

    def update_user_profile_pic(self, username: str, path: str) -> bool:
        q = "UPDATE ut_users SET profile_pic_path=%s WHERE username=%s"
        return (self.db.execute_query(q, (path, username), fetch="rowcount") or 0) > 0

    def get_user_id(self, name_or_user: str) -> Optional[int]:
        # Try username match
        q1 = "SELECT id FROM ut_users WHERE username=%s"
        res = self.db.execute_query(q1, (name_or_user,), fetch="one")
        if res: return res['id']
        
        # Try display name match
        q2 = "SELECT id FROM ut_users WHERE display_name ILIKE %s"
        res = self.db.execute_query(q2, (name_or_user,), fetch="one")
        if res: return res['id']
        return None

    def get_user_roles(self, username: str) -> list:
        q = "SELECT roles FROM ut_users WHERE username=%s"
        res = self.db.execute_query(q, (username,), fetch="one")
        if res and res['roles']:
            try:
                return json.loads(res['roles'])
            except Exception:
                pass
        return ["Artist"]
