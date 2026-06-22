ALTER TABLE jobs ADD COLUMN created_order INTEGER NOT NULL DEFAULT 0;

UPDATE jobs
SET created_order = (
    SELECT COUNT(*)
    FROM jobs AS earlier
    WHERE earlier.created_at < jobs.created_at
       OR (earlier.created_at = jobs.created_at AND earlier.id <= jobs.id)
);

DROP INDEX IF EXISTS idx_jobs_claim;
CREATE INDEX idx_jobs_claim ON jobs(status, priority, created_order);
