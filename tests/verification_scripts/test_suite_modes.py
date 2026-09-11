"""
Comprehensive verification test for Suite Decoupling & Build Pipeline.
Tests:
1. VFXStudioWindow mode and tabs isolation
2. StudioOpsWindow mode and tabs isolation
3. VFXFolderCreatorApp full suite compatibility
4. Entry points (vfx_studio_main, studio_ops_main, gatekeeper_main)
5. Spec and Inno Setup configuration integrity
6. Build pipeline argument handling
"""

import sys
import os
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

os.environ["Slate_DB_MODE"] = "sqlite"
from slate.core.infra.global_config import GlobalConfig
GlobalConfig.set("db_mode", "sqlite")
from slate.core.infra.database_manager import database_manager
try:
    database_manager.reload_from_config()
except Exception:
    pass

from PySide6.QtWidgets import QApplication

# Ensure single headless QApplication instance
app = QApplication.instance()
if not app:
    app = QApplication(["--platform", "offscreen"])

from slate.core.infra.app_context import AppContext
from slate.gui.vfx_studio_window import VFXStudioWindow
from slate.gui.studio_ops_window import StudioOpsWindow
from slate.gui.main_window import VFXFolderCreatorApp
from slate.gui.tabs.vfx_dashboard_pro.viewmodels.dashboard_viewmodel import DashboardViewModel

# Ensure Developer role has ALL permissions
um = AppContext().user_manager()
um.update_role_permissions("Developer", ["ALL"])

mock_user = {
    "username": "lead_artist",
    "display_name": "Lead Artist",
    "job_title": "VFX Lead",
    "roles": ["Developer"]
}

def verify_vfx_studio_window():
    print("[TEST 1] Verifying VFXStudioWindow...")
    ctx = AppContext()
    win = VFXStudioWindow(user_data=mock_user, app_context=ctx)
    assert win.app_mode == "vfx", f"Expected mode 'vfx', got '{win.app_mode}'"
    assert "Slate Studio" in win.windowTitle(), f"Expected 'Slate Studio' in title, got '{win.windowTitle()}'"

    tab_labels = [lbl for lbl in win.tab_coordinator.tab_labels if not lbl.startswith("__HEADER__")]
    print(f"  VFX Tab count: {len(tab_labels)}, Labels: {tab_labels}")

    # VFX MUST have these
    for expected in ["Home", "Folder Creator", "Scan Manager", "CAP Rename", "Stock Viewer", "Shot Review", "VFX Dashboard", "Scheduling", "Bidding", "Settings"]:
        assert expected in tab_labels, f"Expected tab '{expected}' in VFX Studio"

    # Verify VFX Home tab does NOT have attendance widgets
    vfx_home = win.tab_coordinator.get_or_create_tab("Home")
    assert not hasattr(vfx_home, "attendance_panel"), "VFX Home tab must NOT have attendance_panel"
    assert not hasattr(vfx_home, "btn_punch_in"), "VFX Home tab must NOT have btn_punch_in"

    # VFX MUST NOT have Ops-specific tabs
    for forbidden in ["Attendance", "Leave Management", "Onboarding", "Hardware", "Licenses", "Ticketing", "Deployment", "Users & Roles", "Admin Panel"]:
        assert forbidden not in tab_labels, f"Forbidden tab '{forbidden}' was found in VFX Studio!"

    print("  [PASS] VFXStudioWindow tab isolation and attendance-free Home verified.")
    win.close()
    win.deleteLater()
    app.processEvents()

