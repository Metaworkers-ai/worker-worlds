# Secure worker deployment boundary

Worker Worlds implements deterministic world-state isolation and per-run Postgres
schema isolation. It does **not** implement process, host, or network sandboxing.
Provider credential isolation is the deployer's responsibility.

Trusted workers may run in process when their code and dependencies receive the
same trust as the harness. Untrusted or third-party workers should run in a
separate container or VM. The example in `examples/secure-worker/compose.yaml`
demonstrates a network-disabled baseline; it does not claim to be a universal
sandbox.

Production deployment checklist:

- Run as a numeric non-root user with a read-only root filesystem.
- Drop every Linux capability, enable `no-new-privileges`, and use the runtime's
  default or a stricter reviewed seccomp/AppArmor profile.
- Set CPU, memory, PID, open-file, wall-time, token, cost, tool-call, mutation,
  and injection limits.
- Mount a unique, size-limited `tmpfs` workspace. Never mount the Docker socket,
  host home directory, source-control credentials, or production data.
- Deny egress by default. If a model provider is required, route traffic through
  a controlled proxy that allowlists the exact HTTPS provider endpoints. Docker
  Compose alone cannot securely enforce hostname-level egress allowlists.
- Put Postgres on a private network. Give the harness a dedicated Worker Worlds
  database credential; give worker containers no database credential or route.
- Inject short-lived, test-project provider credentials at runtime through the
  platform secret store—not images, YAML, command lines, traces, or scenarios.
  Rotate immediately after suspected disclosure.
- Use a fresh container and workspace per evaluation, terminate descendants on
  timeout/cancellation, retain redacted evidence, revoke credentials, and audit
  leases/schemas during incident response.

Isolation claims:

| Boundary | Status |
|---|---|
| World state | Implemented |
| Database run schema | Implemented |
| Worker process | External deployment control |
| Host/VM | External deployment control |
| Network egress | External deployment control |
| Provider credentials | Deployment responsibility |

The network-disabled profile can run local deterministic workers. A provider
profile must be designed for the chosen infrastructure and egress proxy; do not
simply attach the container to an unrestricted bridge network.
