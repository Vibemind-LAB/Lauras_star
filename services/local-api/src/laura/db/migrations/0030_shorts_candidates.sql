-- Shorts candidate windows with score + QA results for the auto-shorts-cutter.
-- Each row represents one transcript-safe candidate extracted from a source timeline.
CREATE TABLE shorts_candidates (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  source_timeline_id TEXT NOT NULL,
  order_index INTEGER NOT NULL,
  start_frame INTEGER NOT NULL,
  end_frame_exclusive INTEGER NOT NULL,
  start_boundary TEXT NOT NULL,
  end_boundary TEXT NOT NULL,
  score REAL NOT NULL,
  rejected INTEGER NOT NULL DEFAULT 0,
  reject_reason TEXT,
  score_breakdown TEXT,
  qa_passed INTEGER NOT NULL DEFAULT 1,
  qa_issues TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_shorts_candidates_timeline ON shorts_candidates (source_timeline_id, order_index);
CREATE INDEX idx_shorts_candidates_asset ON shorts_candidates (asset_id);
