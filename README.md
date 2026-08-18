# Worker Worlds

Worker Worlds lets teams see what AI workers would do before they do it. This
Day 0 foundation freezes framework-neutral contracts and provides an executable,
deterministic commerce stub.

## Development

Python 3.12 is required.

```bash
make setup
make verify
.venv/bin/worker-worlds run examples/scenarios/refund_happy.yaml \
  --worker stub --output .worker-worlds/runs/
```

The JSON output is a canonical `RunRecord` containing normalized turns, calls,
results, world snapshots, events, verdicts, and complete failure provenance.
The stub remains in-memory for contract tests. Week 1 adds the isolated
Postgres commerce world; native framework adapters, HTML reporting, and
behavioral diff execution remain later work.

## Postgres quickstart

```bash
docker compose up -d --wait postgres
.venv/bin/worker-worlds migrate
.venv/bin/worker-worlds doctor
.venv/bin/worker-worlds run examples/scenarios/refund_happy.yaml \
  --worker stub --world postgres --output .worker-worlds/runs/
```

The safe local default is
`postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_dev`.
Override it only with `WORKER_WORLDS_DATABASE_URL` or `--database-url`.
Database names are restricted to `worker_worlds_dev` and
`worker_worlds_test[_suffix]`; per-run schemas are validated before cleanup.

For integration tests, explicitly set a test URL:

```bash
export WORKER_WORLDS_TEST_DATABASE_URL=postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_test
pytest
```

Schema contracts are checked in under `schemas/v1/`. Run `make schemas` after
an intentional additive contract update and `make schemas-check` in CI.

## Repeated suites and reports

Run five isolated repetitions and create canonical JSON, JUnit XML, and a
portable static HTML report:

```bash
.venv/bin/worker-worlds suite examples/scenarios/refund_happy.yaml \
  --worker stub --world postgres --repetitions 5 \
  --output .worker-worlds/week2-report/
open .worker-worlds/week2-report/report.html
```

Network-free native adapter examples are available as `langgraph-fake` and
`openai-agents-fake`. Real integrations are optional installs:

```bash
pip install 'worker-worlds[langgraph]'
pip install 'worker-worlds[openai-agents]'
```

Core imports and deterministic fake adapter tests do not require either SDK or
paid model access.

## Frozen Day 0 decisions

- Python 3.12, strict typing, and async boundaries.
- ULID identifiers; timezone-aware UTC timestamps; controlled world clock.
- Integer minor units and ISO 4217 currency codes; floats are rejected.
- Schema `1.x`; unsupported major versions are rejected during validation.
- Pydantic records forbid unknown fields and canonical JSON sorts object keys.
- World rules remain in worlds; framework translation remains in adapters;
  presentation remains in reporters.
- Incomplete evidence always produces an error verdict and can never pass.
