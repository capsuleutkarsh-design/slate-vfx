import requests
import logging
import json
from typing import List, Dict, Any, Optional, Tuple
from .global_config import GlobalConfig

class _ApiClientProxy:
    """
    Drop-in replacement for DatabaseManager that routes requests through the FastAPI server.
    """
    def __init__(self, base_url=None):
        host = GlobalConfig.get("db_host", "127.0.0.1")
        self.base_url = base_url or f"http://{host}:8000/api"
        self.active_mode = "api"
        self.fallback_used = False
        self.token = None

    def authenticate(self, username: str) -> bool:
        """Authenticate with the API and store the JWT token."""
        try:
            res = requests.post(f"{self.base_url}/users/login", data={"username": username, "password": "x"}, timeout=3)
            if res.status_code == 200:
                self.token = res.json().get("access_token")
                return True
            return False
        except Exception as e:
            logging.error(f"API Error authenticate: {e}")
            return False

    def _get_headers(self):
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def ping_sync(self):
        try:
            res = requests.get(f"{self.base_url.replace('/api', '')}/", timeout=2)
            return res.status_code == 200
        except Exception:
            return False

    def get_runtime_status(self):
        return {"mode": "api", "status": "connected" if self.ping_sync() else "disconnected"}

    def get_user_id(self, username: str) -> Optional[int]:
        try:
            res = requests.get(f"{self.base_url}/users", params={"username": username}, timeout=2)
            if res.status_code == 200:
                return res.json().get("user_id")
            return None
        except Exception as e:
            logging.error(f"API Error get_user_id: {e}")
            return None

    def sync_users(self, users_dict: Dict[str, Any]) -> bool:
        try:
            res = requests.post(f"{self.base_url}/users/sync", json=users_dict, timeout=5)
            return res.status_code == 200
        except Exception as e:
            logging.error(f"API Error sync_users: {e}")
            return False

    def get_user_profile_pic(self, username: str) -> Optional[str]:
        try:
            res = requests.get(f"{self.base_url}/users/{username}/profile_pic", headers=self._get_headers(), timeout=2)
            if res.status_code == 200:
                return res.json().get("profile_pic_path")
            return None
        except Exception:
            return None

    def update_user_profile_pic(self, username: str, path: str) -> bool:
        try:
            res = requests.put(f"{self.base_url}/users/{username}/profile_pic", headers=self._get_headers(), params={"path": path}, timeout=2)
            return res.status_code == 200
        except Exception:
            return False

    def get_tracking_shots(self, project_code: str) -> List[Any]:
        try:
            res = requests.get(f"{self.base_url}/shots/{project_code}", headers=self._get_headers(), timeout=3)
            if res.status_code == 200:
                return res.json().get("shots", [])
            return []
        except Exception as e:
            logging.error(f"API Error get_tracking_shots: {e}")
            return []

    def save_tracking_shots(self, project_code: str, batch_data: List[Tuple[str, str, int, str]]) -> bool:
        try:
            payload = [{"shot_name": s[0], "status": s[1], "priority": s[2], "data_json": s[3]} for s in batch_data]
            res = requests.post(f"{self.base_url}/shots/{project_code}/batch", headers=self._get_headers(), json=payload, timeout=5)
            return res.status_code == 200
        except Exception as e:
            logging.error(f"API Error save_tracking_shots: {e}")
            return False

    def get_connection(self):
        """Mock connection context manager for legacy code that hasn't fully migrated."""
        class MockConnection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def cursor(self):
                class MockCursor:
                    def execute(self, *args): pass
                    def fetchall(self): return []
                    def fetchone(self): return None
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                return MockCursor()
        return MockConnection()

    def execute_query(self, query: str, params: tuple = None, fetch: str = None):
        """Mock execute_query for legacy code."""
        logging.warning("execute_query called on API client! Legacy code detected.")
        if fetch == "all": return []
        if fetch == "one": return None
        return []

    def get_all_tracking_projects(self):
        # Fallback empty list until implemented in API
        return []

    def get_tracking_project(self, project_code: str):
        # Fallback None until implemented in API
        return None

    def save_tracking_project(self, project_code, project_name, config_json):
        return True

    def save_tracking_tasks(self, project_code, payload):
        return True

api_client = _ApiClientProxy()
