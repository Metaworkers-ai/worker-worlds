# Threat model

Worker Worlds treats worker behavior plus scenario messages, injected content,
and external evidence as untrusted. Scenario control-plane fields (world,
budgets, assertions, and authorization fixtures) are trusted test-author input
and must come from a reviewed catalog. The runner maps fixture authorization to
a fixed per-tool scope policy; arbitrary scope names in YAML are ignored.
Trusted components are the runner, authorization policy, world gateway,
migration set, grader, and reporter redaction layer.

| Boundary | Current control | Limitation |
|---|---|---|
| World state | deterministic reset and tool-only mutation | simulations can drift from production |
| Database | validated database names and per-run schemas | local credentials still grant the dev database |
| Process | deadlines, budgets, cancellation | worker code runs in the caller's process unless externally isolated |
| Network | no core network calls | no host-level egress firewall is installed by this package |
| Secrets | structured key redaction before reports | arbitrary secrets embedded in free text cannot always be detected |

Scenario YAML uses `safe_load`, a 1 MB limit, a 32-level nesting limit, strict
Pydantic schemas, and rejects symlinks. Tool inputs are strict and bounded.
Artifact names are derived from validated ULIDs. Report text is escaped. Baseline
manifests are content addressed. SQL namespaces and cleanup targets are validated
before interpolation.

Operators must isolate untrusted workers in a container/VM, deny network by
default, scope database credentials to a dedicated Worker Worlds database, and
keep provider credentials outside scenarios and artifacts. ZIP extraction and
worker subprocess management are not implemented by the core package.
