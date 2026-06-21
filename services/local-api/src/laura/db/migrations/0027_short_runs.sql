-- Build-graph ledger: one row per attempt to build a short (reel) from an input.
-- short_id is content-addressed (hash computed by P3-T2); supplied as opaque here.
-- trace_json is stored verbatim; populated by P3-T3.
CREATE TABLE short_runs (
    id TEXT PRIMARY KEY,
    short_id TEXT NOT NULL,
    input_sha256 TEXT,
    pipeline_version TEXT NOT NULL,
    recipe_hash TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    trace_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_short_runs_short_id ON short_runs (short_id);
