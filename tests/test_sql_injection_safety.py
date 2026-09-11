"""
Unit test verifying SQL injection and special character resilience in queries.
"""

import sys
import unittest
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slate.core.infra.sqlite_manager import SQLiteManager


class TestSQLInjectionSafety(unittest.TestCase):
    """Verifies that parameterized queries handle quotes, apostrophes, and injections safely."""

    def setUp(self):
        SQLiteManager._instance = None
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db = SQLiteManager(db_path=self.tmp_db.name)

    def tearDown(self):
        try:
            if hasattr(self.db, '_local') and hasattr(self.db._local, 'conn'):
                try:
                    self.db._local.conn.close()
                except Exception:
                    pass
            SQLiteManager._instance = None
            Path(self.tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass

    def test_apostrophe_in_username_and_reason(self):
        # Usernames with Irish/French apostrophes (e.g. O'Connor, D'Angelo)
        user_id = "tim_o'connor"
        reason = "Attending sister's wedding; won't be in office"
        
        # Insert using parameterized query
        q_user = "INSERT INTO ut_users (username, display_name) VALUES (%s, %s)"
        self.db.execute_query(q_user, (user_id, "Tim O'Connor"), fetch=False)

        q_leave = """
            INSERT INTO leave_requests (user_id, type, start_date, end_date, reason, status)
            VALUES (%s, %s, %s, %s, %s, 'Pending')
        """
        self.db.execute_query(q_leave, (user_id, "Casual Leave (CL)", "2026-09-10", "2026-09-12", reason), fetch=False)

        # Retrieve and verify
        res = self.db.execute_query("SELECT * FROM leave_requests WHERE user_id = %s", (user_id,))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["reason"], reason)

    def test_sql_injection_payload_is_neutralized(self):
        # Classic SQL injection attempt to bypass auth or drop tables
        malicious_input = "test_user'; DROP TABLE ut_users;--"
        
        # Query with parameterization
        q = "SELECT * FROM leave_balances WHERE user_id = %s"
        res = self.db.execute_query(q, (malicious_input,))
        self.assertEqual(len(res), 0)

        # Ensure ut_users table was NOT dropped
        users = self.db.execute_query("SELECT count(*) as cnt FROM ut_users")
        self.assertIsNotNone(users)

    def test_hardware_inventory_special_names(self):
        # Machine names with brackets, quotes, dashes
        mname = "WS-VFX-['Node-01']"
        q = """
            INSERT INTO hardware_inventory (machine_name, type, status, assigned_to, cpu, gpu, ram, storage)
            VALUES (%s, 'Workstation', 'Active', %s, %s, %s, %s, %s)
        """
        self.db.execute_query(q, (mname, "artist_o'neil", "Intel Core i9", "RTX 4090", "64 GB", "2 TB"), fetch=False)

        res = self.db.execute_query("SELECT * FROM hardware_inventory WHERE machine_name = %s", (mname,))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["assigned_to"], "artist_o'neil")


if __name__ == "__main__":
    unittest.main()