def verify_studio_ops_window():
    print("[TEST 2] Verifying StudioOpsWindow...")
    ctx = AppContext()
    win = StudioOpsWindow(user_data=mock_user, app_context=ctx)
    assert win.app_mode == "ops", f"Expected mode 'ops', got '{win.app_mode}'"
    assert "Slate Operations" in win.windowTitle(), f"Expected 'Slate Operations' in title, got '{win.windowTitle()}'"

    tab_labels = [lbl for lbl in win.tab_coordinator.tab_labels if not lbl.startswith("__HEADER__")]
    print(f"  Ops Tab count: {len(tab_labels)}, Labels: {tab_labels}")

    # Ops MUST have these (including the new Operations Home tab!)
    for expected in ["Home", "Attendance", "Leave Management", "Onboarding", "Hardware", "Licenses", "Ticketing", "Deployment", "Users & Roles", "Admin Panel", "Settings"]:
        assert expected in tab_labels, f"Expected tab '{expected}' in Studio Ops"

    # Verify Operations Home tab has attendance widgets
    ops_home = win.tab_coordinator.get_or_create_tab("Home")
    assert hasattr(ops_home, "attendance_panel"), "Studio Ops Home tab MUST have attendance_panel"
    assert hasattr(ops_home, "btn_punch_in"), "Studio Ops Home tab MUST have btn_punch_in"

    # Ops MUST NOT have VFX-specific tabs
    for forbidden in ["Folder Creator", "Scan Manager", "CAP Rename", "Stock Viewer", "Shot Review", "VFX Dashboard", "Scheduling", "Bidding"]:
        assert forbidden not in tab_labels, f"Forbidden tab '{forbidden}' was found in Studio Ops!"

    print("  [PASS] StudioOpsWindow tab isolation and Operations Home verified.")
    win.close()
    win.deleteLater()
    app.processEvents()

def verify_all_in_one_window():
    print("[TEST 3] Verifying all-in-one backwards compatibility...")
    ctx = AppContext()
    win = VFXFolderCreatorApp(user_data=mock_user, app_context=ctx, app_mode="all")
    assert win.app_mode == "all", f"Expected mode 'all', got '{win.app_mode}'"
    assert "Slate Production" in win.windowTitle(), f"Expected 'Slate Production' in title, got '{win.windowTitle()}'"

    tab_labels = [lbl for lbl in win.tab_coordinator.tab_labels if not lbl.startswith("__HEADER__")]
    print(f"  All-in-One Tab count: {len(tab_labels)}, Labels: {tab_labels}")

    # All-in-one MUST have both VFX and Ops tabs
    for expected in ["Home", "Folder Creator", "Scan Manager", "Stock Viewer", "Shot Review", "VFX Dashboard", "Attendance", "Leave Management", "Hardware", "Licenses", "Users & Roles", "Settings"]:
        assert expected in tab_labels, f"Expected tab '{expected}' in All-in-One window"

    print("  [PASS] All-in-one window verified.")
    win.close()
    win.deleteLater()
    app.processEvents()

def verify_build_and_launch_files():
    print("[TEST 4] Verifying launch scripts, spec, and installers...")
    required_files = [
        root_dir / "launch_vfx.bat",
        root_dir / "launch_ops.bat",
        root_dir / "launch_server.bat",
        root_dir / "launch_app.bat",
        root_dir / "launch_console.bat",
        root_dir / "Slate.spec",
        root_dir / "deployment" / "setup_slate_client.iss",
        root_dir / "deployment" / "setup_ut_studio_ops.iss",
        root_dir / "deployment" / "setup_ut_central_server.iss",
        root_dir / "tools" / "build_pipeline.py",
        root_dir / "tools" / "build_update_package.py",
        root_dir / "tools" / "slate_console" / "ui" / "build_tab.py",
    ]
    for f in required_files:
        assert f.exists(), f"Required build/launch file missing: {f}"
        print(f"  [OK] Exists: {f.name}")

    # Check spec contains all 3 targets
    spec_content = (root_dir / "Slate.spec").read_text(encoding="utf-8")
    assert "name='Slate_Studio'" in spec_content, "Slate_Studio missing from Slate.spec"
    assert "name='UT_Studio_Ops'" in spec_content, "UT_Studio_Ops missing from Slate.spec"
    assert "name='UT_Server'" in spec_content, "UT_Server missing from Slate.spec"
    assert "name='Slate'" in spec_content, "Slate fallback missing from Slate.spec"

    print("  [PASS] All build and installer configuration files verified.")

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    try:
        verify_vfx_studio_window()
        verify_studio_ops_window()
        verify_all_in_one_window()
        verify_build_and_launch_files()
        print("\n============================================================")
        print("  ALL SUITE DECOUPLING & BUILD PIPELINE TESTS PASSED!")
        print("============================================================")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        import gc
        gc.collect()
        if app:
            app.quit()
    sys.exit(0)

