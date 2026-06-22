-- Cached VLM transition-smoothness verdicts (Plan B). The identity is the SEMANTIC boundary
-- (source-frame pair) + model digest, NOT the sequence position (which drifts on upstream edits),
-- so a re-review after an unrelated edit is a cache hit (idempotency, invariant #7). ON DELETE
-- CASCADE keeps the table from orphaning when a timeline/project is deleted.
CREATE TABLE transition_reviews (
    id TEXT PRIMARY KEY,
    timeline_id TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    asset_a TEXT NOT NULL,
    asset_b TEXT NOT NULL,
    src_out_a INTEGER NOT NULL,
    src_in_b INTEGER NOT NULL,
    boundary_seq_frame INTEGER NOT NULL,
    boundary_signature TEXT NOT NULL,
    smoothness REAL NOT NULL,
    label TEXT NOT NULL,
    reason TEXT NOT NULL,
    suggested_fix_json TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(timeline_id, asset_a, asset_b, src_out_a, src_in_b, model_digest)
);

CREATE INDEX idx_transition_reviews_timeline ON transition_reviews(timeline_id);
