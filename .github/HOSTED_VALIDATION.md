# Hosted-runner verification checklist

Status: **PR gate and nightly have both run green on hosted runners.** Release validation
last ran green via manual `workflow_dispatch` before the most recent merge to `main` and has
not been re-run since; re-run it before cutting a release. The live-smoke workflow below is
new and has not yet been exercised on a hosted runner because it requires a maintainer to
configure the `OPENAI_API_KEY` repository secret and manually dispatch it.

## Pull request

- `worker-worlds` starts Postgres 17.6, installs Python 3.12 dependencies, and
  runs formatting, Ruff, mypy, schema/scenario drift, all tests, docs, and build.
- The composite action runs the deterministic fast profile and uploads JSON,
  JUnit, and offline HTML from `.worker-worlds/ci`.
- Expected permissions: `contents: read`; no repository or token write access.
- Acceptance: green checks, parseable JUnit, artifacts present, no secret values
  in logs, passing comparison exit 0, seeded critical regression nonzero.

## Nightly

- Run all local gates, the 200-scenario Postgres profile with repetitions, mutant
  and fault suites, adapters, security tests, and behavioral comparison.
- Retain raw JSON, JUnit, HTML, benchmark, and cleanup audit.
- Acceptance: no infrastructure errors, zero critical regressions, zero active
  leases/run schemas, artifacts downloadable and offline-readable.

## Live adapter smoke

- `worker-worlds-live-smoke.yml` runs only on manual `workflow_dispatch`; it never triggers
  on pushes, pull requests, or a schedule.
- Requires the `OPENAI_API_KEY` repository secret to be configured. GitHub redacts secret
  values from all logs automatically; the job never echoes it.
- Runs `pytest -m live tests/live` with `WORKER_WORLDS_LIVE_SMOKE=1` and the same bounded
  token/cost/retry ceilings documented in `docs/live-adapter-smoke.md`.
- Bounded by a 10-minute job timeout in addition to the test's own internal 30-second
  per-provider-call timeout.
- If the secret is missing, the guarded test fails loudly (`OPENAI_API_KEY is required only
  after live smoke is explicitly enabled`) rather than silently passing or skipping --
  dispatching this workflow is itself an explicit, deliberate action.
- Acceptance: both the `openai-agents` and `langgraph` parametrized cases report
  `runtime_exercised=true` with a real `provider_response_ids` value and measured cost at or
  under `WORKER_WORLDS_LIVE_MAX_COST_MINOR`.

## Release validation

- Run clean-install matrix on Ubuntu and macOS, docs build, wheel/sdist build,
  reproducibility, resource audit, SBOM/checksums/provenance, and TestPyPI-ready
  metadata checks.
- Acceptance: identical distribution hashes for fixed source epoch; package
  resources and entry points work from installed artifacts.
- Publication is deliberately absent. TestPyPI, PyPI, GitHub release, hosted docs,
  and signed tags require separate human authorization.

Local `actionlint` 1.7.7 validates workflow files. It does not parse composite
`action.yml` as a workflow; the composite schema and references are covered by
the repository contract test and local execution of its shell commands.
