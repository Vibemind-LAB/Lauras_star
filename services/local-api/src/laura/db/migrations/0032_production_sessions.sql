-- production_sessions table for tracking agentic short-creator sessions (Task 4+).
CREATE TABLE production_sessions (
    session_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    created_utc TEXT NOT NULL
);
CREATE INDEX idx_production_sessions_asset ON production_sessions(asset_id);
