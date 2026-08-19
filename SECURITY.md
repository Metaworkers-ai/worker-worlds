# Security policy

Report suspected vulnerabilities privately to `security@metaworkers.ai`. Do not
include live credentials or customer data. We acknowledge reports within five
business days and coordinate disclosure after a fix is available.

Security fixes are provided for the latest 1.x release candidate/release. World
state and Postgres schemas are isolated per run. Worker processes, host network,
and host secrets are **not sandboxed** by the core package; run untrusted workers
inside an independently secured container or VM with deny-by-default egress.

See [the threat model](docs/security/threat-model.md) for trust boundaries and
known limitations.
