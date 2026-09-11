import os
import sys
import unittest
import json
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from ut_vfx.core.domain.user_manager import UserManager
from ut_vfx.core.infra.database_manager import database_manager

class TestUserDatabasePersistence(unittest.TestCase):
    def setUp(self):
        self.um = UserManager()
        self.test_username = "test_persist_artist"
        self.test_password = "SecurePassword123!"
        # Clean up any remnant
        self.um.delete_user(self.test_username)

    def tearDown(self):
        self.um.delete_user(self.test_username)

    def test_add_and_retrieve_user(self):
        # 1. Add User
        success = self.um.add_user(
            u=self.test_username,
            p=self.test_password,
            roles=["Artist", "Compositor"],
            n="Test Persistence Artist",
            j="Senior Comp",
            pic="/path/to/pic.png"
        )
        self.assertTrue(success, "Failed to add user to database")

        # 2. Retrieve all users
        all_users = self.um.get_all_users()
        self.assertIn(self.test_username, all_users, "User not found in get_all_users()")
        
        user_info = all_users[self.test_username]
        self.assertEqual(user_info["display_name"], "Test Persistence Artist")
        self.assertEqual(user_info["job_title"], "Senior Comp")
        self.assertEqual(user_info["profile_pic_path"], "/path/to/pic.png")
        self.assertIsInstance(user_info["roles"], list)
        self.assertIn("Artist", user_info["roles"])
        self.assertIn("Compositor", user_info["roles"])

        # 3. Direct SQL verification
        row = database_manager.execute_query(
            "SELECT username, display_name, job_title, roles FROM ut_users WHERE username=%s",
            (self.test_username,),
            fetch="one"
        )
        self.assertIsNotNone(row, "Direct SQL query returned None")
        self.assertEqual(row["username"], self.test_username)
        self.assertEqual(row["display_name"], "Test Persistence Artist")

    def test_authentication_workflow(self):
        # Add user
        self.um.add_user(
            u=self.test_username,
            p=self.test_password,
            roles=["Supervisor"],
            n="Super User",
            j="Lead"
        )

        # Authenticate with correct password
        auth_user = self.um.authenticate(self.test_username, self.test_password)
        self.assertIsNotNone(auth_user, "Authentication failed with correct password")
        self.assertEqual(auth_user["user_id"], self.test_username)
        self.assertIn("Supervisor", auth_user["roles"])

        # Authenticate with incorrect password
        bad_auth = self.um.authenticate(self.test_username, "WrongPassword!")
        self.assertIsNone(bad_auth, "Authentication should have failed with wrong password")

    def test_update_user_fields_and_password(self):
        self.um.add_user(
            u=self.test_username,
            p=self.test_password,
            roles=["Artist"],
            n="Initial Name",
            j="Junior Artist"
        )

        # Update without changing password ("KEEP_OLD")
        update_ok = self.um.add_user(
            u=self.test_username,
            p="KEEP_OLD",
            roles=["Supervisor"],
            n="Updated Name",
            j="Senior Lead"
        )
        self.assertTrue(update_ok, "Failed to update user")

        all_users = self.um.get_all_users()
        updated_info = all_users[self.test_username]
        self.assertEqual(updated_info["display_name"], "Updated Name")
        self.assertEqual(updated_info["job_title"], "Senior Lead")
        self.assertEqual(updated_info["roles"], ["Supervisor"])

        # Authentication should still work with original password
        auth_user = self.um.authenticate(self.test_username, self.test_password)
        self.assertIsNotNone(auth_user, "Authentication failed after user update")

        # Now reset password
        new_pass = "BrandNewPassword456!"
        self.um.add_user(
            u=self.test_username,
            p=new_pass,
            roles=["Supervisor"],
            n="Updated Name",
            j="Senior Lead"
        )
        self.assertIsNone(self.um.authenticate(self.test_username, self.test_password), "Old password should not work")
        self.assertIsNotNone(self.um.authenticate(self.test_username, new_pass), "New password must authenticate")

    def test_delete_user(self):
        self.um.add_user(
            u=self.test_username,
            p=self.test_password,
            roles=["Artist"],
            n="To Delete",
            j="Temp"
        )
        self.assertIn(self.test_username, self.um.get_all_users())

        del_ok = self.um.delete_user(self.test_username)
        self.assertTrue(del_ok, "Failed to delete user")
        self.assertNotIn(self.test_username, self.um.get_all_users(), "Deleted user still present in get_all_users()")
        self.assertIsNone(self.um.authenticate(self.test_username, self.test_password))

    def test_users_property_backward_compat(self):
        self.um.add_user(
            u=self.test_username,
            p=self.test_password,
            roles=["Artist"],
            n="Prop Test",
            j="Prop"
        )
        # Verify um.users property works as dict
        self.assertIn(self.test_username, self.um.users)
        self.assertEqual(self.um.users[self.test_username]["display_name"], "Prop Test")

    def test_sync_users_preserves_hashes_and_json_roles(self):
        # Create a user with password
        self.um.add_user(
            u=self.test_username,
            p=self.test_password,
            roles=["Artist"],
            n="Sync User",
            j="Comp"
        )
        initial_hash = self.um.get_all_users()[self.test_username]["password_hash"]
        self.assertTrue(len(initial_hash) > 10)

        # Call database_manager.sync_users with updated metadata but empty password
        payload = {
            self.test_username: {
                "display_name": "Sync User Renamed",
                "roles": ["Supervisor", "Artist"],
                "job_title": "Lead",
                "password_hash": ""  # empty from external caller
            }
        }
        sync_ok = database_manager.sync_users(payload)
        self.assertTrue(sync_ok)

        # Check that password_hash was NOT wiped out
        user_after = self.um.get_all_users()[self.test_username]
        self.assertEqual(user_after["password_hash"], initial_hash, "password_hash was wiped by sync_users")
        self.assertEqual(user_after["display_name"], "Sync User Renamed")
        self.assertEqual(user_after["roles"], ["Supervisor", "Artist"])

        # Auth still works
        self.assertIsNotNone(self.um.authenticate(self.test_username, self.test_password))

if __name__ == "__main__":
    unittest.main()
