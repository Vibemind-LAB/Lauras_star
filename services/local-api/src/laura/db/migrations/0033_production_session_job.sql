-- A production session records the job currently running it, so GET /production/{sid} can
-- report liveness. Live incident (2026-07-18): a run died in its first seconds and the status
-- endpoint kept returning a serene board for 55 minutes because it never looked at the job that
-- knew. Nullable: a session exists briefly before its job is enqueued, and old rows have none.
ALTER TABLE production_sessions ADD COLUMN latest_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL;
