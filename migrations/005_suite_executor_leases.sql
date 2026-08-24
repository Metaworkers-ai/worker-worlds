ALTER TABLE worker_worlds.suite_jobs
ADD COLUMN IF NOT EXISTS executor_id text,
ADD COLUMN IF NOT EXISTS executor_expires_at timestamptz;

CREATE INDEX IF NOT EXISTS suite_jobs_recovery_idx
ON worker_worlds.suite_jobs(state, executor_expires_at)
WHERE state IN ('queued','running','cancelling');
