# Local HTTP API

Worker Worlds exposes a versioned local control plane at `/api/v1`. It reuses
the framework-neutral runner and canonical contracts; domain rules remain in the
world and grading remains in the grader.

```bash
pip install -e '.[dev,api]'
docker compose up -d --wait postgres
worker-worlds migrate
worker-worlds-api
```

Defaults bind only to `127.0.0.1:8000`. Relevant configuration:

- `WORKER_WORLDS_API_HOST` and `WORKER_WORLDS_API_PORT`
- `WORKER_WORLDS_DATABASE_URL`
- `WORKER_WORLDS_ARTIFACT_DIR` (default `.worker-worlds/api`)
- `WORKER_WORLDS_SCENARIO_DIR`
- `WORKER_WORLDS_DASHBOARD_ORIGINS`
- `WORKER_WORLDS_ALLOW_NON_LOOPBACK_API=1` (explicit trusted-network override)

Endpoints:

- `GET /api/v1/health`
- `GET /api/v1/overview`
- `GET /api/v1/scenarios`
- `GET /api/v1/runs`
- `GET /api/v1/runs/{run_id}`
- `POST /api/v1/runs`
- `GET /api/v1/agents`
- `GET /api/v1/agents/{agent_id}`
- `GET /api/v1/catalog`, `/domains`, and `/capabilities`
- `GET /api/v1/domains/{domain_id}/roles`
- `GET /api/v1/roles/{role_id}/suites`
- `GET /api/v1/suites/{suite_id}`
- `POST /api/v1/suite-jobs`
- `GET /api/v1/suite-jobs` and `GET /api/v1/suite-jobs/{job_id}`
- `DELETE /api/v1/suite-jobs/{job_id}`
- `GET /api/v1/suite-jobs/{job_id}/evidence`
- `GET /api/v1/comparisons`
- `POST /api/v1/comparisons/contextual`
- `GET /docs` for generated OpenAPI documentation

`POST /api/v1/runs` accepts a scenario ID resolved only from configured scenario
roots, a supported worker adapter, and `stub`, `postgres`, `supply-chain`, or `insurance` world
selection. The
optional `agent_id` selects the same credential-free registry used by the CLI
and dashboard. Unknown agents return 404; registered but unavailable agents
return 409 with requirement names only. Environment values are never returned.
The response is the canonical `RunRecord`; it is also persisted under the configured
artifact directory.

Non-stub registered agents may execute only scenarios marked `live_ready` in the reviewed/generated
libraries. Demonstration fixtures return the typed `ScenarioNotLiveReady` error rather than making
a provider call.

`POST /api/v1/suite-jobs` validates a domain, role, immutable suite revision, ready registered
agent, world, and bounded concurrency before persisting work. Status and per-scenario progress live
in PostgreSQL. The evidence download is a deterministic ZIP containing the manifest, `SuiteRecord`,
every recorded `RunRecord`, hashes, JUnit, and offline HTML. Cancellation is durable and produces a
terminal job plus partial suite evidence.

Contextual comparison accepts two completed suite-job IDs. Domain, role, suite revision, scenario
hashes, world version, seeds, and budgets must match; incompatibility and incomplete evidence cannot
pass the gate. The frozen standalone CLI comparison remains available for legacy suite artifacts.

The checked HTTP contract is `schemas/v1/openapi.json`; `make openapi-check` detects drift.

This API has no user authentication and is for a trusted local environment. Do not bind it to a
public interface. Organizations, user authorization, rate limits, and billing remain hosted-SaaS
work; suite-job durability is local PostgreSQL-backed execution infrastructure.
