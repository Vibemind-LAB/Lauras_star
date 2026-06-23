-- Per-timeline undo/redo checkpoint stacks. payload_json = full editorial snapshot
-- (clips/scenes/audio_clips/transitions). ON DELETE CASCADE mirrors transition_reviews (0024).
CREATE TABLE timeline_history (
    id            TEXT PRIMARY KEY,
    timeline_id   TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    seq_no        INTEGER NOT NULL,
    stack         TEXT NOT NULL,
    label         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    CHECK (stack IN ('undo','redo'))
);
CREATE INDEX idx_timeline_history_lookup ON timeline_history(timeline_id, stack, seq_no);
