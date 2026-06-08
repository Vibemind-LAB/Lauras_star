-- Rename the old interchange-export table so that "exports" is free
-- for the render-pipeline export records added in this migration.
ALTER TABLE exports RENAME TO interchange_exports;
DROP INDEX IF EXISTS idx_exports_timeline;
CREATE INDEX idx_interchange_exports_timeline ON interchange_exports(timeline_id);

-- Render-pipeline exports (MP4 / ProRes output files)
CREATE TABLE exports (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    timeline_id   TEXT,
    format        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'rendering',
    path          TEXT,
    size_bytes    INTEGER,
    error         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_exports_project ON exports(project_id);
