# Worker Worlds engineering handover

This document is the incoming engineer's operational starting point. The complete implementation is
on branch `agent/saas-dashboard`. It was branched from `main`; implementation commit `315d2ad`
contains the domain/role catalog, enterprise worlds, durable suite execution, dashboard, and hardened
live scenarios. The documentation cleanup containing this file follows that implementation commit.
As of 2026-08-24, this branch has not been merged into `main`.

## Product state

Worker Worlds is a local-first evaluation system for observing AI workers inside deterministic,
stateful enterprise simulations. The implemented verticals are Retail & E-commerce and Insurance.
The execution path is:

```text
Scenario -> WorkerAdapter -> Runner -> authorized World tools
         -> atomic state/event evidence -> deterministic Grader
         -> RunRecord/SuiteRecord -> JSON, JUnit, HTML, and dashboard
```

The repository currently provides:

- frozen schema-major-1 contracts, canonical serialization, ULID identifiers, UTC timestamps, and
  integer monetary minor units;
- deterministic stub, Postgres commerce, supply-chain, and insurance worlds;
- stub, OpenAI Agents SDK, and LangGraph adapters behind the same framework-neutral protocols;
- versioned domain, role, capability, scenario, and suite catalog;
- durable PostgreSQL-backed suite jobs with leases, cancellation, bounded retry, recovery, and
  content-addressed evidence;
- a local FastAPI control plane and Next.js evaluation dashboard;
- 200 reviewed/generated commerce scenarios and 25 enterprise scenarios. These 225 scenarios are
  live-ready and require tool-result evidence. Ten legacy example fixtures remain stub-only;
- deterministic contextual comparisons for compatible completed suites.

## Fresh checkout

Requirements are Python 3.12, Docker with Compose, Node.js/npm, and Git.

```bash
git clone https://github.com/Metaworkers-ai/worker-worlds.git
cd worker-worlds
git checkout agent/saas-dashboard
cp .env.example .env
make setup
docker compose up -d --wait postgres
set -a
source .env
set +a
.venv/bin/worker-worlds migrate
.venv/bin/worker-worlds doctor
```

The Compose service creates only `worker_worlds_dev`. Both database variables in `.env.example`
therefore point explicitly to that safe local database. Tests isolate mutable state by validated
per-run schemas. Do not substitute a production URL.

Run the complete release gate with ports 3000 and 8000 free:

```bash
make verify
```

The handover verification on 2026-08-24 produced 264 passing Python tests, two intentionally skipped
paid-provider tests, ten passing Playwright stories, current schemas/OpenAPI/catalog/scenarios, and
successful Python and Next.js production builds.

## Start the local product

Load `.env` into the API process so registered live adapters can see the provider variable names:

```bash
# terminal 1, repository root
set -a
source .env
set +a
.venv/bin/worker-worlds-api

# terminal 2
cd apps/dashboard
npm run dev
```

Open `http://localhost:3000`. API health is available at
`http://127.0.0.1:8000/api/v1/health`, and generated API documentation is at
`http://127.0.0.1:8000/docs`.

For a provider-free smoke test, select `local-stub`. For OpenAI or LangGraph, install the matching
optional dependencies through `make setup`, put a scoped test key only in `.env`, run
`.venv/bin/worker-worlds agents doctor <agent-id>`, and explicitly authorize the paid evaluation.
Never use the ten `examples/scenarios` demonstration fixtures for a live adapter.

## Module map

| Responsibility | Primary files |
|---|---|
| Contracts and protocols | `src/worker_worlds/contracts.py`, `protocols.py` |
| Execution and grading | `runner.py`, `grading.py`, `policies.py` |
| Commerce world | `postgres_world.py`, `domain.py` |
| Supply chain and insurance | `supply_chain.py`, `insurance.py`, `json_world.py` |
| Agent integration | `agent_registry.py`, `adapters.py`, `native_bridge.py`, `example_factories.py` |
| Catalog and evaluation context | `catalog.py`, `evaluation.py`, `scenario_identity.py` |
| Durable suites | `suite_jobs.py`, `suite_service.py`, migrations `004`-`006` |
| Evidence and comparison | `reporting.py`, `suite.py`, `comparison.py`, `comparison_context.py` |
| HTTP API | `api.py`, `api_models.py`, checked `schemas/v1/openapi.json` |
| Dashboard | `apps/dashboard/src`, browser tests in `apps/dashboard/tests` |
| Scenario sources | `scenarios/release`, `scenarios/enterprise`, generators in `scenario_library.py` and `enterprise_scenarios.py` |

Database migrations are forward-only and checksummed. Root migration files and their packaged copies
under `src/worker_worlds/migrations` must remain byte-identical.

## Evidence and local state

- API artifacts: `.worker-worlds/api`
- CLI artifacts: the path passed to `--output`
- Durable suite bundle:
  `.worker-worlds/api/suite-jobs/<job-id>/publications/<executor-id>/evidence.zip`
- Checked public schemas: `schemas/v1`
- Checked catalog: `catalog/v1/catalog.json`
- Local Postgres: `127.0.0.1:55432/worker_worlds_dev`

Runtime artifacts, `.env`, build output, virtual environments, and Next.js output are ignored by Git.

## Change discipline

Before committing:

```bash
make format
make verify
git diff --check
```

- Contract changes require schema generation plus compatibility tests.
- API changes require `make openapi` and checked OpenAPI review.
- Catalog changes require `make catalog`; suite membership changes require a suite revision.
- Release-scenario generator changes require `make scenarios` and review of all generated YAML.
- Migration changes must be new numbered migrations; never edit an applied migration.
- Never run paid provider tests unless the operator explicitly authorizes them.

## Known limitations and next work

- The API is an unauthenticated trusted-loopback control plane. Authentication, organizations,
  billing, rate limits, and hosted multi-tenancy are not implemented.
- Worker code is not a process, host, or network sandbox. Use the secure deployment guide for
  untrusted workers.
- Independent business-domain approval remains external work, particularly before using simulated
  outcomes as a production policy claim.
- A pre-hardening, explicitly authorized OpenAI insurance run passed 6 of 12 scenarios and exposed
  prompts that allowed clarification/inaction. All 224 live prompts and assertions were hardened
  afterward; a post-hardening paid replay has not been authorized or run.
- Hosted CI should be confirmed on this branch, then the branch should be reviewed and merged into
  `main`. TestPyPI/PyPI publication, signed tags, and a hosted deployment remain external steps.

Start deeper reading with `README.md`, `docs/concepts.md`, `docs/catalog.md`, `docs/operations.md`,
`docs/security/threat-model.md`, and the six ADRs under `docs/adr`.
