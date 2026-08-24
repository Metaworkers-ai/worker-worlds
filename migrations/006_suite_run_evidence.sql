CREATE TABLE worker_worlds.suite_run_evidence (
    suite_job_id text NOT NULL REFERENCES worker_worlds.suite_jobs(id) ON DELETE CASCADE,
    scenario_id text NOT NULL,
    run_id text NOT NULL,
    record_hash text NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    relative_path text NOT NULL,
    executor_id text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (suite_job_id, run_id),
    FOREIGN KEY (suite_job_id, scenario_id)
        REFERENCES worker_worlds.suite_job_scenarios(suite_job_id, scenario_id)
        ON DELETE CASCADE
);

CREATE INDEX suite_run_evidence_job_created_idx
    ON worker_worlds.suite_run_evidence(suite_job_id, created_at, run_id);
