import sys

# Add project root to path
sys.path.append("c:/Users/capadmin/Documents/Studio_soft_2/V0040")

from ut_vfx.core.domain.user_manager import UserManager
from ut_vfx.core.domain.notification_manager import NotificationManager

def test_multi_role():
    print("--- Testing Multi-Role ---")
    um = UserManager()
    
    # create test user
    uid = "verification_user"
    if uid in um.users:
        um.delete_user(uid)
        
    roles = ["Artist", "Supervisor"]
    um.add_user(uid, "pass123", roles, "Verification User", "Tester")
    
    user = um.users.get(uid)
    print(f"User Roles: {user.get('roles')}")
    assert user.get('roles') == roles
    
    # Check permissions
    tabs = um.get_allowed_tabs(roles)
    print(f"Allowed Tabs: {tabs}")
    assert "Stock Browser" in tabs # From Artist
    assert "Admin Panel" in tabs   # From Supervisor
    print("Multi-Role Verified!")

def test_notifications():
    print("\n--- Testing Notifications ---")
    notifier = NotificationManager()
    
    # Clear old
    target_user = "test_artist_notification"
    
    # Mock Shot Write
    # We need to simulate SQLiteHandler.write_shots but without full DB requirement if possible
    # or just use NotificationManager directly to verify storage
    
    notifier.add_notification(target_user, "Test Assignment", "assignment")
    notifier.add_notification("other_user", "Ignore this", "info")
    
    unread = notifier.get_unread(target_user)
    print(f"Unread for {target_user}: {len(unread)}")
    assert len(unread) >= 1
    assert unread[0]['message'] == "Test Assignment"
    
    # Mark read
    notifier.mark_read([unread[0]['id']])
    unread_after = notifier.get_unread(target_user)
    print(f"Unread after clear: {len(unread_after)}")
    assert len(unread_after) == 0
    print("Notifications Verified!")

if __name__ == "__main__":
    try:
        test_multi_role()
        test_notifications()
        print("\nALL TESTS PASSED")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
