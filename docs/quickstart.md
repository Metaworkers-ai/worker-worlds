# Quickstart

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
docker compose up -d --wait postgres
cp .env.example .env
set -a
source .env
set +a
.venv/bin/worker-worlds migrate
.venv/bin/worker-worlds doctor
.venv/bin/worker-worlds suite examples/scenarios/refund_happy.yaml --worker stub --world postgres --repetitions 1 --output .worker-worlds/quickstart
open .worker-worlds/quickstart/report.html
```

Create and compare an immutable baseline with `worker-worlds baseline create`
and `worker-worlds compare`; a nonzero comparison exit status means the explicit
gate rejected the candidate. The deterministic fake workers require no paid API.

## Register and run real agents

Install both optional integrations and keep credentials in the ignored `.env` file. Use a scoped
test key and authorize paid calls explicitly:

```bash
.venv/bin/pip install -e '.[openai-agents,langgraph]'
set -a; source .env; set +a
.venv/bin/worker-worlds agents list
.venv/bin/worker-worlds agents doctor openai-project
.venv/bin/worker-worlds run scenarios/release/commerce__refunds-payments__001.yaml \
  --agent openai-project --world postgres --no-interactive
.venv/bin/worker-worlds run scenarios/release/commerce__refunds-payments__001.yaml \
  --agent langgraph-project --world postgres --no-interactive
```

Do not use scenarios under `examples/scenarios` with live adapters. They are deterministic harness
fixtures retained for stub tests; live adapters are restricted to the 224 reviewed/generated
scenarios under `scenarios/release` and `scenarios/enterprise`.

Factories use explicit `module:callable` paths in `worker-worlds.yaml`. OpenAI
Agents factories return an `OpenAIAgentsAdapter(OpenAIAgentsRuntime(agent))`;
LangGraph factories return a `LangGraphAdapter(LangGraphRuntime(graph_factory))`.
Neither configuration nor readiness responses persist environment values.

The HTTP API exposes the same registry at `GET /api/v1/agents`; open
the Next.js dashboard after starting `worker-worlds-api` for the domain → role → suite → agent
workflow:

```bash
# terminal 1, from the repository root
set -a; source .env; set +a
.venv/bin/worker-worlds-api

# terminal 2
cd apps/dashboard
npm ci
npm run dev
```

The dashboard can run Retail & E-commerce suites (including Supply Chain Analyst), Insurance Claims
Adjuster suites, cancel durable evaluations, download complete evidence, and compare two compatible
completed agent evaluations. Direct scenario execution remains under Advanced custom scenario run.

If doctor reports `not ready`, install the named optional extra, verify the
factory import path, export every listed environment name, and confirm Postgres
with `docker compose up -d --wait postgres` and `worker-worlds migrate`.

For the complete incoming-engineer setup, module map, evidence locations, and current limitations,
see the repository-level [HANDOVER.md](../HANDOVER.md).
