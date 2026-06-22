-- Persistent transcript re-alignment state per segment.
-- aligned  = words/timing are in sync with the current text
-- stale    = text changed, timing still reflects an older alignment
-- aligning = a transcript.realign job has been queued/running
-- failed   = the last re-alignment attempt failed; text remains saved
ALTER TABLE transcript_segments ADD COLUMN alignment_status TEXT NOT NULL DEFAULT 'aligned';
ALTER TABLE transcript_segments ADD COLUMN alignment_job_id TEXT;
ALTER TABLE transcript_segments ADD COLUMN alignment_language TEXT;
ALTER TABLE transcript_segments ADD COLUMN alignment_error TEXT;
ALTER TABLE transcript_segments ADD COLUMN alignment_updated_at TEXT;
