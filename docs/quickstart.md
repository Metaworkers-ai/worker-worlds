# Quickstart

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
docker compose up -d --wait postgres
.venv/bin/worker-worlds migrate
.venv/bin/worker-worlds doctor
.venv/bin/worker-worlds suite examples/scenarios/refund_happy.yaml --worker stub --world postgres --repetitions 1 --output .worker-worlds/quickstart
open .worker-worlds/quickstart/report.html
```

Create and compare an immutable baseline with `worker-worlds baseline create`
and `worker-worlds compare`; a nonzero comparison exit status means the explicit
gate rejected the candidate. The deterministic fake workers require no paid API.

## Register and run real agents

Install both optional integrations and keep credentials in the environment:

```bash
.venv/bin/pip install -e '.[openai-agents,langgraph]'
export OPENAI_API_KEY='<scoped-project-key>'
.venv/bin/worker-worlds agents list
.venv/bin/worker-worlds agents doctor openai-project
.venv/bin/worker-worlds scenarios search refund
.venv/bin/worker-worlds run --scenario refund.partial.happy --agent openai-project --world postgres --no-interactive
.venv/bin/worker-worlds run --scenario refund.partial.happy --agent langgraph-project --world postgres --no-interactive
```

Factories use explicit `module:callable` paths in `worker-worlds.yaml`. OpenAI
Agents factories return an `OpenAIAgentsAdapter(OpenAIAgentsRuntime(agent))`;
LangGraph factories return a `LangGraphAdapter(LangGraphRuntime(graph_factory))`.
Neither configuration nor readiness responses persist environment values.

The HTTP API exposes the same registry at `GET /api/v1/agents`; open
`site/dashboard.html` after starting `worker-worlds-api` for the local picker.

If doctor reports `not ready`, install the named optional extra, verify the
factory import path, export every listed environment name, and confirm Postgres
with `docker compose up -d --wait postgres` and `worker-worlds migrate`.
