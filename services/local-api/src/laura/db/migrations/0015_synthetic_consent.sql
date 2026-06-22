-- Synthetic / AI-effect flags on assets, and a consent-records table for subject tracking.
-- Additive: existing assets default to synthetic=0, ai_effect NULL.
ALTER TABLE media_assets ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media_assets ADD COLUMN ai_effect TEXT;

CREATE TABLE consent_records (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL,
  subject_label TEXT NOT NULL,
  source_asset_id TEXT,
  confirmed_by  TEXT,
  confirmed_at  TEXT NOT NULL,
  note          TEXT
);
