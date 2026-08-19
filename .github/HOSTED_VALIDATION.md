# Hosted-runner verification checklist

Status: **PENDING EXTERNAL VALIDATION**.

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
