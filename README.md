# Worker Worlds

See what your AI workers would do—before they do it—in deterministic, stateful enterprise simulations.

Worker Worlds runs a worker against isolated Retail & E-commerce or Insurance worlds, records every tool call and mutation, grades final state and event history, and compares behavior between worker versions.

## What the worker said vs. what the world shows

```text
Worker: "I issued the requested $25 refund."

World evidence:
  tool call       issue_refund(amount_minor=2500, currency="USD")
  authorization   allowed for customer and order
  state change    captured_minor: 10000 -> refundable_minor: 7500
  event           refund.issued, sequence=1, committed atomically
  verdict         PASS — required state and event evidence are complete
```

A convincing response is not enough: incomplete evidence never passes.

## Architecture

```text
Scenario + policy
       |
       v
Framework-neutral runner <---- Worker adapter (stub / LangGraph / Agents SDK)
       |
       v
Tool gateway ---- authorization + validation + idempotency
       |
       v
Isolated enterprise world --- atomic state + append-only event log
       |
       +---- snapshots / hashes ---- grader ---- RunRecord
                                             \---- JSON / HTML / comparison
```

## Five-minute quickstart

Python 3.12 and Docker are required.

```bash
make setup
docker compose up -d --wait postgres
cp .env.example .env
set -a; source .env; set +a
make verify
.venv/bin/worker-worlds migrate
.venv/bin/worker-worlds doctor
.venv/bin/worker-worlds run examples/scenarios/refund_happy.yaml \
  --worker stub --world postgres --output .worker-worlds/runs/
```

The safe local development URL is `postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_dev`. Override it only with `WORKER_WORLDS_DATABASE_URL` or `--database-url`. Test code requires an explicit `WORKER_WORLDS_TEST_DATABASE_URL`.

## A scenario is readable YAML

```yaml
schema_version: "1.0"
id: example.refund.partial
world:
  schema_version: "1.0"
  name: postgres-commerce
  version: "1.0"
  seed: 42
trigger:
  schema_version: "1.0"
  type: customer_request
  actor:
    customer_id: cus_102
  content: >-
    Call `issue_refund` for order `ord_900` with amount_minor 2500,
    currency USD, and idempotency_key example-refund-1.
assertions:
  - schema_version: "1.0"
    id: example.refund.partial.event
    type: action_exists
    severity: critical
    event: refund.issued
  - schema_version: "1.0"
    id: example.refund.partial.tool-result
    type: tool_result_matches
    severity: critical
    parameters:
      tool_name: issue_refund
      arguments:
        order_id: ord_900
        amount_minor: 2500
        currency: USD
        idempotency_key: example-refund-1
      result_status: success
      count: 1
```

The checked-in library contains 200 generated commerce scenarios in
[`scenarios/release`](scenarios/release) and 24 supply-chain/insurance scenarios in
[`scenarios/enterprise`](scenarios/enterprise). All 224 live-ready scenarios require matching tool
result evidence; the ten fixtures under `examples/scenarios` are stub-only demonstrations. Export
and validate the generated commerce library with:

```bash
worker-worlds scenario export scenarios/release --overwrite
worker-worlds scenario validate scenarios/release
worker-worlds scenario coverage scenarios/release --output artifacts/scenario-coverage.json --overwrite
```

## Evidence and verdicts

Failures retain the causal trail instead of collapsing into a boolean:

```json
{
  "status": "fail",
  "terminal_reason": "authorization_rejection",
  "verdict": {"outcome": "fail", "complete_evidence": true},
  "failed_assertions": ["refund.issued event was absent"],
  "mutations": 0
}
```

![Worker Worlds suite summary showing 200 passing scenario runs](docs/assets/suite-summary.png)

![Critical failure report showing assertion and execution evidence](docs/assets/critical-failure.png)

![World-event evidence showing authorization and before-and-after state](docs/assets/world-event-evidence.png)

## Behavioral comparisons

Create an immutable baseline, then compare a candidate. New critical violations fail with a nonzero exit code.

```bash
worker-worlds baseline create --from artifacts/reference/suite.json \
  --name rc1 --output .worker-worlds/baselines
worker-worlds compare --baseline .worker-worlds/baselines/rc1.json \
  --candidate artifacts/candidate/suite.json --config worker-worlds.yaml \
  --output .worker-worlds/comparison
```

![Behavioral comparison showing a candidate regression and failed release gate](docs/assets/behavioral-comparison.png)

## Adapters and boundaries

The core runner is framework-neutral. Supported adapters are the deterministic stub, LangGraph, and OpenAI Agents SDK. Fake adapter examples need no network or paid API; real SDK integrations are optional extras:

```bash
pip install 'worker-worlds[langgraph]'
pip install 'worker-worlds[openai-agents]'
```

Worker Worlds implements world-state and per-run database isolation. It does **not** sandbox arbitrary worker processes, isolate the host, or enforce network egress. Those are deployment responsibilities. Use scoped test credentials and see the [secure worker deployment guide](docs/security/secure-worker-deployment.md) before running untrusted workers.

## Performance disclosure

Local deterministic evaluation is designed for parallel execution, but results depend on hardware and Postgres configuration. The release benchmark records machine metadata, concurrency, suite size, total duration, and latency distribution; it is not a hosted-service throughput claim. Small stochastic samples are not statistical proof, and simulated commerce behavior can diverge from production systems.

## Development and documentation

```bash
make setup       # install development dependencies
make verify      # format, lint, strict typecheck, tests, schemas, scenarios, docs, build
make schemas-check
make openapi-check
make catalog-check
make scenarios-check
```

- [Quickstart](docs/quickstart.md), [concepts](docs/concepts.md), and [scenario authoring](docs/authoring.md)
- [Engineering handover](HANDOVER.md) for current branch state, module ownership, startup, and next work
- [Operations](docs/operations.md), [CLI reference](docs/reference.md), and [release process](docs/release.md)
- [Domain and role catalog](docs/catalog.md) and [local API](docs/api.md)
- [Threat model](docs/security/threat-model.md), [secure deployment](docs/security/secure-worker-deployment.md), and [live-adapter smoke tests](docs/live-adapter-smoke.md)
- [Domain-review package](docs/domain-review/index.html) and [release checklist](docs/release-checklist.md)
- [Contributing](CONTRIBUTING.md), [security policy](SECURITY.md), and [changelog](CHANGELOG.md)

Contracts use schema major version 1, ULID identifiers, UTC timestamps, integer monetary minor units with ISO currency, strict unknown-field rejection, and canonical deterministic serialization. Checked-in schemas live in [`schemas/v1`](schemas/v1).

Worker Worlds is released under the [MIT License](LICENSE).

## Local dashboard and API

The Next.js dashboard in [`apps/dashboard`](apps/dashboard) consumes the
versioned Python API in `worker_worlds.api`. Metrics, scenarios, runs, verdicts,
events, readiness, and comparisons come from real Worker Worlds contracts and
persisted artifacts.

```bash
# terminal 1
pip install -e '.[dev,api]'
worker-worlds-api

# terminal 2
cd apps/dashboard && npm ci && npm run dev
# then open http://localhost:3000
```

The API binds to `127.0.0.1:8000` by default and exposes `/api/v1`. See the
[API guide](docs/api.md). Authentication, organizations, billing, and hosted
multi-tenancy are intentionally not implemented yet.
