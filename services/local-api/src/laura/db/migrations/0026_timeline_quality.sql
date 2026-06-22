-- Persisted rough-cut quality per timeline (one row per timeline, upsert on recompute).
-- status: 'computed' when scores are available, 'no_video' when the build had no readable
-- video, 'error' when persistence itself failed (sentinel stored by the catch path).
-- ON DELETE CASCADE mirrors the transition_reviews idiom so rows vanish with the timeline.
CREATE TABLE timeline_quality (
    timeline_id TEXT PRIMARY KEY REFERENCES timelines(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('computed', 'no_video', 'error')),
    overall REAL,
    visual_exactness REAL,
    editorial_cleanliness REAL,
    n_cuts INTEGER,
    n_split_cuts INTEGER,
    created_at TEXT NOT NULL
);
