-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Add the semantic_embedding column to tracking_shots
-- BAAI/bge-small-en-v1.5 produces 384-dimensional vectors
ALTER TABLE tracking_shots 
ADD COLUMN IF NOT EXISTS semantic_embedding vector(384);

-- 3. Create an HNSW index on the vector column for extremely fast cosine similarity search
-- The <=> operator is used for cosine distance in pgvector
CREATE INDEX IF NOT EXISTS tracking_shots_embedding_hnsw_idx 
ON tracking_shots USING hnsw (semantic_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
