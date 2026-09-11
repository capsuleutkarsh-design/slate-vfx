"""
Unit test verifying Optimistic Concurrency Control (OCC) and conflict detection.
"""

import sys
import unittest
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ut_vfx.core.infra.sqlite_manager import SQLiteManager
from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler, StaleDataError


class TestOptimisticConcurrencyControl(unittest.TestCase):
    """Tests OCC conflict detection, stale data rejections, and force overrides."""

    def setUp(self):
        SQLiteManager._instance = None
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db = SQLiteManager(db_path=self.tmp_db.name)
        self.handler = SQLiteHandler(project_code="PRJ_OCC", db_manager=self.db, user_role="supervisor")

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

    def test_single_shot_version_increment_and_stale_detection(self):
        # 1. Initial shot write
        shot1 = Shot(shot_name="SH_001", status="Ready", priority=1)
        self.handler.write_shots([shot1])

        # Read back
        shots = self.handler.read_shots()
        self.assertEqual(len(shots), 1)
        loaded_shot = shots[0]
        self.assertEqual(loaded_shot.shot_name, "SH_001")
        initial_version = loaded_shot.version

        # 2. First user modifies and saves
        loaded_shot.status = "In Progress"
        self.handler.write_shots([loaded_shot])

        # 3. Simulate second user who had loaded the initial version (stale)
        stale_shot = Shot(shot_name="SH_001", status="Blocked", priority=2)
        stale_shot.version = initial_version # stale!

        # Attempting to save stale shot must raise StaleDataError
        with self.assertRaises(StaleDataError):
            self.handler.write_shots([stale_shot])

    def test_batch_save_conflict_detection(self):
        # Insert 3 shots
        shots = [
            Shot(shot_name="SH_A", status="Ready"),
            Shot(shot_name="SH_B", status="Ready"),
            Shot(shot_name="SH_C", status="Ready"),
        ]
        self.handler.write_shots(shots)

        # User 1 and User 2 both read
        user1_shots = self.handler.read_shots()
        user2_shots = self.handler.read_shots()

        # User 1 updates SH_B
        user1_shots[1].status = "Approved"
        self.handler.write_shots([user1_shots[1]])

        # User 2 now attempts to batch save all 3 shots with older versions
        user2_shots[0].status = "Comp Done"
        with self.assertRaises(StaleDataError) as ctx:
            self.handler.write_shots(user2_shots)
        self.assertIn("SH_B", str(ctx.exception))

        # User 2 forces overwrite (supervisor override)
        force_ok = self.handler.write_shots(user2_shots, force=True)
        self.assertTrue(force_ok)


if __name__ == "__main__":
    unittest.main()
