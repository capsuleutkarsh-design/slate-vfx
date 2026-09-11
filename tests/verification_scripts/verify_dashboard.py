import sys
import logging
from pathlib import Path

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def verify_dashboard():
    print("="*60)
    print("UT_VFX - DASHBOARD PRE-FLIGHT CHECK")
    print("="*60)
    
    # 1. Check dashboard path (current pro path first, legacy fallback second)
    root_dir = Path(__file__).resolve().parents[2]
    path_candidates = [
        root_dir / "ut_vfx" / "gui" / "tabs" / "vfx_dashboard_pro",
        root_dir / "ut_vfx" / "vfx_dashboard",
    ]

    dash_path = next((p for p in path_candidates if p.exists()), None)
    if not dash_path:
        print("[-] FAIL: Dashboard folder missing.")
        for p in path_candidates:
            print(f"    Checked: {p}")
        return False

    print(f"[+] PASS: Found dashboard folder at {dash_path}")

    # 2. Check Adapter Logic (Simulate Main App)
    print("\n[Testing Integration Adapter]...")
    try:
        from PySide6.QtWidgets import QApplication
        
        # Create Dummy App if needed
        QApplication.instance() or QApplication(sys.argv)
        
        # Simulate User Data (current multi-role format)
        mock_user = {
            "username": "verify_bot", 
            "roles": ["Supervisor"],
            "display_name": "Verification Bot"
        }
        
        print(f"   Simulating User: {mock_user}")
        
        # Import current dashboard widget
        sys.path.append(str(root_dir))
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        # Initialize widget directly (main app uses this implementation now)
        widget = DashboardWidget(user_data=mock_user)
        if widget is not None:
            if hasattr(widget, "cleanup_resources"):
                widget.cleanup_resources()
            widget.deleteLater()
            print("[+] PASS: DashboardWidget initialized successfully.")
            return True
        else:
            print("[-] FAIL: DashboardWidget returned None.")
            return False
            
    except ImportError as e:
        print(f"[-] FAIL: Import Error: {e}")
        return False
    except Exception as e:
        print(f"[-] FAIL: Unexpected Crash: {e}")
        return False

if __name__ == "__main__":
    success = verify_dashboard()
    print("="*60)
    if success:
        print("[+] INTEGRATION VERIFIED. Safe to Deploy.")
        sys.exit(0)
    else:
        print("[-] VERIFICATION FAILED. Do NOT Deploy.")
        sys.exit(1)
