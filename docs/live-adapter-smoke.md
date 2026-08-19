# Optional live-adapter smoke tests

These tests are disabled by default and use only synthetic text. They never
receive database credentials, world tools, customer data, or production data.
Do not run them without explicit authorization for a paid provider call.

```bash
export WORKER_WORLDS_LIVE_SMOKE=1
export OPENAI_API_KEY='<scoped-test-key>'
export WORKER_WORLDS_LIVE_MODEL=gpt-5-mini
export WORKER_WORLDS_LIVE_MAX_TOKENS=32
export WORKER_WORLDS_LIVE_MAX_COST_MINOR=5
pytest -m live tests/live -q
```

The package makes at most two 30-second requests, prints no response content or
credential, captures adapter/package/provider/model metadata, closes the client,
and has no persistent world state. Without the enable flag, both tests report a
clear skip. Cost is bounded operationally by the token ceiling and the required
test-project credential; the local harness cannot independently enforce provider
billing after a request is accepted.
