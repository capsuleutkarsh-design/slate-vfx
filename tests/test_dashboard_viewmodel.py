import unittest
import sys, os
sys.path.insert(0, os.path.abspath("."))
from types import SimpleNamespace
from slate.gui.tabs.vfx_dashboard_pro.viewmodels.dashboard_viewmodel import DashboardViewModel


class TestDashboardViewModel(unittest.TestCase):
    def setUp(self):
        self.vm = DashboardViewModel()
        self.shots = [
            SimpleNamespace(name="SH010", sequence="SEQ01", reel="R01", status="APPROVED", assigned_artist="alice", duration=100, bid_days=2.0, actual_days=1.8, sow="Paint wire"),
            SimpleNamespace(name="SH020", sequence="SEQ01", reel="R01", status="WIP", assigned_artist="bob", duration=50, bid_days=1.5, actual_days=1.0, sow="Roto actor"),
            SimpleNamespace(name="SH030", sequence="SEQ02", reel="R02", status="RETAKE", assigned_artist="alice", duration=80, bid_days=3.0, actual_days=2.5, sow="Comp BG"),
            SimpleNamespace(name="SH040", sequence="SEQ02", reel="R02", status="YTS", assigned_artist="", duration=40, bid_days=1.0, actual_days=0.0, sow="3D tracking"),
        ]
        self.vm.all_shots = self.shots

    def test_initial_state(self):
        self.assertEqual(len(self.vm.displayed_shots), 4)
        self.assertEqual(self.vm.search_text, "")
        self.assertEqual(self.vm.status_filter, "All Status")

    def test_search_filtering(self):
        self.vm.set_search_text("Roto")
        self.assertEqual(len(self.vm.displayed_shots), 1)
        self.assertEqual(self.vm.displayed_shots[0].name, "SH020")

        self.vm.set_search_text("SEQ02")
        self.assertEqual(len(self.vm.displayed_shots), 2)

        self.vm.set_search_text("")
        self.assertEqual(len(self.vm.displayed_shots), 4)

    def test_status_filtering(self):
        self.vm.set_status_filter("APPROVED")
        self.assertEqual(len(self.vm.displayed_shots), 1)
        self.assertEqual(self.vm.displayed_shots[0].name, "SH010")

        self.vm.set_status_filter("All Status")
        self.assertEqual(len(self.vm.displayed_shots), 4)

    def test_artist_scoping(self):
        self.vm.artist_scope = "alice"
        self.assertEqual(len(self.vm.displayed_shots), 2)
        for s in self.vm.displayed_shots:
            self.assertEqual(s.assigned_artist, "alice")

        self.vm.artist_scope = None
        self.assertEqual(len(self.vm.displayed_shots), 4)

    def test_grouping_and_rollups(self):
        self.vm.set_group_by_mode("Reel / Seq")
        groups = self.vm.groups
        self.assertIn("R01", groups)
        self.assertIn("R02", groups)
        self.assertEqual(groups["R01"]["count"], 2)
        self.assertEqual(groups["R01"]["total_frames"], 150)
        self.assertEqual(groups["R01"]["approved_count"], 1)
        self.assertEqual(groups["R01"]["approved_pct"], 50)

    def test_batch_update(self):
        updated = self.vm.apply_batch_update(["SH020", "SH040"], {"status": "APPROVED", "assigned_artist": "carol"})
        self.assertEqual(updated, 2)
        self.assertEqual(self.shots[1].status, "APPROVED")
        self.assertEqual(self.shots[1].assigned_artist, "carol")
        self.assertEqual(self.shots[3].status, "APPROVED")
        self.assertEqual(self.shots[3].assigned_artist, "carol")


if __name__ == "__main__":
    unittest.main()
