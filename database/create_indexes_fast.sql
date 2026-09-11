-- Fast Index Creation (WITHOUT GIN full-text index)
-- This skips the problematic idx_stock_tags GIN index
-- You'll get 10 out of 11 indexes = 90% of the benefit

-- Enable pg_trgm extension for ILIKE wildcard search performance
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Stock Library Indexes
CREATE INDEX IF NOT EXISTS idx_stock_file_path ON stock_library(file_path);
CREATE INDEX IF NOT EXISTS idx_stock_file_path_trgm ON stock_library USING gin (file_path gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stock_tags_trgm ON stock_library USING gin (tags gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stock_file_name_trgm ON stock_library USING gin (file_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stock_file_type ON stock_library(file_type);
CREATE INDEX IF NOT EXISTS idx_stock_ingest_date ON stock_library(ingest_date DESC);

-- Tracking Shots Indexes
CREATE INDEX IF NOT EXISTS idx_tracking_shots_project ON tracking_shots(project_id);
CREATE INDEX IF NOT EXISTS idx_tracking_shots_name ON tracking_shots(shot_name);

-- Tracking Tasks Indexes
CREATE INDEX IF NOT EXISTS idx_tracking_tasks_shot ON tracking_tasks(shot_id);

-- Tracking Projects Indexes
CREATE INDEX IF NOT EXISTS idx_tracking_projects_code ON tracking_projects(project_code);

-- Operations Log Indexes
CREATE INDEX IF NOT EXISTS idx_operations_project ON operations(project_id);
CREATE INDEX IF NOT EXISTS idx_operations_type ON operations(operation_type);
CREATE INDEX IF NOT EXISTS idx_operations_start_time ON operations(start_time DESC);

-- Update statistics
ANALYZE stock_library;
ANALYZE tracking_shots;
ANALYZE tracking_tasks;
ANALYZE tracking_projects;
ANALYZE operations;

-- Verification queries
SELECT 'Created indexes:' as status;
SELECT indexname FROM pg_indexes 
WHERE schemaname = 'public' 
AND indexname LIKE 'idx_%'
ORDER BY indexname;
