-- PostgreSQL Performance Optimization Script
-- Run this on your PostgreSQL server (172.16.1.45) to add indexes
-- Creates indexes on frequently queried columns for better performance

-- ============================================================================
-- STOCK LIBRARY INDEXES
-- ============================================================================

-- Enable pg_trgm extension for ILIKE wildcard search performance
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Index for file path lookups (exact matches and partial matches)
CREATE INDEX IF NOT EXISTS idx_stock_file_path ON stock_library(file_path);
CREATE INDEX IF NOT EXISTS idx_stock_file_path_trgm ON stock_library USING gin (file_path gin_trgm_ops);

-- Index for tags search (Trigram wildcard matching for ILIKE)
CREATE INDEX IF NOT EXISTS idx_stock_tags_trgm ON stock_library USING gin (tags gin_trgm_ops);

-- Index for file name partial matches
CREATE INDEX IF NOT EXISTS idx_stock_file_name_trgm ON stock_library USING gin (file_name gin_trgm_ops);

-- Index for file type filtering
CREATE INDEX IF NOT EXISTS idx_stock_file_type ON stock_library(file_type);

-- Index for sorting by ingest date
CREATE INDEX IF NOT EXISTS idx_stock_ingest_date ON stock_library(ingest_date DESC);

-- ============================================================================
-- TRACKING SYSTEM INDEXES
-- ============================================================================

-- Index for project code lookups
CREATE INDEX IF NOT EXISTS idx_tracking_shots_project ON tracking_shots(project_code);

-- Index for shot name lookups within a project
CREATE INDEX IF NOT EXISTS idx_tracking_shots_name ON tracking_shots(project_code, shot_name);

-- Index for task queries by shot
CREATE INDEX IF NOT EXISTS idx_tracking_tasks_shot ON tracking_tasks(shot_id);

-- Index for project lookups
CREATE INDEX IF NOT EXISTS idx_tracking_projects_code ON tracking_projects(code);

-- ============================================================================
-- OPERATIONS & AUDIT INDEXES
-- ============================================================================

-- Index for finding operations by project
CREATE INDEX IF NOT EXISTS idx_operations_project ON operations(project_id);

-- Index for finding operations by type
CREATE INDEX IF NOT EXISTS idx_operations_type ON operations(operation_type);

-- Index for time-based queries
CREATE INDEX IF NOT EXISTS idx_operations_start_time ON operations(start_time DESC);

-- ============================================================================
-- VERIFY INDEXES
-- ============================================================================

-- List all indexes on stock_library table
SELECT 
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'stock_library'
ORDER BY indexname;

-- List all indexes on tracking_shots table
SELECT 
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'tracking_shots'
ORDER BY indexname;

-- ============================================================================
-- MAINTENANCE RECOMMENDATIONS
-- ============================================================================

-- Analyze tables to update statistics
ANALYZE stock_library;
ANALYZE tracking_shots;
ANALYZE tracking_tasks;
ANALYZE tracking_projects;
ANALYZE operations;

-- Show auto-vacuum status
SHOW autovacuum;

-- If autovacuum is off, recommend enabling it in postgresql.conf:
-- autovacuum = on
-- autovacuum_naptime = 1min

-- ============================================================================
-- PERFORMANCE MONITORING QUERIES
-- ============================================================================

-- Find slow queries
-- SELECT pid, now() - pg_stat_activity.query_start AS duration, query
-- FROM pg_stat_activity
-- WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '5 seconds';

-- Check table sizes
-- SELECT 
--     schemaname,
--     tablename,
--     pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
-- FROM pg_tables
-- WHERE schemaname = 'public'
-- ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
