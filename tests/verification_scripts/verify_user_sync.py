
import sys
from pathlib import Path

# Setup paths
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent.parent
sys.path.insert(0, str(root_dir))

# Mock GlobalConfig if needed or rely on existing
# (Assuming the environment is set up correctly in the user's workspace)

# Mock PySide6 and GUI dependencies to avoid installation issues
from unittest.mock import MagicMock
sys.modules["PySide6"] = MagicMock()
sys.modules["PySide6.QtWidgets"] = MagicMock()
sys.modules["PySide6.QtGui"] = MagicMock()
sys.modules["PySide6.QtCore"] = MagicMock()
sys.modules["ut_vfx.gui"] = MagicMock()
sys.modules["ut_vfx.gui.main_window"] = MagicMock()

# Mock heavy binary dependencies not needed for User Sync logic
sys.modules["psutil"] = MagicMock()
sys.modules["numpy"] = MagicMock()
sys.modules["cv2"] = MagicMock()
sys.modules["PIL"] = MagicMock()
sys.modules["reportlab"] = MagicMock()
sys.modules["pandas"] = MagicMock()
sys.modules["cryptography"] = MagicMock()
sys.modules["tenacity"] = MagicMock()
# Configure bcrypt mock to return strings for JSON serialization
bcrypt_mock = MagicMock()
bcrypt_mock.hashpw.return_value = b"mocked_hash_123" # Return bytes as real bcrypt does
bcrypt_mock.gensalt.return_value = b"mocked_salt"
bcrypt_mock.checkpw.return_value = True
sys.modules["bcrypt"] = bcrypt_mock

# Mock DB driver
sys.modules["psycopg2"] = MagicMock()
sys.modules["psycopg2.extras"] = MagicMock()
sys.modules["psycopg2.pool"] = MagicMock()

# Mock PostgresManager module entirely to avoid "Password not found" error during import
sys.modules["ut_vfx.core.infra.postgres_manager"] = MagicMock()
sys.modules["matplotlib"] = MagicMock()
sys.modules["matplotlib.pyplot"] = MagicMock()
sys.modules["seaborn"] = MagicMock()
sys.modules["plotly"] = MagicMock()

# Reportlab submodules
sys.modules["reportlab.lib"] = MagicMock()
sys.modules["reportlab.lib.pagesizes"] = MagicMock()
sys.modules["reportlab.platypus"] = MagicMock()
sys.modules["reportlab.lib.styles"] = MagicMock()

# Mock PostgresManager specifically to spy on checks
# We need to patch it WHERE it is imported in user_manager, or mock the class used


# Now import the backend
from ut_vfx.core.domain.user_manager import UserManager

def run_test():
    print("--- STARTING USER SYNC VERIFICATION (MOCKED) ---")
    
    # Access the mock we injected
    pm_module = sys.modules["ut_vfx.core.infra.postgres_manager"]
    MockPM = pm_module.PostgresManager
    
    print("[1] Initializing UserManager...")
    um = UserManager()
    
    test_user = "_sync_test_bot"
    
    # 2. Add User via UserManager
    print(f"[2] Adding test user '{test_user}'...")
    um.add_user(
        u=test_user,
        p="testpass123",
        r="Artist",
        n="Test Sync Bot",
        j="Debugging"
    )
    
    # 3. Check if PostgresManager was instantiated and sync_users called
    # The Instance is created via PostgresManager() -> MockPM() -> return_value
    
    instance = MockPM.return_value
    
    if instance.sync_users.called:
            print("SUCCESS: PostgresManager.sync_users() was called.")
            print("--- SYNC IS WORKING ---")
    else:
            print("FAILURE: PostgresManager.sync_users() was NOT called.")
            print(f"DEBUG: Calls: {instance.method_calls}")
            print("--- SYNC IS BROKEN / NOT IMPLEMENTED ---")

if __name__ == "__main__":
    run_test()
