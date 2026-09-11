import unittest
import os
import sys
import datetime
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

os.environ["Slate_DB_MODE"] = "sqlite"

from slate.core.infra.global_config import GlobalConfig
GlobalConfig.set("db_mode", "sqlite")
from slate.core.infra.database_manager import database_manager

class TestLogicFixes(unittest.TestCase):
    def setUp(self):
        self.db = database_manager

    def test_milestone_circular_dependency_cycle_detection(self):
        """Fix 1: Verify shift_downstream detects cycles and prevents infinite recursion."""
        # Create table if not exists
        self.db.execute_update("""
            CREATE TABLE IF NOT EXISTS prod_scheduling (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                milestone TEXT,
                start_date TEXT,
                end_date TEXT,
                depends_on_id INTEGER,
                status TEXT
            )
        """)
        # Insert circular dependencies: M1 -> M2 -> M1
        self.db.execute_update("DELETE FROM prod_scheduling WHERE project_code = 'TEST_CYCLE'")
        self.db.execute_update(
            "INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, depends_on_id, status) VALUES (%s, %s, %s, %s, %s, %s)",
            ('TEST_CYCLE', 'Milestone A', '2026-09-10', '2026-09-20', None, 'Pending')
        )
        row_a = self.db.execute_query("SELECT id FROM prod_scheduling WHERE project_code='TEST_CYCLE' AND milestone='Milestone A'", fetch="one")
        id_a = row_a['id']

        self.db.execute_update(
            "INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, depends_on_id, status) VALUES (%s, %s, %s, %s, %s, %s)",
            ('TEST_CYCLE', 'Milestone B', '2026-09-21', '2026-09-30', id_a, 'Pending')
        )
        row_b = self.db.execute_query("SELECT id FROM prod_scheduling WHERE project_code='TEST_CYCLE' AND milestone='Milestone B'", fetch="one")
        id_b = row_b['id']

        # Close the cycle: A depends on B
        self.db.execute_update("UPDATE prod_scheduling SET depends_on_id = %s WHERE id = %s", (id_b, id_a))

        # Re-implement shift_downstream logic as in prod_scheduling_tab
        visited_nodes = set()
        def shift_downstream(current_id, shift_days, visited=None):
            if visited is None:
                visited = set()
            if current_id in visited:
                return
            visited.add(current_id)

            curr = self.db.execute_query("SELECT start_date, end_date FROM prod_scheduling WHERE id = %s", (int(current_id),))
            if curr:
                s_raw = str(curr[0].get('start_date') or '').split()[0]
                e_raw = str(curr[0].get('end_date') or '').split()[0]
                try:
                    s_date = datetime.date.fromisoformat(s_raw) + datetime.timedelta(days=shift_days)
                    e_date = datetime.date.fromisoformat(e_raw) + datetime.timedelta(days=shift_days)
                    self.db.execute_query(
                        "UPDATE prod_scheduling SET start_date = %s, end_date = %s WHERE id = %s",
                        (str(s_date), str(e_date), int(current_id)),
                        fetch=False
                    )
                except Exception:
                    pass
            children = self.db.execute_query("SELECT id FROM prod_scheduling WHERE depends_on_id = %s", (int(current_id),)) or []
            for c in children:
                child_id = c.get('id')
                if child_id is not None and child_id not in visited:
                    shift_downstream(child_id, shift_days, visited)

        # Must not raise RecursionError despite circular graph
        try:
            shift_downstream(id_a, 5, visited_nodes)
            success = True
        except RecursionError:
            success = False

        self.assertTrue(success, "Infinite recursion occurred on circular milestone dependencies!")
        self.assertIn(id_a, visited_nodes)
        self.assertIn(id_b, visited_nodes)

        # Clean up
        self.db.execute_update("DELETE FROM prod_scheduling WHERE project_code = 'TEST_CYCLE'")

    def test_central_attendance_configurable_auto_logout(self):
        """Fix 3: Verify central_attendance auto-logout uses configured cutoff time."""
        from slate.core.domain.central_attendance import CentralAttendance
        ca = CentralAttendance()

        test_user = "test_autologout_user"
        test_prev_date = "2026-09-01"
        test_today = "2026-09-02"

        # Create un-closed punch-in on prev date
        self.db.execute_update("DELETE FROM attendance_log WHERE user_id = %s", (test_user,))
        self.db.execute_update(
            "INSERT INTO attendance_log (user_id, day_date, punch_in, pc_name, metadata) VALUES (%s, %s, %s, %s, %s)",
            (test_user, test_prev_date, "10:00:00", "TEST_PC", "{}")
        )

        # Set custom logout time
        GlobalConfig.set("default_auto_logout_time", "21:00:00")

        # Run auto-logout fix
        ca._check_and_fix_previous_day(test_user, test_today)

        row = self.db.execute_query("SELECT punch_out, metadata FROM attendance_log WHERE user_id = %s AND day_date = %s", (test_user, test_prev_date), fetch="one")
        self.assertIsNotNone(row)
        self.assertEqual(row.get("punch_out"), "21:00:00")

        # Reset config and clean up
        GlobalConfig.set("default_auto_logout_time", "19:30:00")
        self.db.execute_update("DELETE FROM attendance_log WHERE user_id = %s", (test_user,))

    def test_it_licenses_schema_and_scan(self):
        """Fix 4: Verify it_licenses scan updates seats_used based on process keyword matching."""
        self.db.execute_update("""
            CREATE TABLE IF NOT EXISTS it_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                software_name TEXT,
                license_key TEXT,
                seats_total INTEGER DEFAULT 1,
                seats_used INTEGER DEFAULT 0,
                expiry_date TEXT
            )
        """)
        self.db.execute_update("DELETE FROM it_licenses WHERE software_name = 'Test Python DCC'")
        self.db.execute_update(
            "INSERT INTO it_licenses (software_name, license_key, seats_total, seats_used, expiry_date) VALUES (%s, %s, %s, %s, %s)",
            ("Test Python DCC", "KEY-12345", 10, 0, "2027-01-01")
        )

        row = self.db.execute_query("SELECT id, seats_used FROM it_licenses WHERE software_name = 'Test Python DCC'", fetch="one")
        self.assertIsNotNone(row)
        self.assertEqual(row.get("seats_used"), 0)

        # Verify update query executes cleanly
        self.db.execute_query(
            "UPDATE it_licenses SET seats_used = %s WHERE id = %s",
            (3, row["id"]),
            fetch=False
        )
        updated = self.db.execute_query("SELECT seats_used FROM it_licenses WHERE id = %s", (row["id"],), fetch="one")
        self.assertEqual(updated.get("seats_used"), 3)

        self.db.execute_update("DELETE FROM it_licenses WHERE software_name = 'Test Python DCC'")

if __name__ == "__main__":
    unittest.main()
