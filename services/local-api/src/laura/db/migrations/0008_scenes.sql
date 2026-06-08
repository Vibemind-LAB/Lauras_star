-- Scenes: a lightweight, end-exclusive marker layer over a rough_cut timeline.
-- Boundaries always fall on clip junctions; scenes tile the timeline contiguously.
CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_timeline_id TEXT NOT NULL,
  name TEXT NOT NULL,
  order_index INTEGER NOT NULL,
  seq_in_frame INTEGER NOT NULL,
  seq_out_frame_exclusive INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_scenes_timeline ON scenes (source_timeline_id, order_index);
