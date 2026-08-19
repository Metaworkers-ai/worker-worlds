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
