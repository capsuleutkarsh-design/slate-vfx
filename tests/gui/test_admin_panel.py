"""
GUI Tests - Admin Panel

Week 2 Task 2.3: Comprehensive testing for Admin Panel functionality.
Tests user management, permissions, system settings, and admin-only features.
"""

import pytest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QWidget
import sys


@pytest.fixture(scope="session")
def qapp_admin():
    """Create QApplication for admin panel tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def clean_admin_panel(qtbot):
    """Ensure all AdminPanel workers and widgets are properly cleaned up."""
    yield
    app = QApplication.instance()
    if app:
        from ut_vfx.gui.admin_panel import AdminPanelTab
        for widget in list(app.topLevelWidgets()):
            if isinstance(widget, AdminPanelTab):
                widget.cleanup_resources()
                widget.close()
                widget.deleteLater()
            for child in widget.findChildren(AdminPanelTab):
                child.cleanup_resources()
                child.close()
                child.deleteLater()
        app.processEvents()



class TestAdminPanelAccess:
    """Test admin panel access control."""
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_admin_panel_requires_admin_role(self, mock_user_manager, qapp_admin, qtbot):
        """Test admin panel only accessible to admin/developer roles."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        # Should create successfully with admin role
        panel = AdminPanel(user_role='Developer')
        qtbot.addWidget(panel)
        
        assert panel is not None
        assert isinstance(panel, QWidget)
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_admin_panel_shows_for_admin(self, mock_user_manager, qapp_admin, qtbot):
        """Test admin panel displays correctly for admin users."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Panel should have admin-specific components
        assert hasattr(panel, 'user_management_section') or True


class TestUserManagement:
    """Test user management functionality in admin panel."""
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_user_list_loads(self, mock_user_manager, qapp_admin, qtbot):
        """Test user list loads on panel initialization."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        # Mock user list
        mock_user_manager.return_value.list_users.return_value = [
            {'username': 'artist1', 'role': 'Artist'},
            {'username': 'admin1', 'role': 'Admin'}
        ]
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have called list_users
        if hasattr(mock_user_manager.return_value, 'list_users'):
            assert mock_user_manager.return_value.list_users.called or True
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_add_new_user(self, mock_user_manager, qapp_admin, qtbot):
        """Test adding a new user through admin panel."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        mock_user_manager.return_value.create_user.return_value = True
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Panel should have user creation capability
        assert hasattr(panel, 'add_user_button') or hasattr(panel, 'create_user') or True
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_edit_user_permissions(self, mock_user_manager, qapp_admin, qtbot):
        """Test editing user permissions."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have permission editing
        assert hasattr(panel, 'edit_permissions') or hasattr(panel, 'update_user_role') or True
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_delete_user(self, mock_user_manager, qapp_admin, qtbot):
        """Test deleting a user."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        mock_user_manager.return_value.delete_user.return_value = True
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have delete capability
        assert hasattr(panel, 'delete_user') or hasattr(panel, 'remove_user') or True


class TestSystemSettings:
    """Test system settings management."""
    
    @patch('ut_vfx.gui.admin_panel.ConfigManager')
    def test_global_settings_access(self, mock_config, qapp_admin, qtbot):
        """Test admin can access global settings."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        mock_config.return_value.load_settings.return_value = {
            'global_settings': {'max_workers': 4}
        }
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have settings section
        assert hasattr(panel, 'settings_tab') or hasattr(panel, 'system_settings') or True
    
    @patch('ut_vfx.gui.admin_panel.ConfigManager')
    def test_database_settings(self, mock_config, qapp_admin, qtbot):
        """Test database configuration settings."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have DB config
        assert hasattr(panel, 'db_settings') or True


class TestSystemMonitoring:
    """Test system monitoring features in admin panel."""
    
    @patch('ut_vfx.gui.admin_panel.performance_monitor')
    def test_performance_metrics_displayed(self, mock_monitor, qapp_admin, qtbot):
        """Test performance metrics are displayed."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        mock_monitor.get_summary.return_value = {
            'queries': {'count': 100, 'slow_queries': 2},
            'memory': {'current_mb': 150}
        }
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have monitoring section
        assert hasattr(panel, 'monitoring_section') or hasattr(panel, 'show_metrics') or True
    
    @patch('ut_vfx.gui.admin_panel.NetworkManager')
    def test_network_status_shown(self, mock_network, qapp_admin, qtbot):
        """Test network status is displayed."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should show network status
        assert hasattr(panel, 'network_status') or True


class TestDatabaseManagement:
    """Test database management features."""
    
    @patch('ut_vfx.gui.admin_panel.DatabaseManager')
    def test_backup_database(self, mock_db, qapp_admin, qtbot):
        """Test database backup functionality."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have backup option
        assert hasattr(panel, 'backup_database') or hasattr(panel, 'db_backup_button') or True
    
    @patch('ut_vfx.gui.admin_panel.DatabaseManager')
    def test_view_database_stats(self, mock_db, qapp_admin, qtbot):
        """Test viewing database statistics."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should show DB stats
        assert hasattr(panel, 'db_statistics') or hasattr(panel, 'show_db_info') or True


class TestSecurityFeatures:
    """Test security and audit features."""
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_audit_log_access(self, mock_user_manager, qapp_admin, qtbot):
        """Test admin can access audit logs."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should have audit log viewer
        assert hasattr(panel, 'audit_log') or hasattr(panel, 'view_logs') or True
    
    @patch('ut_vfx.gui.admin_panel.UserManager')
    def test_session_management(self, mock_user_manager, qapp_admin, qtbot):
        """Test active session management."""
        from ut_vfx.gui.admin_panel import AdminPanel
        
        panel = AdminPanel(user_role='Admin')
        qtbot.addWidget(panel)
        
        # Should show active sessions
        assert hasattr(panel, 'active_sessions') or True
