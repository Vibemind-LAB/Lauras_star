-- Per-job progress sample (latest), written throttled by the fetch handler.
ALTER TABLE jobs ADD COLUMN progress_json TEXT;
