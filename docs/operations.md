# Operations

Use the pinned Compose service for local Postgres. Migrations are forward-only,
checksummed, and applied by `worker-worlds migrate`. Every run owns a validated
schema and lease; `close()` removes only that namespace. Artifact retention is an
operator decision. Use summary reports in CI and retain per-run JSON for failures.

Troubleshooting begins with `worker-worlds doctor`. Never aim cleanup at an
unspecified database. Reference performance and security claims apply only to
the documented local profile; provider queues and external worker latency are
separate.
