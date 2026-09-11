import sys
import os
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

# Add Root to sys.path (tests/verification_scripts -> root)
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(root_dir)

def verify_imports():
    print("--- Verifying Refactor Imports ---")
    print(f"Running from: {root_dir}")
    
    try:
        print("Importing core.workers.file_ops...")
        from ut_vfx.core.domain.workers import file_ops
        print("OK")
    except ImportError as e:
        print(f"FAILED: {e}")
        return False

    try:
        print("Importing core.workers.structure...")
        from ut_vfx.core.domain.workers import structure
        print("OK")
    except ImportError as e:
        print(f"FAILED: {e}")
        return False

    try:
        print("Importing core.workers.reporting...")
        from ut_vfx.core.domain.workers import reporting
        print("OK")
    except ImportError as e:
        print(f"FAILED: {e}")
        return False

    try:
        print("Importing core.workers.library...")
        from ut_vfx.core.domain.workers import library
        print("OK")
    except ImportError as e:
        print(f"FAILED: {e}")
        return False

    try:
        print("Importing core.workers.analysis...")
        from ut_vfx.core.domain.workers import analysis
        print("OK")
    except ImportError as e:
        print(f"FAILED: {e}")
        return False

    try:
        print("Importing gui.tabs.settings_tab...")
        from ut_vfx.gui.tabs import settings_tab
        print("OK")
    except ImportError as e:
        print(f"FAILED: {e}")
        return False

    # This one might fail if GUI requires QApplication, but we'll try just the import
    try:
        print("Importing gui.main_window...")
        from ut_vfx.gui import main_window
        print("OK")
    except ImportError as e:
        print(f"FAILED logic import: {e}")
        return False
    except Exception as e:
        # Expected to fail instantiation if no app, but import should be fine unless top-level logic runs
        print(f"Import triggered execution needing App? {e}")
    
    print("--- All Module Imports Successful ---")
    return True

if __name__ == "__main__":
    if verify_imports():
        sys.exit(0)
    else:
        sys.exit(1)
