import asyncpg
import logging
import re
from typing import Dict, List, Tuple, Optional, Any
from collections import OrderedDict
from datetime import datetime

class AsyncPostgresManager:
    """
    Asynchronous Enterprise Database Manager using asyncpg.
    Provides non-blocking database queries to prevent UI freezing.
    """
    
    _instance = None
    _pool = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AsyncPostgresManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized: return
        
        self.host = "127.0.0.1"
        self.port = 5440
        self.dbname = "slate"
        self.user = "postgres"
        
        # Load configuration
        try:
            from .global_config import GlobalConfig
            config = GlobalConfig.get('db_config', {}) or {}
            self.host = config.get('host') or "127.0.0.1"
            p = config.get('port')
            self.port = int(p) if p else 5440
            self.dbname = config.get('name') or "slate"
            self.user = config.get('user') or "postgres"
        except Exception as e:
            logging.debug(f"AsyncPostgresManager: Could not load GlobalConfig: {e}")
            
        self._initialized = True

    def _get_password(self):
        try:
            from slate.core.infra.postgres_manager import PostgresManager
            sync_manager = PostgresManager()
            return sync_manager._load_password_secure()
        except Exception as e:
            logging.exception(f"AsyncPostgresManager: Failed to securely load database password: {e}")
            raise RuntimeError("Database password not configured.")

    @staticmethod
    def _max_pool_size() -> int:
        """How many connections semantic search may hold on one machine."""
        try:
            from slate.core.infra.global_config import GlobalConfig
            value = int(GlobalConfig.get("max_semantic_connections", 4) or 4)
        except Exception:
            value = 4
        return max(1, min(value, 8))

    @staticmethod
    def _identity() -> str:
        from slate.core.infra.postgres_manager import _client_identity
        return f"{_client_identity()} (search)"[:63]

    async def init_pool(self):
        """Initializes the asyncpg connection pool."""
        if self._pool is not None:
            return
            
        password = self._get_password()
        logging.info(f"AsyncPostgresManager: Initializing pool to {self.host}:{self.port}/{self.dbname}")
        
        try:
            self._pool = await asyncpg.create_pool(
                user=self.user,
                password=password,
                database=self.dbname,
                host=self.host,
                port=self.port,
                min_size=1,
                # Capped, and configurable. Twenty per machine is 3,000 across
                # a 150-seat studio against a server limit of 100.
                max_size=self._max_pool_size(),
                command_timeout=60,
                server_settings={"application_name": self._identity()}
            )
            logging.info("AsyncPostgresManager: Pool initialized successfully.")
        except Exception as e:
            logging.error(f"AsyncPostgresManager: Failed to initialize pool: {e}")
            self._pool = None

    async def close_pool(self):
        """Closes the asyncpg connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logging.info("AsyncPostgresManager: Pool closed.")

    def _convert_query_params(self, query: str) -> str:
        """Converts psycopg2 %s placeholders to asyncpg $1, $2 placeholders, safely ignoring quotes."""
        result = []
        i = 0
        param_idx = 1
        in_quotes = False
        
        while i < len(query):
            char = query[i]
            
            if char == "'":
                in_quotes = not in_quotes
                result.append(char)
                i += 1
                continue
                
            if not in_quotes and char == '%' and i + 1 < len(query) and query[i+1] == 's':
                result.append(f"${param_idx}")
                param_idx += 1
                i += 2
                continue
                
            result.append(char)
            i += 1
            
        return "".join(result)

    async def execute_query(self, query: str, params: Optional[tuple] = None, fetch: str = "all") -> Any:
        """
        Execute an asynchronous query.
        fetch: "all" (returns list of dicts), "one" (returns dict), 
               "none" (returns None), "rowcount" (returns int)
        """
        if self._pool is None:
            await self.init_pool()
            
        if self._pool is None:
            logging.error("AsyncPostgresManager: No pool available for query.")
            if fetch == "all": return []
            return None
            
        # Convert %s to $1, $2 etc.
        converted_query = self._convert_query_params(query)
        params = params or ()
        
        try:
            async with self._pool.acquire() as conn:
                # asyncpg methods:
                # fetch() -> list of records
                # fetchrow() -> single record
                # execute() -> status string (e.g. "INSERT 0 1")
                
                if fetch == "all":
                    records = await conn.fetch(converted_query, *params)
                    return [dict(r) for r in records] if records else []
                    
                elif fetch == "one":
                    record = await conn.fetchrow(converted_query, *params)
                    return dict(record) if record else None
                    
                elif fetch == "rowcount":
                    status = await conn.execute(converted_query, *params)
                    # status is usually something like 'UPDATE 1' or 'INSERT 0 1'
                    try:
                        return int(status.split()[-1])
                    except (ValueError, IndexError):
                        return 1 # Fallback if we can't parse it
                        
                elif fetch == "none":
                    await conn.execute(converted_query, *params)
                    return None
                    
                else:
                    raise ValueError(f"Invalid fetch mode: {fetch}")
                    
        except Exception as e:
            logging.exception(f"AsyncPostgresManager: Query failed: {e}\\nQuery: {query}\\nParams: {params}")
            if fetch == "all": return []
            return None
