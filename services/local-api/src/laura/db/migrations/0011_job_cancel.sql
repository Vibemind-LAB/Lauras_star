-- Cooperative cancel signal for an in-flight import/download job. The fetch handler
-- polls this flag and aborts (used to abort large 30 GB downloads on request).
ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
