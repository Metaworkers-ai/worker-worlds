# Operations

Use the pinned Compose service for local Postgres. Migrations are forward-only,
checksummed, and applied by `worker-worlds migrate`. Every run owns a validated
schema and lease; `close()` removes only that namespace. Artifact retention is an
operator decision. Use summary reports in CI and retain per-run JSON for failures.

Role-level suite jobs use global `worker_worlds.suite_jobs` metadata and isolated per-run world
schemas. Progress updates are transactional and monotonic. The local API owns active executor tasks;
job rows and completed evidence survive dashboard refreshes. Cancellation marks pending scenarios
before cancelling active tasks. Evidence bundles live under
`.worker-worlds/api/suite-jobs/<job-id>/publications/<executor-id>/evidence.zip`.

The API is a trusted-loopback control plane, not a public multi-tenant service. Suite executors hold
short PostgreSQL leases and heartbeat while active. A restarted API scans queued jobs and expired
leases, reloads immutable RunRecords already written to disk, resets only interrupted scenario rows,
and resumes the remaining work. Executor IDs fence stale processes from writing after takeover.
Infrastructure failures identified as `InfrastructureError` receive one bounded retry by default;
all attempts remain in the final SuiteRecord. Cancellation is durable and creates terminal partial
evidence, including when the prior executor disappeared.

Troubleshooting begins with `worker-worlds doctor`. Never aim cleanup at an
unspecified database. Reference performance and security claims apply only to
the documented local profile; provider queues and external worker latency are
separate.
