import psycopg2
from psycopg2 import pool
from psycopg2.pool import PoolError
from psycopg2.extras import RealDictCursor, execute_values
import logging
import json
import threading
import atexit
from collections import OrderedDict
from typing import Dict, List, Tuple, Optional, Any
import re
from datetime import datetime, timedelta
from contextlib import contextmanager
from tenacity import retry_if_exception, retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .circuit_breaker import CircuitBreaker, CircuitBreakerError
from .retry_strategy import RetryStrategy
from .project_repository import ProjectRepository
from .stock_repository import StockRepository
from .tracking_repository import TrackingRepository
from .user_repository import UserRepository

# How many database connections one machine may hold, whatever its saved
# settings say. Every workstation that already has Slate installed carries a
# config file from before this limit existed, and those files override the
# shipped defaults - so a change to the default alone would never reach an
# installed machine. 150 machines at 4 connections is 600 against a server
# limit of 100; at 2 it is 300, and behind PgBouncer it is a few dozen.
MAX_POOL_PER_CLIENT = 2


class DatabaseUnavailableError(ConnectionError):
    """
    The database could not be reached, so this operation did not happen.

    Raised instead of quietly returning "no rows". A read that silently returns
    nothing looks like an empty project; a write that silently does nothing
    looks like a successful save. Both are worse than an error message, and
    both are exactly what a connection shortage produces.
    """


def _client_identity() -> str:
    """
    How this client names itself to the database.

    Shows up in pg_stat_activity and on the server dashboard, so load can be
    traced to a person and a machine instead of 150 anonymous connections.
    """
    import getpass
    import socket

    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    # Postgres truncates application_name at 63 bytes.
    return f"Slate {user}@{host}"[:63]


def what_to_do(error, host, port, dbname, user) -> str:
    """
    What to actually check, for the failure that actually happened.

    The advice used to be the same five lines whatever went wrong, starting
    with "verify the database server is running" and "ping the host". For the
    two failures that happen most often the server is running, the host does
    ping, the port is open and the database is there - every one of those five
    checks passes, and the person doing them concludes the software is broken.

    A database refusing a connection and a database that is not there are
    different problems with nothing in common except the word "connect".
    """
    text = str(error).lower()

    if "no pg_hba.conf entry" in text:
        return (
            "The server is running and reachable. It has not been told to "
            "accept connections from this address.\n\n"
            "On the machine running Slate Server:\n"
            "1. Open Slate Server and look at the Dashboard for a warning\n"
            "2. If it says no database password is configured, set it in "
            "Settings and restart the server - the access rules are written "
            "when the accounts are created, and that step is skipped without "
            "a password\n"
            "3. If it says nothing, the studio network this machine is on "
            "(%s) is outside the addresses the server allows" % host)

    if "password authentication" in text:
        return (
            "The server is running and it refused the password.\n\n"
            "1. The password on this workstation is not the one the database "
            "has - they are set together at install time and drift apart when "
            "one end is reinstalled\n"
            "2. Check Database Password in Slate Server's Settings, and use "
            "the same one here\n"
            "3. Reinstalling only this workstation will not fix it if the "
            "server is the end that changed")

    if "does not exist" in text and "role" in text:
        return (
            "The server is running, and the account '%s' was never created "
            "on it.\n\n"
            "1. Restart Slate Server - it creates this account on start\n"
            "2. If it does not, its Dashboard will say why; the usual reason "
            "is no database password configured on the server" % user)

    if "does not exist" in text and "database" in text:
        return (
            "The server is running and has no database called '%s'.\n\n"
            "1. Check Database Name in Slate Server's Settings\n"
            "2. A name that does not match is not an error to PostgreSQL - it "
            "creates an empty database rather than complaining, so the studio "
            "can end up with two and its work in the other one" % dbname)

    return (
        "Troubleshooting:\n"
        "1. Verify database server is running\n"
        "2. Check network connectivity (ping %s)\n"
        "3. Ensure firewall allows port %s\n"
        "4. Confirm database '%s' exists\n"
        "5. Verify credentials are correct" % (host, port, dbname))


