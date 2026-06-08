-- Stage 5: a sequence (kind="sequence" timeline) is an ordered list of scene references.
CREATE TABLE sequence_items (
  id TEXT PRIMARY KEY,
  sequence_timeline_id TEXT NOT NULL,
  scene_id TEXT NOT NULL,
  order_index INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_sequence_items ON sequence_items (sequence_timeline_id, order_index);
