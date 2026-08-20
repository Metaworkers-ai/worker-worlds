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

Endpoints:

- `GET /api/v1/health`
- `GET /api/v1/overview`
- `GET /api/v1/scenarios`
- `GET /api/v1/runs`
- `GET /api/v1/runs/{run_id}`
- `POST /api/v1/runs`
- `GET /api/v1/agents`
- `GET /api/v1/agents/{agent_id}`
- `GET /api/v1/comparisons`
- `GET /docs` for generated OpenAPI documentation

`POST /api/v1/runs` accepts a scenario ID resolved only from configured scenario
roots, a supported worker adapter, and `stub` or `postgres` world selection. The
optional `agent_id` selects the same credential-free registry used by the CLI
and dashboard. Unknown agents return 404; registered but unavailable agents
return 409 with requirement names only. Environment values are never returned.
response is the canonical `RunRecord`; it is also persisted under the configured
artifact directory.

This API has no authentication and is for a trusted local environment. Do not
bind it to a public interface. Authentication, organizations, authorization,
rate limits, durable jobs, and billing are separate hosted-SaaS work.
