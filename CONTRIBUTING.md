# Contributing

Worker Worlds requires Python 3.12 and a pinned local Postgres container.

```bash
make setup
docker compose up -d --wait postgres
export WORKER_WORLDS_TEST_DATABASE_URL=postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_test
make verify
```

Public contracts require schema drift updates and backward-compatibility tests.
Scenario changes require `make scenarios`, independent domain review, and mutant
coverage. Never use production data, credentials, or endpoints. By contributing,
you agree that your contribution is licensed under MIT.
