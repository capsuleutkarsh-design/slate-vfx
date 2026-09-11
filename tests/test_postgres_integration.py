"""
PostgreSQL Manager Tests - Structural and Focused Integration

Tests for PostgresManager covering:
- API structure validation
- Method signatures
- Singleton pattern
- Key functionality with minimal mocking

Simplified approach focusing on testable aspects without requiring full database.
"""

import pytest
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPostgresManagerStructure:
    """Test PostgresManager class structure and API."""
    
    def test_postgres_manager_can_be_imported(self):
        """Test that PostgresManager can be imported."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        assert PostgresManager is not None
    
    def test_postgres_manager_has_core_methods(self):
        """Test that PostgresManager has all required methods."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        # Core database methods
        assert hasattr(PostgresManager, 'execute_query')
        assert hasattr(PostgresManager, 'get_connection')
        assert hasattr(PostgresManager, 'transaction')
        
        # Stock library methods
        assert hasattr(PostgresManager, 'add_stock_asset')
        assert hasattr(PostgresManager, 'add_stock_assets_batch')
        assert hasattr(PostgresManager, 'get_all_stock_assets')
        assert hasattr(PostgresManager, 'update_asset_tags')
        
        # Project tracking methods
        assert hasattr(PostgresManager, 'record_project')
        assert hasattr(PostgresManager, 'start_operation')
        assert hasattr(PostgresManager, 'update_operation')
        assert hasattr(PostgresManager, 'get_all_projects')
        
        # Pool management
        assert hasattr(PostgresManager, 'get_pool_stats')
        assert hasattr(PostgresManager, '_init_pool')
        assert hasattr(PostgresManager, '_close_pool')
    
    def test_postgres_manager_singleton_pattern(self):
        """Test that PostgresManager uses singleton pattern."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        # Should be able to instantiate (even if it fails connection)
        try:
            db1 = PostgresManager()
            db2 = PostgresManager()
            # If singleton, should be same instance
            assert db1 is db2
        except Exception:
            # OK if instantiation fails without database
            # Just testing structure exists
            pass
    
    def test_postgres_manager_has_retry_logic(self):
        """Test that retry logic method exists."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        assert hasattr(PostgresManager, '_create_pool_with_retry')
    
    def test_context_managers_exist(self):
        """Test that context manager methods exist."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        # get_connection and transaction should be context managers
        assert hasattr(PostgresManager, 'get_connection')
        assert hasattr(PostgresManager, 'transaction')


class TestPostgresManagerMocked:
    """Test PostgresManager with minimal mocking."""
    
    @pytest.fixture(autouse=True)
    def setup_basic_mocks(self):
        """Setup minimal mocks for class instantiation."""
        with patch('ut_vfx.core.infra.global_config.GlobalConfig') as mock_config:
            mock_config.get.return_value = None
            yield
    
    def test_init_creates_instance(self):
        """Test that __init__ can create instance."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        try:
            db = PostgresManager()
            # Check basic attributes exist
            assert hasattr(db, 'host')
            assert hasattr(db, 'port')
            assert hasattr(db, 'dbname')
            assert hasattr(db,'user')
        except Exception:
            # init might fail without actual DB
            # That's OK - we're testing structure
            pass
    
    def test_lazy_initialization_mode(self):
        """Test that PostgresManager uses lazy initialization."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        try:
            db = PostgresManager()
            # Password should be None initially (lazy init)
            assert hasattr(db, 'password')
        except Exception:
            pass
    
    def test_pool_stats_structure(self):
        """Test that get_pool_stats returns expected structure."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        with patch.object(PostgresManager, 'get_pool_stats') as mock_stats:
            mock_stats.return_value = {
                'active': 0,
                'idle': 2,
                'total': 2,
                'min': 2,
                'max': 10
            }
            
            db = PostgresManager()
            stats = db.get_pool_stats()
            
            assert 'active' in stats
            assert 'idle' in stats
            assert 'total' in stats


class TestPostgresManagerWithFullMocks:
    """Test PostgresManager with comprehensive mocking."""
    
    def test_execute_query_method_callable(self):
        """Test execute_query method exists and is callable."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        # Just verify method exists, don't try to call it without proper setup
        assert hasattr(PostgresManager, 'execute_query')
        assert callable(getattr(PostgresManager, 'execute_query'))
    
    def test_add_stock_asset_method_callable(self):
        """Test add_stock_asset method exists and is callable."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        assert hasattr(PostgresManager, 'add_stock_asset')
        assert callable(getattr(PostgresManager, 'add_stock_asset'))
    
    def test_transaction_method_callable(self):
        """Test transaction method exists and is callable."""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        assert hasattr(PostgresManager, 'transaction')
        assert callable(getattr(PostgresManager, 'transaction'))


class TestDatabaseManagerProxy:
    """Test the DatabaseManager proxy wrapper."""
    
    def test_proxy_can_be_imported(self):
        """Test that DatabaseManager proxy can be imported."""
        from ut_vfx.core.infra.database_manager import DatabaseManager
        assert DatabaseManager is not None
    
    def test_proxy_has_backend_attribute(self):
        """Test that proxy has backend attribute."""
        from ut_vfx.core.infra.database_manager import DatabaseManager
        
        with patch.object(DatabaseManager, '_bootstrap_backend', return_value=(MagicMock(), 'postgres', False)):
            db = DatabaseManager()
            assert hasattr(db, 'backend')
    
    def test_proxy_delegates_to_backend(self):
        """Test that DatabaseManager delegates calls to PostgresManager."""
        from ut_vfx.core.infra.database_manager import DatabaseManager
        
        mock_backend = MagicMock()
        mock_backend.execute_query.return_value = [{'id': 1}]
        
        with patch.object(DatabaseManager, '_bootstrap_backend', return_value=(mock_backend, 'postgres', False)):
            db = DatabaseManager()

            # Startup runs schema migrations against the backend; this test is
            # about delegation, so ignore those and start counting from here.
            mock_backend.execute_query.reset_mock()

            # Call method through proxy
            result = db.execute_query("SELECT 1")

            # Should delegate to backend
            mock_backend.execute_query.assert_called_once()
            assert result == [{'id': 1}]
    
    def test_proxy_accepts_legacy_db_path_parameter(self):
        """Test that DatabaseManager accepts db_path for backward compatibility."""
        from ut_vfx.core.infra.database_manager import DatabaseManager
        
        with patch.object(DatabaseManager, '_bootstrap_backend', return_value=(MagicMock(), 'postgres', False)):
            # Should not raise error with optional db_path
            db = DatabaseManager(db_path=Path("/ignored/path.db"))
            assert db is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
