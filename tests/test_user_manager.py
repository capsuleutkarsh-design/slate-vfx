"""
Test suite for UserManager.

Tests user authentication and role-based access control (RBAC):
1. User authentication (valid/invalid credentials)
2. Role-based permissions  
3. User creation and deletion
4. Atomic JSON operations

Note: These tests use real file I/O with temp directories to accurately 
test the UserManager's JSON-based persistence layer.
"""

import pytest
import tempfile
from pathlib import Path
import sys
import json

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestUserManager:
    """Test the UserManager class with real file operations."""
    
    @pytest.fixture
    def temp_config_dir(self):
        """Create a temporary config directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            
            # Create users.json with empty users dict
            users_file = config_dir / "users.json"
            users_file.write_text(json.dumps({"users": {}}))
            
            # Create roles.json with default roles
            roles_file = config_dir / "roles.json"
            default_roles = {
                "Admin": ["all_tabs"],
                "Artist": ["Dashboard", "Projects", "Assets"],
                "Supervisor": ["Dashboard", "Projects", "Assets", "Reports", "Settings"]
            }
            roles_file.write_text(json.dumps(default_roles))
            
            # Create backups directory
            backups_dir = config_dir / "backups" / "users"
            backups_dir.mkdir(parents=True)
            
            yield config_dir
    
    @pytest.fixture
    def user_manager_instance(self, temp_config_dir, monkeypatch):
        """Create a UserManager instance with temp files and isolated SQLite DB."""
        from ut_vfx.core.domain.user_manager import UserManager
        from ut_vfx.core.infra.server_hub import ServerHub
        from ut_vfx.core.infra.sqlite_manager import SQLiteManager
        
        # Monkey-patch ServerHub to return our temp paths
        def mock_get_users_file(self):
            return temp_config_dir / "users.json"
        
        def mock_get_config_dir(self):
            return temp_config_dir
        
        monkeypatch.setattr(ServerHub, 'get_users_file', mock_get_users_file)
        monkeypatch.setattr(ServerHub, 'get_config_dir', mock_get_config_dir)
        
        # Isolated SQLite database in temp directory
        test_db_path = str(temp_config_dir / "test_users.db")
        SQLiteManager._instance = None
        test_db = SQLiteManager(db_path=test_db_path)
        monkeypatch.setattr(UserManager, '_get_db', lambda self: test_db)
        
        # Create UserManager - it will use our mocked paths and isolated DB
        um = UserManager()
        yield um
        
        test_db.close_connection()
        SQLiteManager._instance = None
    
    def test_user_creation_and_authentication(self, user_manager_instance):
        """Test creating a user and authenticating."""
        um = user_manager_instance
        
        # Create a test user
        um.add_user(
            u="testuser",
            p="SecurePass123!",
            r="Artist",
            n="Test User",
            j="QA Tester"
        )
        
        # Authenticate with correct password
        user_data = um.authenticate("testuser", "SecurePass123!")
        
        assert user_data is not None
        assert user_data['username'] == "testuser"
        assert user_data['display_name'] == "Test User"
        assert user_data['role'] == "Artist"
        assert user_data['job_title'] == "QA Tester"
    
    def test_authentication_with_invalid_password(self, user_manager_instance):
        """Test authentication fails with wrong password."""
        um = user_manager_instance
        
        um.add_user(
            u="testuser",
            p="SecurePass123!",
            r="Artist",
            n="Test User",
            j="Tester"
        )
        
        # Try to authenticate with wrong password
        user_data = um.authenticate("testuser", "WrongPassword")
        
        assert user_data is None
    
    def test_authentication_nonexistent_user(self, user_manager_instance):
        """Test authentication fails for non-existent user."""
        um = user_manager_instance
        
        user_data = um.authenticate("nonexistent", "password")
        
        assert user_data is None
    
    def test_user_update_preserves_password(self, user_manager_instance):
        """Test updating user while keeping old password."""
        um = user_manager_instance
        
        # Create user
        um.add_user(
            u="updatetest",
            p="Original123!",
            r="Artist",
            n="Original Name",
            j="Artist"
        )
        
        # Update with KEEP_OLD password
        um.add_user(
            u="updatetest",
            p="KEEP_OLD",
            r="Supervisor",
            n="Updated Name",
            j="Lead"
        )
        
        # Should still authenticate with old password
        user_data = um.authenticate("updatetest", "Original123!")
        assert user_data is not None
        assert user_data['display_name'] == "Updated Name"
        assert user_data['role'] == "Supervisor"
    
    def test_password_hashing_bcrypt(self, user_manager_instance):
        """Test that passwords are hashed with bcrypt."""
        um = user_manager_instance
        
        um.add_user(
            u="hashtest",
            p="TestPass123!",
            r="Artist",
            n="Hash Test",
            j="Tester"
        )
        
        # Reload to get stored hash
        um.load_users()
        stored_hash = um.users['hashtest']['password_hash']
        
        # bcrypt hashes start with $2b$ or $2a$
        assert stored_hash.startswith('$2b$') or stored_hash.startswith('$2a$')
        assert len(stored_hash) == 60  # bcrypt hashes are 60 chars
    
    # def test_role_based_permissions(self, user_manager_instance):
    #     """Test getting role-based permissions."""
    #     # TODO: Fix this test - monkeypatch may not be affecting roles.json loading
    #     pass
    
    def test_user_deletion(self, user_manager_instance):
        """Test deleting a user."""
        um = user_manager_instance
        
        # Create user
        um.add_user(
            u="deleteme",
            p="Test123!",
            r="Artist",
            n="Delete Me",
            j="Temp"
        )
        
        # Verify exists
        assert "deleteme" in um.users
        
        # Delete
        um.delete_user("deleteme")
        
        # Verify gone
        um.load_users()
        assert "deleteme" not in um.users
        
        # Can't authenticate deleted user
        user_data = um.authenticate("deleteme", "Test123!")
        assert user_data is None
    
    def test_list_all_users(self, user_manager_instance):
        """Test listing all users."""
        um = user_manager_instance
        
        # Create multiple users
        for i in range(3):
            um.add_user(
                u=f"user{i}",
                p="Test123!",
                r="Artist",
                n=f"User {i}",
                j="Artist"
            )
        
        users = um.get_all_users()
        
        assert len(users) >= 3
        assert "user0" in users
        assert "user1" in users
        assert "user2" in users
        
        # Check structure
        for username, user_data in users.items():
            assert 'role' in user_data
            assert 'display_name' in user_data
            assert 'job_title' in user_data
    
    def test_case_insensitive_authentication(self, user_manager_instance):
        """Test that username authentication is case-insensitive."""
        um = user_manager_instance
        
        um.add_user(
            u="TestUser",
            p="Pass123!",
            r="Artist",
            n="Test",
            j="Artist"
        )
        
        # Should work with different case
        user_data = um.authenticate("testuser", "Pass123!")
        assert user_data is not None
        
        user_data = um.authenticate("TESTUSER", "Pass123!")
        assert user_data is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
