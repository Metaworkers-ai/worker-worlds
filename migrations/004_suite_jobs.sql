CREATE TABLE IF NOT EXISTS worker_worlds.suite_jobs (
    id text PRIMARY KEY,
    request_key text NOT NULL UNIQUE,
    state text NOT NULL CHECK (state IN ('queued','running','cancelling','cancelled','completed','failed')),
    catalog_version text NOT NULL,
    domain_id text NOT NULL,
    role_id text NOT NULL,
    suite_id text NOT NULL,
    suite_revision text NOT NULL,
    agent_id text NOT NULL,
    world text NOT NULL,
    configuration jsonb NOT NULL,
    total_scenarios integer NOT NULL CHECK (total_scenarios >= 0),
    completed_scenarios integer NOT NULL DEFAULT 0 CHECK (completed_scenarios >= 0),
    passed_scenarios integer NOT NULL DEFAULT 0 CHECK (passed_scenarios >= 0),
    failed_scenarios integer NOT NULL DEFAULT 0 CHECK (failed_scenarios >= 0),
    cancel_requested boolean NOT NULL DEFAULT false,
    revision integer NOT NULL DEFAULT 0,
    error_type text,
    error_message text,
    suite_record_path text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    started_at timestamptz,
    ended_at timestamptz
);

CREATE TABLE IF NOT EXISTS worker_worlds.suite_job_scenarios (
    suite_job_id text NOT NULL REFERENCES worker_worlds.suite_jobs(id) ON DELETE CASCADE,
    scenario_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    state text NOT NULL CHECK (state IN ('pending','running','passed','failed','error','cancelled')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    run_id text,
    run_record_hash text,
    terminal_reason text,
    error_type text,
    error_message text,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (suite_job_id, scenario_id),
    UNIQUE (suite_job_id, ordinal)
);

CREATE INDEX IF NOT EXISTS suite_jobs_state_idx
ON worker_worlds.suite_jobs(state, updated_at);
