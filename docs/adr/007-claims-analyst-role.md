# ADR 007: Insurance Claims Analyst role and authority boundary

Status: accepted

Worker Worlds adds `claims-analyst` as a second Insurance role alongside the existing
`claims-adjuster`, sharing the same `postgres-insurance` world and `InsuranceWorld`
implementation. The analyst investigates, calculates coverage exposure, requests evidence,
records non-binding recommendations, flags claims for fraud review, and escalates
investigations. It never decides a claim or issues a payment; those tools
(`decide_claim`, `issue_claim_payment`) remain exclusive to the adjuster.

## Authority boundary mechanism

The boundary is enforced the same way the adjuster's tool authority already is: through
`AuthorizationContext.scopes`, a harness-trusted field the worker cannot set, checked
per-call inside `InsuranceWorld.apply_tool` via `_require_scope`. `AuthorizationContext`
gains no new `role_id` field — that would touch a frozen evidence contract (`WorldEvent`
embeds it, and `WorldEvent` is part of `RunRecord`). Instead, each scenario's
`metadata.stub_tool_calls` declares the scopes granted for each tool call it scripts, and
`runner.py`'s `_TOOL_SCOPE_POLICY` maps every tool name to its required scope. A
claims-analyst scenario simply never scripts `claim:decide` or `claim:pay`; if a worker
attempts either tool anyway, `_require_scope` rejects it regardless of arguments, because
the scope was never granted. This was verified directly: `decide_claim` and
`issue_claim_payment` invoked with an analyst-scoped `AuthorizationContext` fail
authorization under every argument shape tried, including when every *other* analyst
scope is present.

This is scope-based enforcement, not tool-discovery filtering. `InsuranceWorld.tools()`
still returns the full tool list regardless of caller — filtering it by scope was
considered and rejected, because `runner.py`'s discovery-time `AuthorizationContext` is
always built with an empty scope set (scopes are only resolved per specific tool call),
so filtering discovery by scope would hide every tool from every caller, not just the
analyst-prohibited ones.

## Domain model

No new SQL migration. Insurance world state is one JSON blob per run-namespace
(`world_state` table via `JsonPostgresWorld`), not per-entity relational tables, so the
richer analyst domain model (evidence trust classification, coverage-analysis results,
recommendations, risk flags, related-claim links, lifecycle/timing boundary fixtures) is
additive Python/pydantic state, not schema. `migration_version` stays `"006"`.

The analyst's richer entities (evidence, recommendations, risk flags) are represented as
typed-key dicts inside `world_state`, matching the existing pattern the adjuster's
documents/notes/investigations/payments already use, rather than as new top-level pydantic
model classes. This was a deliberate scope trade-off to ship working, Postgres-verified
functionality first; it does not yet give evidence items, recommendations, or risk flags
their own strict pydantic schemas the way `Policy`/`Coverage`/`Claim` have. Promoting them
to typed models remains open follow-up work and does not require a contract-breaking
change when done, since `InsuranceModel.model_dump(mode="json")` output shape is
unaffected by whether the Python-side construction is a dict literal or a model instance.

## Stable identifiers

- Role: `claims-analyst`, label "Insurance Claims Analyst", domain `insurance`.
- Capabilities: `claim-intake-review`, `policy-coverage-analysis`, `evidence-assessment`,
  `financial-exposure-analysis`, `fraud-anomaly-triage`, `decision-recommendation`,
  `evidence-request-followup`, `investigation-escalation`.
- Scopes: `claim:read`, `claim:evidence` (shared with the adjuster), `claim:analyst-note`,
  `claim:recommend`, `claim:flag`, `claim:investigate` (shared with the adjuster).
- Tools: `search_claims`, `get_coverage`, `inspect_incident`, `list_claim_evidence`,
  `get_evidence`, `get_related_claims`, `calculate_coverage_analysis` (read);
  `add_analyst_note`, `record_claim_recommendation`, `flag_claim_for_review` (new
  mutations); `request_evidence`, `escalate_investigation` (reused from the adjuster,
  unchanged).
- Events: `claim.evidence_requested` (reused), `claim.analyst_note_added`,
  `claim.recommendation_recorded`, `claim.risk_flagged`, `claim.investigation_escalated`.
- Suites: `insurance.claims-analyst.{smoke,standard,full,custom}`.
- Scenario IDs: `insurance.claims-analyst.NNN`, zero-padded, never reused.

## Compatibility

Purely additive. `claims-adjuster`'s entities, tools, scopes, events, suites, and 12
scenarios are unchanged — confirmed by byte-identical scenario export hashes before and
after this work. Schema major version 1 is untouched.