class PostgresManager:
    """
    Enterprise Database Manager using PostgreSQL with Connection Pooling.
    Drop-in replacement for DatabaseManager with improved performance.
    
    Features:
    - Connection pooling for reduced latency
    - Thread-safe connection management
    - Automatic pool lifecycle management
    - Connection health monitoring
    """
    
    _instance = None
    _connection_pool = None
    _pool_lock = threading.RLock()
    _pool_stats = {'connections_created': 0, 'connections_reused': 0, 'pool_hits': 0}
    _atexit_registered = False
    
    # Circuit breaker for database operations
    _circuit_breaker = CircuitBreaker(
        failure_threshold=5,
        timeout=120,
        expected_exceptions=(psycopg2.OperationalError, psycopg2.InterfaceError, ConnectionError, PoolError),
        name="PostgresCircuitBreaker"
    )
    
    # SHUTDOWN LOCK
    _is_shutting_down = False

    @classmethod
    def shutdown_system(cls):
        """
        Shut down the PostgresManager connection pool.
        Allows re-initialization if called during a config reload.
        """
        cls._is_shutting_down = True
        try:
            logging.info("PostgresManager: Connection pool shutdown initiated.")
            if cls._instance:
                 cls._instance._close_pool()
                 cls._instance._initialized = False
        finally:
            cls._is_shutting_down = False
    
    # Enhanced retry strategy
    _retry_strategy = RetryStrategy(
        max_attempts=5,
        base_delay=1.0,
        max_delay=20.0,
        exponential_base=2.0,
        jitter=True,
        exceptions=(psycopg2.OperationalError, psycopg2.InterfaceError),
        name="PostgresRetry"
    )

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PostgresManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # 1. Check for shutdown lock
        if self.__class__._is_shutting_down:
            logging.warning("PostgresManager: Attempted instantiation during shutdown. Request denied.")
            return

        if hasattr(self, '_initialized') and self._initialized: return
        
        # Note: QMutex removed - connection pooling provides thread safety
        
        # Load configuration from GlobalConfig (client_config.json)
        config = {}
        try:
            from .global_config import GlobalConfig
            config = GlobalConfig.get('db_config', {}) or {}
            # Also try individual keys for backwards compatibility
            if not config:
                config = {
                    'host': GlobalConfig.get('db_host'),
                    'hosts': GlobalConfig.get('db_hosts'),
                    'port': GlobalConfig.get('db_port'),
                    'name': GlobalConfig.get('db_name'),
                    'user': GlobalConfig.get('db_user'),
                    'minconn': GlobalConfig.get('min_db_connections'),
                    'maxconn': GlobalConfig.get('max_db_connections')
                }
        except Exception as e:
            logging.debug(f"Could not load GlobalConfig: {e}")
        
        # Helper to get from keyring safely
        def get_from_keyring(key):
            try:
                import keyring
                val = keyring.get_password("Slate", key)
                return val
            except Exception:
                return None

        # Set database connection parameters
        # Priority: Config File > Keyring > Default
        primary_host = config.get('host') or get_from_keyring("db_host") or "127.0.0.1"
        raw_hosts = config.get('hosts') or config.get('db_hosts') or []
        if isinstance(raw_hosts, str):
            raw_hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
        elif not isinstance(raw_hosts, list):
            raw_hosts = []
        self.host_candidates = list(OrderedDict.fromkeys([primary_host] + raw_hosts)) or [primary_host]
        self.host = self.host_candidates[0]
        
        # Port handling
        p = config.get('port') or get_from_keyring("db_port")
        self.port = int(p) if p else 5440

        # PgBouncer, if the studio is running it, shares a small number of real
        # database connections between everybody. Clients try it first and fall
        # back to the database directly, so a studio that has not installed it
        # yet carries on working exactly as before.
        try:
            from slate.core.infra.global_config import GlobalConfig
            pooler = GlobalConfig.get("db_pooler_port", 6432)
        except Exception:
            pooler = 6432
        try:
            self.pooler_port = int(pooler) if pooler else 0
        except (TypeError, ValueError):
            self.pooler_port = 0
        # Which one actually worked, for the log and the connection banner.
        self.connected_via = ""

        self.dbname = config.get('name') or get_from_keyring("db_name") or "ut_vfx"
        self.user = config.get('user') or get_from_keyring("db_user") or "postgres"

        maxconn_cfg = config.get('maxconn') or config.get('max_db_connections') or 2  # noqa: E501
        minconn_cfg = config.get('minconn') or config.get('min_db_connections') or 1
        try:
            self.maxconn = max(1, int(maxconn_cfg))
        except (TypeError, ValueError):
            self.maxconn = 2

        # Applied whatever the saved settings ask for, so the limit actually
        # reaches machines that already have the software installed.
        if self.maxconn > MAX_POOL_PER_CLIENT:
            logging.info(
                "Saved settings ask for %s database connections; using %s. "
                "A studio of 150 machines cannot afford more.",
                self.maxconn, MAX_POOL_PER_CLIENT,
            )
            self.maxconn = MAX_POOL_PER_CLIENT
        try:
            self.minconn = max(1, int(minconn_cfg))
        except (TypeError, ValueError):
            self.minconn = 1
        if self.minconn > self.maxconn:
            self.minconn = self.maxconn
        
        # SECURITY: Load password from secure storage (NEVER from plain config)
        # CHANGED: Lazy loading to allow GUI prompt to work (requires QApplication)
        self.password = None
        # self._connection_pool is already managed by class/init
        
        # Usage Stats / Cache
        self._embedding_cache = None
        self._vector_cache_lock = threading.RLock()
        self.project_repo = ProjectRepository(self)
        self.stock_repo = StockRepository(self)
        self.tracking_repo = TrackingRepository(self)
        self.user_repo = UserRepository(self)
        from .attendance_repository import AttendanceRepository
        self.attendance_repo = AttendanceRepository(self)
        
        logging.info(
            f"PostgresManager Instantiated (Hosts: {self.host_candidates}, "
            f"Port: {self.port}, DB: {self.dbname}, Pool: {self.minconn}-{self.maxconn}) - Lazy Init Mode"
        )
        
        # KEY FIX: Mark as initialized so we don't run this logic again!
        self._initialized = True

        
    def invalidate_vector_cache(self):
        """
        Invalidate the in-memory vector cache.
        Must be called whenever stock library assets or embeddings are modified.
        """
        with self._vector_cache_lock:
            self._embedding_cache = None
            if hasattr(self, 'ids_cache'):
                del self.ids_cache
            if hasattr(self, 'matrix_cache'):
                del self.matrix_cache
            if hasattr(self, 'norms_cache'):
                del self.norms_cache
        logging.debug("Vector search cache invalidated")

    def _ensure_not_shutting_down(self):
        """Raise a consistent error when DB access is requested during shutdown."""
        if self.__class__._is_shutting_down:
            raise ConnectionError("PostgresManager is shutting down; database operations are disabled.")
    
    def _load_password_secure(self) -> str:
        """
        Load database password from secure storage.
        Priority: Environment Variable > Keyring > Encrypted File > Error
        """
        import os
        from pathlib import Path
        
        # 1. Try environment variable (highest priority)
        password = os.getenv('DB_PASSWORD')
        if password:
            logging.info("Database password loaded from environment variable")
            return password
            
        # 2. Try GlobalConfig (User requested "Zero Config" Deployment Priority)
        # If a password is set in config.json/default_config.json, it should override local cache
        from .global_config import GlobalConfig
        config_password = GlobalConfig.get('db_password') or GlobalConfig.get('password')
        if config_password:
             # Basic sanity check to avoid empty strings if they somehow got in
             if str(config_password).strip():
                 logging.info("Database password loaded from GlobalConfig (config.json)")
                 return config_password
        
        # 3. Try Windows Credential Manager via keyring
        try:
            import keyring
            password = keyring.get_password("Slate", "db_password")
            if password:
                logging.info("Database password loaded from Windows Credential Manager")
                return password
        except ImportError:
            logging.debug("keyring library not available")
        except Exception as e:
            logging.debug(f"Could not access keyring: {e}")
        
        # 4. Try encrypted file (fallback)
        local_appdata = Path(os.getenv('LOCALAPPDATA', '')) / "Slate"
        encrypted_creds_file = local_appdata / ".db_credentials"
        
        if encrypted_creds_file.exists():
            try:
                from cryptography.fernet import Fernet
                
                # Load encryption key
                key_file = local_appdata / ".encryption_key"
                if key_file.exists():
                    with open(key_file, 'rb') as f:
                        key = f.read()
                    
                    cipher = Fernet(key)
                    
                    # Decrypt credentials
                    with open(encrypted_creds_file, 'rb') as f:
                        encrypted_data = f.read()
                    
                    decrypted_data = cipher.decrypt(encrypted_data)
                    import json
                    credentials = json.loads(decrypted_data.decode())
                    
                    password = credentials.get('db_password')
                    if password:
                        logging.info("Database password loaded from encrypted file")
                        return password
            except Exception as e:
                logging.warning(f"Could not read encrypted credentials file: {e}")

        # 5. No password found - fail with explicit guidance.
        error_msg = (
            "\n" + "="*70 + "\n"
            "DATABASE PASSWORD NOT CONFIGURED\n"
            "="*70 + "\n\n"
            "The database password must be configured before using Slate.\n\n"
            "Please run the credential setup script:\n\n"
            "    python tools/setup_credentials.py\n\n"
            "Or set the DB_PASSWORD environment variable.\n\n"
            "For more information, see docs/INSTALLATION.md\n"
            "="*70
        )
        logging.critical(error_msg)
        raise RuntimeError("Database password not configured. Run: python tools/setup_credentials.py")



    def _ports_to_try(self):
        """
        Where to look for the database, best first.

        PgBouncer shares a small number of real database connections between
        every machine in the studio. It is tried first; a studio that has not
        installed it yet falls through to the database itself and carries on
        exactly as before.
        """
        ports = []
        if self.pooler_port and self.pooler_port != self.port:
            ports.append(("PgBouncer", self.pooler_port))
        ports.append(("the database directly", self.port))
        return ports

    @staticmethod
    def _is_worth_retrying(exc: BaseException) -> bool:
        """
        Whether this failure might succeed if we simply wait and try again.

        A refused connection or a timeout is worth another go - the server may
        be starting, or the network may have blinked. A wrong password or a
        database that does not exist will fail identically every time, and
        retrying it five times with a growing wait turns an instant, clear
        error into most of a minute of silence before the same message.
        """
        if not isinstance(exc, psycopg2.OperationalError):
            return False

        permanent = (
            "does not exist",
            "authentication failed",
            "no password supplied",
            "role \"",
            "is not permitted to log in",
        )
        message = str(exc).lower()
        return not any(marker.lower() in message for marker in permanent)

    @retry(
        retry=retry_if_exception(lambda e: PostgresManager._is_worth_retrying(e)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(5),
        reraise=True
    )
    def _create_pool_with_retry(self, host: Optional[str] = None,
                                port: Optional[int] = None):
        """
        Create connection pool with automatic retry on network failures.
        
        Retries up to 3 times with exponential backoff:
        - Attempt 1: immediate
        - Attempt 2: wait 1 second
        - Attempt 3: wait 2 seconds
        
        Raises ConnectionError if all attempts fail.
        """
        target_host = host or self.host
        target_port = int(port or self.port)
        logging.debug(f"Attempting to create connection pool (host: {target_host}:{target_port})")
        
        return pool.ThreadedConnectionPool(
            minconn=self.minconn,
            maxconn=self.maxconn,
            host=target_host,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
            port=target_port,
            connect_timeout=30,  # Increased from 15 for high latency (17s ping reported)
            # Names this machine on the server, so a busy database can be traced
            # to a person rather than to one of 150 identical connections.
            application_name=_client_identity(),
        )

    def _create_pool_once(self, host: str, port: int):
        """
        One attempt, no waiting and no retries.

        Used for every connection option that has another one behind it. The
        pooler is tried before the database, and when the pooler is simply not
        running, retrying a refused port five times with a growing wait added
        almost thirty seconds to every start-up before falling through to the
        database that was there all along.
        """
        return pool.ThreadedConnectionPool(
            minconn=self.minconn,
            maxconn=self.maxconn,
            host=host,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
            port=int(port),
            connect_timeout=5,
            application_name=_client_identity(),
        )

    def _init_pool(self):
        """
        Initialize connection pool with lazy loading and thread-safety.
        Pool is created on first database access with automatic retry.
        """
        self._ensure_not_shutting_down()
        if self._connection_pool is None:
            with self._pool_lock:
                # Double-checked locking
                if self._connection_pool is None:
                    self._ensure_not_shutting_down()
                    # RETRY LOOP for User Authentication
                    max_auth_retries = 3
                    for attempt in range(max_auth_retries):
                        try:
                            self._ensure_not_shutting_down()
                            # LAZY PASSWORD LOAD (Crash fix for GUI)
                            if not self.password:
                                logging.info("Lazy loading database password...")
                                try:
                                    self.password = self._load_password_secure()
                                    if not self.password:
                                        # User cancelled or failed to load
                                        raise RuntimeError("No password provided.")
                                except Exception as e:
                                    logging.critical(f"Critical Auth Failure: {e}")
                                    raise
                                    
                            last_error = None
                            connected = False
                            for host in self.host_candidates:
                                self._ensure_not_shutting_down()
                                self.host = host
                                # PgBouncer first, then the database directly.
                                # A studio that has not installed the pooler yet
                                # simply falls through to the second option.
                                options = self._ports_to_try()
                                for index, (label, try_port) in enumerate(options):
                                    is_last = index == len(options) - 1
                                    try:
                                        logging.info(
                                            f"Connecting to {host}:{try_port} ({label})..."
                                        )
                                        # Only the last option is worth waiting
                                        # and retrying for; the others have
                                        # somewhere to fall through to.
                                        candidate_pool = (
                                            self._create_pool_with_retry(host=host, port=try_port)
                                            if is_last else
                                            self._create_pool_once(host, try_port)
                                        )
                                        if self.__class__._is_shutting_down:
                                            try:
                                                candidate_pool.closeall()
                                            except Exception as exc:
                                                logging.debug("Pool closeall during shutdown skipped: %s", exc)
                                            raise ConnectionError("Shutdown requested during pool initialization.")
                                        self._connection_pool = candidate_pool
                                        self.port = try_port
                                        self.connected_via = label
                                        connected = True
                                        if label != "PgBouncer" and self.pooler_port:
                                            # Worth saying out loud: without the
                                            # pooler every machine holds its own
                                            # database connections, which is what
                                            # runs out at about 40 people.
                                            logging.warning(
                                                "PgBouncer was not reachable on port "
                                                f"{self.pooler_port}; connected straight "
                                                "to the database instead."
                                            )
                                        self._ensure_db()
                                        break
                                    except psycopg2.OperationalError as port_err:
                                        last_error = port_err
                                        logging.info(
                                            f"{host}:{try_port} ({label}) did not answer: {port_err}"
                                        )
                                        continue
                                if connected:
                                    break
                                logging.warning(
                                    f"Database host unavailable: {host} | {last_error}. "
                                    "Trying next host..."
                                )

                            if not connected:
                                # Fallback: Try forced network discovery if configured hosts failed
                                if attempt == 0:  # Only attempt discovery on the first failure round
                                    try:
                                        from .network_discovery import discover_server
                                        logging.info("Configured hosts failed. Attempting forced UDP discovery...")
                                        server_ip, db_port = discover_server(timeout=1.5)
                                        if server_ip and server_ip not in self.host_candidates:
                                            logging.info(f"Forced discovery found server at {server_ip}:{db_port}. Retrying connection...")
                                            # Insert at front to try it first on the next attempt
                                            self.host_candidates.insert(0, server_ip)
                                            from .global_config import GlobalConfig
                                            GlobalConfig.set('db_host', server_ip)
                                            if db_port:
                                                GlobalConfig.set('db_port', db_port)
                                                self.port = int(db_port)
                                            continue  # Retry outer auth loop with new host
                                    except Exception as discovery_exc:
                                        logging.warning(f"Forced discovery failed: {discovery_exc}")

                                raise last_error or psycopg2.OperationalError("No database hosts configured")
                            
                            # Register cleanup on app exit
                            if not self.__class__._atexit_registered:
                                atexit.register(self._close_pool)
                                self.__class__._atexit_registered = True
                            
                            logging.info(f"Connection pool initialized ({self.minconn}-{self.maxconn} connections)")
                            break # Success!
                            
                        except (psycopg2.OperationalError, RuntimeError) as e:
                            # Check if it's an AUTH error
                            error_str = str(e).lower()
                            is_auth_error = "password" in error_str or "authentication" in error_str
                            
                            if is_auth_error and attempt < max_auth_retries - 1:
                                logging.warning(f"Authentication failed: {e}. Retrying credential lookup...")
                                
                                # Clear cached password so secure loaders re-read sources.
                                self.password = None
                                logging.info("Retrying auth with secure password sources (keyring entry preserved).")
                                continue
                            
                            logging.error(f"Failed to initialize connection pool after retries: {e}")
                            raise ConnectionError(
                                f"Cannot connect to database at {self.host}:{self.port}\n\n"
                                f"Error: {e}\n\n"
                                + what_to_do(e, self.host, self.port,
                                             self.dbname, self.user)
                            )
    
    def _close_pool(self):
        """Close all connections in the pool gracefully."""
        pool_to_close = None
        with self._pool_lock:
            if self._connection_pool:
                pool_to_close = self._connection_pool
                self._connection_pool = None
        if pool_to_close:
            try:
                pool_to_close.closeall()
                logging.info("Database connection pool closed")
            except Exception as e:
                logging.exception(f"Error closing connection pool: {e}")
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics for monitoring."""
        stats = self._pool_stats.copy()
        with self._pool_lock:
            stats['pool_initialized'] = self._connection_pool is not None
        return stats

    def _ensure_db(self):
        """Initialize schema on first connection if missing."""
        schema = """
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            template_used TEXT DEFAULT '',
            target_directory TEXT DEFAULT '',
            total_folders INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS operations (
            id SERIAL PRIMARY KEY,
            project_id INTEGER,
            operation_type TEXT DEFAULT '',
            start_time TEXT,
            end_time TEXT,
            duration REAL DEFAULT 0,
            items_processed INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS task_details (
            id SERIAL PRIMARY KEY,
            operation_id INTEGER,
            item_name TEXT DEFAULT '',
            source_path TEXT DEFAULT '',
            dest_path TEXT DEFAULT '',
            file_size BIGINT DEFAULT 0,
            duration REAL DEFAULT 0,
            status TEXT DEFAULT '',
            error_msg TEXT DEFAULT '',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stock_library (
            id SERIAL PRIMARY KEY,
            file_path TEXT UNIQUE,
            file_name TEXT DEFAULT '',
            file_size BIGINT DEFAULT 0,
            file_type TEXT DEFAULT '',
            thumb_path TEXT DEFAULT '',
            proxy_path TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            embedding TEXT,
            ingest_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tracking_projects (
            code TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            config_json TEXT DEFAULT '{}',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS tracking_shots (
            id SERIAL PRIMARY KEY,
            project_code TEXT NOT NULL,
            reel TEXT DEFAULT '',
            shot_name TEXT NOT NULL,
            status TEXT DEFAULT '',
            priority INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version INTEGER DEFAULT 0,
            -- The reel is part of a shot's identity: SH010 can exist in ReelA
            -- and ReelB, and they are different shots.
            UNIQUE(project_code, reel, shot_name)
        );

        CREATE TABLE IF NOT EXISTS tracking_tasks (
            id SERIAL PRIMARY KEY,
            shot_id INTEGER,
            project_code TEXT,
            department TEXT DEFAULT '',
            status TEXT DEFAULT '',
            artist_name TEXT DEFAULT '',
            artist_id INTEGER,
            bid_days REAL DEFAULT 0,
            target_date TEXT DEFAULT '',
            UNIQUE(project_code, shot_id, department)
        );

        CREATE TABLE IF NOT EXISTS tracking_versions (
            id SERIAL PRIMARY KEY,
            project_code TEXT NOT NULL,
            reel TEXT DEFAULT '',
            shot_name TEXT NOT NULL,
            version_name TEXT NOT NULL,
            department TEXT DEFAULT '',
            artist TEXT DEFAULT '',
            status TEXT DEFAULT '',
            sent_to TEXT DEFAULT '',
            sent_date TEXT DEFAULT '',
            media_path TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_code, reel, shot_name, version_name)
        );

        CREATE TABLE IF NOT EXISTS tracking_version_notes (
            id SERIAL PRIMARY KEY,
            version_id INTEGER NOT NULL,
            source TEXT DEFAULT '',
            author TEXT DEFAULT '',
            note_date TEXT DEFAULT '',
            text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ut_users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            job_title TEXT DEFAULT '',
            roles TEXT DEFAULT '[]',
            profile_pic_path TEXT DEFAULT '',
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ut_roles (
            id SERIAL PRIMARY KEY,
            role_name TEXT UNIQUE NOT NULL,
            permissions TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS change_history (
            id SERIAL PRIMARY KEY,
            project_code TEXT DEFAULT '',
            entity_type TEXT DEFAULT '',
            entity_id TEXT DEFAULT '',
            user_id TEXT,
            action_type TEXT DEFAULT '',
            field_changed TEXT DEFAULT '',
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS ut_attendance (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            check_in_time TIMESTAMP NOT NULL,
            check_out_time TIMESTAMP,
            status TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER,
            action TEXT DEFAULT '',
            target TEXT DEFAULT '',
            details TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS attendance_log (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            day_date TEXT NOT NULL,
            punch_in TEXT,
            punch_out TEXT,
            pc_name TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            UNIQUE(user_id, day_date)
        );

        CREATE TABLE IF NOT EXISTS slate_json_write_locks (
            lock_name TEXT PRIMARY KEY,
            holder TEXT DEFAULT '',
            acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS it_deployments (
            id SERIAL PRIMARY KEY,
            package_name TEXT NOT NULL,
            target_machine TEXT NOT NULL,
            deployed_by TEXT NOT NULL,
            status TEXT NOT NULL,
            deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_change_history_project ON change_history(project_code);
        CREATE INDEX IF NOT EXISTS idx_tracking_tasks_project ON tracking_tasks(project_code);
        CREATE INDEX IF NOT EXISTS idx_tracking_shots_project ON tracking_shots(project_code);
        CREATE INDEX IF NOT EXISTS idx_attendance_user ON attendance_log(user_id);
        
        CREATE TABLE IF NOT EXISTS hardware_inventory (
            id SERIAL PRIMARY KEY,
            machine_name TEXT UNIQUE NOT NULL,
            type TEXT DEFAULT '',
            status TEXT DEFAULT 'Available',
            gpu TEXT DEFAULT '',
            ram TEXT DEFAULT '',
            storage TEXT DEFAULT '',
            assigned_to TEXT DEFAULT ''
        );
        
        CREATE TABLE IF NOT EXISTS it_tickets (
            id SERIAL PRIMARY KEY,
            submitted_by TEXT,
            category TEXT,
            description TEXT,
            status TEXT DEFAULT 'Open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS it_licenses (
            id SERIAL PRIMARY KEY,
            software_name TEXT NOT NULL,
            license_key TEXT,
            seats_total INTEGER DEFAULT 1,
            seats_used INTEGER DEFAULT 0,
            expiry_date TEXT
        );
        
        CREATE TABLE IF NOT EXISTS payroll_records (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            month TEXT NOT NULL,
            base_salary REAL,
            total REAL,
            status TEXT DEFAULT 'Pending'
        );
        
        CREATE TABLE IF NOT EXISTS onboarding_workflows (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            task_name TEXT NOT NULL,
            department TEXT,
            is_completed BOOLEAN DEFAULT FALSE
        );
        
        CREATE TABLE IF NOT EXISTS prod_scheduling (
            id SERIAL PRIMARY KEY,
            project_code TEXT,
            milestone TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT,
            depends_on_id INTEGER
        );
        
        CREATE TABLE IF NOT EXISTS prod_bidding (
            id SERIAL PRIMARY KEY,
            project_name TEXT,
            client_name TEXT,
            estimated_budget REAL,
            status TEXT DEFAULT 'Draft'
        );
        """
        
        alter_sql = """
        DO $$ 
        BEGIN 
            ALTER TABLE change_history ALTER COLUMN user_id TYPE TEXT;
            ALTER TABLE stock_library ALTER COLUMN file_size TYPE BIGINT;
            ALTER TABLE task_details ALTER COLUMN file_size TYPE BIGINT;
        EXCEPTION WHEN OTHERS THEN 
            -- Ignore if table doesn't exist yet or other errors
        END $$;
        """

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(schema)
                    cur.execute(alter_sql)
                conn.commit()
            logging.info("PostgreSQL schema ensured.")
        except Exception as e:
            logging.error(f"Failed to ensure PostgreSQL schema: {e}")

    @contextmanager
    def get_connection(self):
        """
        Get database connection from pool.
        Connection is automatically returned to pool after use.
        
        Usage:
            with db-get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ...")
        """
        self._ensure_not_shutting_down()
        # Initialize pool if needed (lazy init)
        self._init_pool()
        
        conn = None
        current_pool = None
        try:
            with self._pool_lock:
                self._ensure_not_shutting_down()
                current_pool = self._connection_pool
            if current_pool is None:
                raise ConnectionError("Database pool is not initialized.")

            # Get connection from pool
            conn = current_pool.getconn()
            self._pool_stats['pool_hits'] += 1
            
            yield conn
            
        except psycopg2.OperationalError as e:
            logging.error(f"Database connection error: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception as exc:
                    logging.debug("Rollback skipped after operational error: %s", exc)
            raise ConnectionError(f"Database connection failed: {e}")
        except Exception as e:
            logging.exception(f"Unexpected database error: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception as exc:
                    logging.debug("Rollback skipped after unexpected DB error: %s", exc)
            raise
        finally:
            # Return connection to pool (don't close!)
            if conn and current_pool:
                try:
                    if self.__class__._is_shutting_down:
                        conn.close()
                    else:
                        current_pool.putconn(conn)
                except Exception as e:
                    logging.exception(f"Error returning connection to pool: {e}")
            
    def execute_query(self, query: str, params: tuple = None, fetch: str = "all") -> Any:
        """
        Execute database query with circuit breaker protection and smart transaction management.
        
        Automatically commits on write operations (INSERT, UPDATE, DELETE).
        READ operations (SELECT) don't trigger unnecessary commits.
        Auto-rollback on errors. Includes retry logic and circuit breaker protection.
        
        Args:
            query: SQL query string
            params: Query parameters (tuple)
            fetch: Result fetch mode ("all", "one", "rowcount", "lastrowid", or None)
            
        Returns:
            Query results based on fetch mode
            
        Raises:
            CircuitBreakerError: If database circuit is open
        """
        def _execute_with_retry():
            """Inner function that performs the actual query execution"""
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, params)
                    
                    # SMART AUTO-COMMIT: Only commit on write operations
                    is_write_operation = False
                    if query:
                        is_write_operation = bool(re.search(
                            r'^\s*(?:--.*?\n\s*|\/\*.*?\*\/\s*)*(?:WITH\s+.*?\s+)?\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b', 
                            query, 
                            re.IGNORECASE | re.DOTALL
                        ))
                    
                    if is_write_operation:
                        conn.commit()
                        logging.debug("Transaction committed for write operation")
                    # READ operations (SELECT) don't need commit - more efficient!
                    
                    # Fetch results
                    if fetch == "all": 
                        return cur.fetchall()
                    elif fetch == "one":
                        return cur.fetchone()
                    elif fetch == "rowcount":
                        return cur.rowcount
                    elif fetch == "lastrowid": 
                        # Postgres requires RETURNING id
                        # Assuming the query already had RETURNING id
                        res = cur.fetchone()
                        if not res: return None
                        return list(res.values())[0] if hasattr(res, 'values') else res[0]
                    elif fetch == "none":
                        return None
        
        try:
            # Apply circuit breaker and retry strategy
            return self._circuit_breaker.call(
                lambda: self._retry_strategy.execute(_execute_with_retry)
            )
        except CircuitBreakerError as e:
            # The database is unreachable, not empty. Saying so is the whole
            # point: this is what a connection shortage looks like, and it must
            # not be mistaken for a project with no shots in it.
            logging.error(f"Database circuit breaker is OPEN: {e}")
            raise DatabaseUnavailableError(
                "The database is not responding, so nothing was read or saved. "
                "Your work has not been lost - try again in a moment."
            ) from e
        except (ConnectionError, psycopg2.OperationalError,
                psycopg2.InterfaceError) as e:
            logging.error(f"Database unreachable: {query[:100]}... Error: {e}")
            raise DatabaseUnavailableError(
                "The database could not be reached, so nothing was read or "
                "saved. Your work has not been lost - try again in a moment."
            ) from e
        except Exception as e:
            # A genuine SQL problem (bad column, constraint) - unchanged
            # behaviour, because that is a bug to fix, not an outage to survive.
            logging.exception(f"Query failed after retries: {query[:100]}... Error: {e}")
            return None
    
    def execute_update(self, query: str, params: tuple = None) -> bool:
        """
        Execute an update query (INSERT, UPDATE, DELETE).
        
        This method is a wrapper around execute_query for write operations,
        explicitly returning a boolean indicating success.
        """
        try:
            # execute_query already handles commit for write operations
            result = self.execute_query(query, params, fetch="rowcount")
            return result is not None and result >= 0 # rowcount can be 0 for no-op updates
        except DatabaseUnavailableError:
            # An outage is not a failed update, it is an update that never
            # happened. Callers treat False as "rejected"; this must not be
            # mistaken for that.
            raise
        except Exception as e:
            logging.exception(f"Update failed: {query[:100]}... Error: {e}")
            return False
    
    @contextmanager
    def transaction(self):
        """
        Manual transaction control for multi-step atomic operations.
        
        Use when you need explicit transaction boundaries across
        multiple operations that must all succeed or all fail together.
        
        Usage:
            with db.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO projects ...")
                project_id = cursor.fetchone()['id']
                cursor.execute("INSERT INTO operations ...", (project_id,))
                # Both succeed or both rollback
        """
        self._ensure_not_shutting_down()
        self._init_pool()  # Ensure pool exists
        conn = None
        current_pool = None
        try:
            with self._pool_lock:
                self._ensure_not_shutting_down()
                current_pool = self._connection_pool
            if current_pool is None:
                raise ConnectionError("Database pool is not initialized.")
            conn = current_pool.getconn()
            yield conn
            conn.commit()
            logging.debug("Manual transaction committed")
        except Exception as e:
            if conn:
                conn.rollback()
                logging.warning(f"Transaction rolled back: {e}")
            raise
        finally:
            if conn and current_pool:
                try:
                    if self.__class__._is_shutting_down:
                        conn.close()
                    else:
                        current_pool.putconn(conn)
                except Exception as e:
                    logging.exception(f"Error returning transaction connection to pool: {e}")

    # --- PROJECT MANAGEMENT ---

    def get_all_projects(self, limit: Optional[int] = 1000):
        return self.project_repo.get_all_projects(limit=limit)
        
    def get_all_projects_summary(self, limit: Optional[int] = 1000) -> List[Dict[str, Any]]:
        return self.project_repo.get_all_projects_summary(limit=limit)

    def record_project(self, name: str, template_used: str, target_directory: str, total_folders: int = 0) -> int:
        return self.project_repo.record_project(name, template_used, target_directory, total_folders)

    def start_operation(self, project_id: int, operation_type: str) -> int:
        return self.project_repo.start_operation(project_id, operation_type)

    def update_operation(self, op_id: int, duration: float, items: int, errors: int, success: bool) -> None:
        self.project_repo.update_operation(op_id, duration, items, errors, success)

    def record_task_detail(self, op_id: int, name: str, src: str, dst: str, size: int, duration: float, status: str, error: str = "") -> None:
        self.project_repo.record_task_detail(op_id, name, src, dst, size, duration, status, error)

    # --- MAINTENANCE ---

    def perform_maintenance(self, days_to_keep=30):
        # Postgres is robust, but cleaning old logs is still good
        try:
            cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            self.execute_query("DELETE FROM task_details WHERE timestamp < %s", (cutoff,), fetch="none")
            
            # VACUUM in Postgres cannot run inside a transaction block easily via execute_query
            # but usually autovacuum handles this. We can skip explicit vacuum for now.
        except Exception as e:
            logging.exception(f"Maintenance error: {e}")

    def cleanup_stale_sessions(self):
        end = datetime.now().isoformat()
        q = """
            UPDATE operations 
            SET end_time = %s, success = 0, errors = errors + 1 
            WHERE end_time IS NULL
        """
        self.execute_query(q, (end,), fetch="none")

    # --- STOCK LIBRARY ---

    def add_stock_asset(self, path: str, thumb_path: str = "", proxy_path: str = "", tags: Optional[List[str]] = None, metadata: dict = None) -> int:
        return self.stock_repo.add_stock_asset(path, thumb_path, proxy_path, tags, metadata)

    def add_stock_assets_batch(self, assets_list: List[Dict[str, Any]]) -> None:
        self.stock_repo.add_stock_assets_batch(assets_list)

    def update_stock_asset_paths(self, asset_id, thumb_path=None, proxy_path=None, file_path=None):
        self.stock_repo.update_stock_asset_paths(asset_id, thumb_path, proxy_path, file_path)

    def update_asset_tags(self, asset_id, new_tags):
        return self.stock_repo.update_asset_tags(asset_id, new_tags)

    def update_asset_metadata(self, asset_id, metadata_str, tags_str):
        return self.stock_repo.update_asset_metadata(asset_id, metadata_str, tags_str)

    def count_stock_assets(self, search_query=None, file_types=None, asset_ids=None) -> int:
        """How many assets match the filter, so the count agrees with the list."""
        return self.stock_repo.count_stock_assets(search_query, file_types, asset_ids)

    def list_stock_paths(self):
        """Just the paths, for the ingest to tell what it already holds."""
        return self.stock_repo.list_stock_paths()

    def get_stock_count(self) -> int:
        """Returns total number of assets in the stock_library table."""
        return self.stock_repo.get_stock_count()

    def get_all_stock_assets(self, limit: int = None, offset: int = 0, search_query: str = None, file_types: List[str] = None, asset_ids: List[str] = None) -> List[Dict]:
        return self.stock_repo.get_all_stock_assets(limit, offset, search_query, file_types, asset_ids)

    def get_stock_file_types(self) -> List[str]:
        """Returns list of unique file extensions in the library."""
        return self.stock_repo.get_stock_file_types()

    def get_stock_tags(self) -> List[str]:
        """Returns list of unique tags in the library."""
        return self.stock_repo.get_stock_tags()

    def remove_stock_asset(self, asset_id) -> bool:
        """Delete one stock asset by database id."""
        return self.stock_repo.remove_stock_asset(asset_id)

    def remove_stock_asset_by_path(self, file_path: str) -> bool:
        """Delete one stock asset by file path."""
        return self.stock_repo.remove_stock_asset_by_path(file_path)

    def clear_stock_library(self):
        return self.stock_repo.clear_stock_library()

    def clear_stock_assets(self):
        """Legacy compatibility alias."""
        return self.clear_stock_library()

    # --- VECTOR SEARCH (Postgres Implementation) ---
    
    def update_asset_embedding(self, asset_id, embedding_json):
        # Postgres JSONB
        q = "UPDATE stock_assets SET embedding_json=%s WHERE id=%s"
        success = (self.execute_query(q, (embedding_json, asset_id), fetch="rowcount") or 0) > 0
        if success:
            self.invalidate_vector_cache()
        return success
        
    def search_similar_assets(self, query_embedding: List[float], limit: int = 50) -> List[Dict]:
        """
        Hybrid Approach: Fetch vectors and do numpy logic in Python (Proven fast for <100k items)
        pgvector extension is better but requires installation. We stick to Python for "No Install" requirement on server side plugins.
        """
        # Reuse the exact logic from DatabaseManager regarding numpy caching
        # Just fetching data from Postgres
        
        import numpy as np
        
        with self._vector_cache_lock:
            if self._embedding_cache is None:
                q = "SELECT id, embedding FROM stock_library WHERE embedding IS NOT NULL"
                rows = self.execute_query(q) or []
                if not rows:
                    return []

                ids = []
                vecs = []
                for r in rows:
                    try:
                        # embedding col in Postgres is JSONB, so psycopg2 adapters might auto-convert to list/dict
                        # Check type
                        v = r['embedding']
                        if isinstance(v, str):
                            v = json.loads(v)

                        if len(v) > 0:
                            ids.append(r['id'])
                            vecs.append(v)
                    except Exception:
                        continue

                if not vecs:
                    return []

                matrix = np.array(vecs, dtype=np.float32)
                norms = np.linalg.norm(matrix, axis=1)
                norms[norms == 0] = 1e-10

                self.ids_cache = np.array(ids)
                self.matrix_cache = matrix
                self.norms_cache = norms
                self._embedding_cache = True

            ids_cache = self.ids_cache
            matrix_cache = self.matrix_cache
            norms_cache = self.norms_cache
            
        # Perform Dot Product (Cosine Similarity)
        # Cosine Sim = (A . B) / (||A|| * ||B||)
        
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0: query_norm = 1e-10
        
        # Dot product of query vs all items
        dot_products = np.dot(matrix_cache, query_vec)
        
        # Calculate similarities
        result_norms = norms_cache * query_norm
        similarities = dot_products / result_norms
        
        # Get top N indices
        # argsort returns indices that would sort the array (ascending), so we take tail and reverse
        top_indices = np.argsort(similarities)[-limit:][::-1]
        
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            # Filter out weak matches if needed, but for now return all top N
            if score > 0.0:
                asset_id = int(ids_cache[idx])
                results.append({'id': asset_id, 'score': score})
                
        return results

    # --- DASHBOARD / TRACKING ---

    def save_tracking_project(self, code: str, name: str, config_json: str):
        return self.tracking_repo.save_tracking_project(code, name, config_json)

    def get_tracking_project(self, code: str) -> Optional[Dict]:
        return self.tracking_repo.get_tracking_project(code)

    def get_all_tracking_projects(self) -> List[Dict]:
        return self.tracking_repo.get_all_tracking_projects()

    def delete_tracking_project(self, code: str) -> bool:
        return self.tracking_repo.delete_tracking_project(code)

    def save_tracking_shots(self, project_code: str, shots_data: List[Tuple[str, str, int, str]]):
        return self.tracking_repo.save_tracking_shots(project_code, shots_data)

    def get_tracking_shots(self, project_code: str) -> List[Dict]:
        return self.tracking_repo.get_tracking_shots(project_code)

    def update_tracking_shot_safe(self, project_code: str, shot_name: str, data_json: str,
                                  current_version: int, reel: str = None) -> bool:
        # The reel is part of a shot's identity - two reels may hold a shot of
        # the same name. This wrapper used to drop it, so every dashboard save
        # against PostgreSQL failed outright with a TypeError. The repository
        # below has always accepted it.
        return self.tracking_repo.update_tracking_shot_safe(
            project_code, shot_name, data_json, current_version, reel=reel)

    def _get_tracking_tasks_columns(self) -> set:
        return self.tracking_repo._get_tracking_tasks_columns()

    def get_tracking_tasks(self, project_code: str) -> List[Dict]:
        return self.tracking_repo.get_tracking_tasks(project_code)

    def save_tracking_tasks(self, project_code: str, tasks_data: List[Dict]):
        return self.tracking_repo.save_tracking_tasks(project_code, tasks_data)

    def sync_users(self, users_dict: Dict[str, Any]):
        return self.user_repo.sync_users(users_dict)

    def get_user_profile_pic(self, username: str) -> Optional[str]:
        return self.user_repo.get_user_profile_pic(username)

    def update_user_profile_pic(self, username: str, path: str) -> bool:
        return self.user_repo.update_user_profile_pic(username, path)

    def get_user_id(self, name_or_user: str) -> Optional[int]:
        return self.user_repo.get_user_id(name_or_user)

    # Stubs for less critical stats
    def get_error_statistics(self): return {'total_errors': 0, 'recent_errors': []}
    def get_asset_statistics(self): return {'total_assets': 0, 'recent_assets': []}
    def get_compliance_data(self): return {'audit_trail': []}
    def export_data(self, table, path): return True

    def log_change_event(self, project_code, entity_type, entity_id, user_id, action_type, field, old_val, new_val):

        q = """
            INSERT INTO change_history 
            (project_code, entity_type, entity_id, user_id, action_type, field_changed, old_value, new_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        self.execute_query(q, (project_code, entity_type, entity_id, user_id, action_type, field, str(old_val), str(new_val)), fetch="none")

    def get_history(self, project_code: str = None, shot_name: str = None, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Fetch audit history globally, for a project, or a specific shot.
        """
        try:
            where_clauses = ["1=1"]  # always true fallback
            params = []

            if project_code:
                where_clauses.append("ch.project_code=%s")
                params.append(project_code)

            if shot_name:
                where_clauses.append("(ch.entity_id=%s OR ch.entity_id LIKE %s)")
                params.append(shot_name)
                params.append(f"{shot_name}_%")

            params.append(int(limit))

            query = f"""
                SELECT
                    ch.timestamp,
                    COALESCE(u.display_name, u.username, 'Unknown') AS user_name,
                    ch.field_changed,
                    ch.old_value,
                    ch.new_value,
                    ch.entity_type,
                    ch.entity_id,
                    ch.action_type
                FROM change_history ch
                LEFT JOIN ut_users u ON u.username = ch.user_id::text
                WHERE {' AND '.join(where_clauses)}
                ORDER BY ch.timestamp DESC
                LIMIT %s
            """
            rows = self.execute_query(query, tuple(params)) or []
            return [dict(r) for r in rows]
        except Exception as e:
            logging.exception(f"Failed to fetch history for project={project_code}, shot={shot_name}: {e}")
            return []
