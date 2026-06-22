-- 'base' = normal clip; 'replace' = opaque overlay that overrides the base lane over its range.
ALTER TABLE timeline_clips ADD COLUMN role TEXT NOT NULL DEFAULT 'base';
