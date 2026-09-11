import logging
from typing import List, Dict

from ...core.infra.async_postgres_manager import AsyncPostgresManager
from .vector_service import vector_service

logger = logging.getLogger(__name__)

class VectorSearch:
    """Handles semantic database queries via pgvector."""
    
    def __init__(self):
        self.db = AsyncPostgresManager()
        
    async def search_similar_shots(self, query: str, limit: int = 20) -> List[Dict]:
        """Returns the top matching tracking_shots records for the given natural language query."""
        if not query or len(query.strip()) < 3:
            return []
            
        vec = vector_service.generate_embedding(query)
        if not vec:
            return []
            
        vec_str = "[" + ",".join(map(str, vec)) + "]"
        
        sql = """
        SELECT project_code, shot_name, version,
               semantic_embedding <=> %s AS distance
        FROM tracking_shots
        WHERE semantic_embedding IS NOT NULL
        ORDER BY distance ASC
        LIMIT %s
        """
        
        try:
            results = await self.db.execute_query(sql, (vec_str, limit), fetch="all")
            return results or []
        except Exception as e:
            logger.error(f"VectorSearch: Query failed: {e}")
            return []

vector_search = VectorSearch()
