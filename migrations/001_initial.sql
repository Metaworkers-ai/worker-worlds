CREATE SCHEMA IF NOT EXISTS worker_worlds;

CREATE TABLE IF NOT EXISTS worker_worlds.schema_migrations (
    version integer PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS worker_worlds.run_leases (
    run_id text PRIMARY KEY,
    namespace text NOT NULL UNIQUE,
    world_version text NOT NULL,
    acquired_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    active boolean NOT NULL DEFAULT true
);
