# ADR 003: Postgres run isolation and migrations

Status: accepted, 2026-08-18.

One database hosts immutable migration metadata and a lease registry in the
`worker_worlds` control schema. Each run receives a separately named schema
`ww_run_<lowercase-ulid>`. The schema name is locally derived and validated
against that exact pattern before creation or cleanup. Commerce tables and the
event log live inside the run schema. Numbered, checksummed SQL migrations are
the intentionally small migration system for Week 1. Mutation rows and their
events share one SQL transaction.
