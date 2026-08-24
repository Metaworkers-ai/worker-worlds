# Contributing

Worker Worlds requires Python 3.12 and a pinned local Postgres container.

```bash
make setup
docker compose up -d --wait postgres
export WORKER_WORLDS_TEST_DATABASE_URL=postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_dev
make verify
```

The pinned Compose service creates `worker_worlds_dev`; tests use validated per-run schemas inside
that explicitly selected local database. Stop manually running API/dashboard processes before
`make verify` so the database lifecycle and Playwright web server own their test resources.

Public contracts require schema drift updates and backward-compatibility tests.
Scenario changes require `make scenarios`, independent domain review, and mutant
coverage. Never use production data, credentials, or endpoints. By contributing,
you agree that your contribution is licensed under MIT.
