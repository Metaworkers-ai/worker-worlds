# Optional live-adapter smoke tests

These tests are disabled by default and use only synthetic text. They never
receive database credentials, world tools, customer data, or production data.
Do not run them without explicit authorization for a paid provider call.

```bash
export WORKER_WORLDS_LIVE_SMOKE=1
export OPENAI_API_KEY='<scoped-test-key>'
export WORKER_WORLDS_LIVE_MODEL=gpt-4.1-mini
export WORKER_WORLDS_LIVE_MAX_TOKENS=64
export WORKER_WORLDS_LIVE_MAX_COST_MINOR=5
export WORKER_WORLDS_LIVE_MAX_RETRIES=0
pytest -m live tests/live -q
```

The package makes at most two 30-second requests, permits no retries by default,
prints no response content or credential, captures adapter/package/provider/model
metadata, closes the client, and has no persistent world state. Without the enable
flag, both tests report a clear skip. The smoke reads the provider's directional
input/output token usage and calculates cost using the documented model rates,
then fails if the measured amount exceeds `WORKER_WORLDS_LIVE_MAX_COST_MINOR`.
For a model other than the pinned smoke model, set
`WORKER_WORLDS_LIVE_INPUT_USD_PER_MILLION` and
`WORKER_WORLDS_LIVE_OUTPUT_USD_PER_MILLION` explicitly. This is token-metered
cost enforcement, not reconciliation against the provider's eventual billing
ledger. The default bounded smoke uses non-reasoning `gpt-4.1-mini` at its
documented $0.40/M input-token and $1.60/M output-token rates; reasoning models
may consume the 64-token smoke ceiling before producing a final answer.

The tested optional dependency compatibility set is `openai-agents==0.19.4`,
`langgraph==1.2.10`, `langchain==1.3.14`, and `langchain-openai==1.4.1`. Update these pins only after
the fake-model conformance, cold-install, and guarded live-smoke contracts pass.

Live smoke authorization is separate from ordinary test execution. Default CI,
`make verify`, package builds, and fake-model conformance tests never set
`WORKER_WORLDS_LIVE_SMOKE` and therefore cannot make paid calls.
