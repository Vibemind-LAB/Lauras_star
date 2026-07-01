-- Frame-level visual embeddings for the auto-shorts-cutter visual-embedding layer (VE2).
-- Each row stores a 1-D float32 embedding vector (as BLOB) for one frame of one analysis run.
CREATE TABLE frame_embeddings (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  analysis_run_id TEXT NOT NULL,
  frame INTEGER NOT NULL,
  model TEXT NOT NULL,
  dims INTEGER NOT NULL,
  vector BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_frame_embeddings_asset ON frame_embeddings (asset_id, analysis_run_id, frame);
