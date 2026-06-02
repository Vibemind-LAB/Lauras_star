-- Portion 15.3: per-clip speed / retiming.
-- Effective speed = speed_num/speed_den (1/1 = normal). Stored as a rational so the
-- retime stays exact; the sequence duration is the integer projection (ADR-0005).
-- Portable: SQLite and PostgreSQL both allow ADD COLUMN with a NOT NULL constant default.
ALTER TABLE timeline_clips ADD COLUMN speed_num INTEGER NOT NULL DEFAULT 1;
ALTER TABLE timeline_clips ADD COLUMN speed_den INTEGER NOT NULL DEFAULT 1;
