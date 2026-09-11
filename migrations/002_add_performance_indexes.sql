-- Migration 002: Add Performance Indexes for Scalability

-- Allow faster filtering by Tag (User often searches "Explosion")
CREATE INDEX IF NOT EXISTS idx_stock_tags ON stock_library USING gin(to_tsvector('english', tags));

-- Allow faster filtering by Type ("Show me only Images")
CREATE INDEX IF NOT EXISTS idx_stock_file_type ON stock_library(file_type);

-- Allow faster Sorting by Date (Default view is Sort By Date)
CREATE INDEX IF NOT EXISTS idx_stock_ingest_date ON stock_library(ingest_date DESC);

-- Allow faster Project lookups
CREATE INDEX IF NOT EXISTS idx_tracking_shots_project ON tracking_shots(project_code);
