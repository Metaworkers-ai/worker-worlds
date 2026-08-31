# ADR 008: Marketing Campaign Analyst role and authority boundary

Status: accepted

Worker Worlds adds a new `marketing` domain and a `campaign-analyst` role, backed by a new
`postgres-marketing` world and `MarketingWorld` implementation. The analyst reviews
campaign intake briefs, analyzes audience-segment and budget exposure, assesses creative
and compliance evidence, requests missing audience data (consent/suppression records),
records non-binding launch recommendations, flags campaigns for risk review, and escalates
campaigns into compliance review. It never launches a campaign, sends a customer-facing
communication, or commits advertiser budget; those tools (`launch_campaign`,
`send_campaign_communication`, `allocate_campaign_budget`) are defined on the world for a
future `campaign-manager` role and are structurally unreachable by the analyst.

This is a new domain, not a second role on an existing one — there is no pre-existing
Marketing world or role in this repository. The design otherwise follows the pattern
established by ADR 007 (`docs/adr/007-claims-analyst-role.md`, the Insurance Claims
Analyst role) as closely as the domains' different subject matter allows: same
authority-boundary mechanism, same `JsonPostgresWorld` base, same non-binding
recommendation model, same seed-banded deterministic fixture technique.

## Why an authority boundary, and why now

A marketing campaign, like an insurance claim, has a step where analysis ends and an
externally consequential action begins: launching a campaign spends an advertiser's budget
and puts creative in front of real audiences, sending a campaign communication reaches real
recipients, and committing budget moves real money. All three are effectively irreversible
once executed and none of them should be reachable by a role whose job is investigation and
recommendation. Rather than build a `campaign-manager` role now and hope the boundary holds,
the boundary is designed in from the start of the domain: `launch_campaign`,
`send_campaign_communication`, and `allocate_campaign_budget` exist on `MarketingWorld`
today (so the domain model is coherent and the boundary is provable), but no scenario or
scope in this phase ever grants a `campaign-analyst` run the scopes they require. A future
`campaign-manager` role can be added by granting those scopes to its own scenarios, exactly
as `claims-adjuster` already holds `claim:decide`/`claim:pay` today.

## Authority boundary mechanism

Identical mechanism to ADR 007: enforced through `AuthorizationContext.scopes`, a
harness-trusted field the worker cannot set, checked per-call inside
`MarketingWorld.apply_tool` via `_require_scope`. No new field is added to
`AuthorizationContext` — that would touch the frozen `WorldEvent`/`RunRecord` evidence
contract. Each scenario's `metadata.stub_tool_calls` declares the scopes granted for each
tool call it scripts, and `runner.py`'s `_TOOL_SCOPE_POLICY` maps every marketing tool name
to its required scope. A `campaign-analyst` scenario never scripts `campaign:launch`,
`campaign:send`, or `campaign:budget-commit`; if a worker attempts any of those tools
anyway, `_require_scope` rejects it regardless of arguments, because the scope was never
granted. This is proven directly by a test that grants an `AuthorizationContext` **every**
legitimately-grantable analyst scope simultaneously and confirms all three privileged tools
still return `AuthorizationDenied` with zero state or event change.

As in ADR 007, this is scope-based enforcement, not tool-discovery filtering.
`MarketingWorld.tools()` returns the full tool list regardless of caller — filtering
discovery by scope was rejected for the same reason: `runner.py`'s discovery-time
`AuthorizationContext` always carries an empty scope set, so filtering discovery by scope
would hide every tool from every caller, not just the analyst-prohibited ones.

## Domain model

No SQL migration. Marketing world state is one JSON blob per run-namespace (`world_state`
table via `JsonPostgresWorld`), the same spine `InsuranceWorld` and `SupplyChainWorld`
already use, so the domain model is additive Python/pydantic state, not schema.
`migration_version` stays `"006"`.

Entities: `Advertiser` (the account owning campaigns), `Campaign` (the unit under review —
proposed budget, spend to date, flight window, channel, objective, assigned analyst, linked
related campaigns), `AudienceSegment` (channel budget cap, platform-fee floor, per-campaign-
type channel sub-caps, exclusion list — the marketing analog of a policy's coverage),
`CampaignBrief` (the intake narrative backing a campaign, analog of an insurance incident),
`CreativeAsset` (one creative or audience-data document attached to a campaign — reused for
both creative review and requested audience-data follow-up, exactly as `EvidenceItem`
serves both evidence review and evidence-request follow-up in Insurance), `RiskFlag`,
`LaunchRecommendation` (explicitly `binding=False`), and `BudgetExposureResult` (the
deterministic budget/reach calculation output).

Marketing-specific vocabulary and logic — advertiser/campaign/audience-segment/creative-
asset entities, campaign lifecycle states, channel budget-cap and platform-fee math, flight-
window and intake-chronology validation — is genuinely new domain logic, not a renamed copy
of the Insurance fixtures. Only the *shape* of the pattern (seed-banded deterministic
fixtures, non-binding recommendation entity, scope-gated mutations, a pure calculation tool
producing several boundary-condition booleans) is reused.

## Stable identifiers

- Domain: `marketing`, label "Marketing". Role: `campaign-analyst`, label "Marketing
  Campaign Analyst".
- World: `postgres-marketing`, registry key `marketing`, `MarketingWorld(JsonPostgresWorld)`.
- Capabilities: `campaign-intake-review`, `audience-segment-analysis`,
  `creative-compliance-assessment`, `budget-exposure-analysis`,
  `performance-anomaly-triage`, `launch-recommendation`, `audience-data-followup`,
  `risk-escalation`.
- Analyst scopes: `campaign:read`, `campaign:analyst-note`, `campaign:recommend`,
  `campaign:flag`, `campaign:request`, `campaign:escalate`.
- Privileged scopes (never granted in this phase): `campaign:launch`, `campaign:send`,
  `campaign:budget-commit`.
- Tools (analyst-reachable): `search_campaigns`, `get_audience_segment`,
  `inspect_campaign_brief`, `list_creative_assets`, `get_creative_asset`,
  `get_related_campaigns`, `calculate_budget_exposure` (reads); `add_campaign_note`,
  `record_launch_recommendation`, `flag_campaign_for_review`, `request_suppression_update`,
  `escalate_compliance_review` (mutations).
- Tools (privileged, world-defined, structurally unreachable by `campaign-analyst`):
  `launch_campaign`, `send_campaign_communication`, `allocate_campaign_budget`.
- `MARKETING_ANALYST_PROHIBITED_TOOLS = frozenset({"launch_campaign",
  "send_campaign_communication", "allocate_campaign_budget"})` in `runner.py` — a
  documentation/testing marker; the actual enforcement is the scope never being granted.
- Events: `campaign.analyst_note_added`, `campaign.recommendation_recorded`,
  `campaign.risk_flagged`, `campaign.data_requested`, `campaign.compliance_review_escalated`
  (analyst-reachable); `campaign.launched`, `campaign.communication_sent`,
  `campaign.budget_allocated` (privileged, unreachable in this phase).
- Suites: `marketing.campaign-analyst.{smoke,standard,full,custom}`.
- Scenario IDs: `marketing.campaign-analyst.NNN`, zero-padded, never reused.

## Compatibility

Purely additive: a new domain, a new role, a new world module, new catalog entries, new
scope-policy entries. Nothing in `commerce` or `insurance` — capabilities, roles, tools,
scopes, events, suites, or scenarios — is touched. Schema major version 1 is untouched.

## Scope note: Phase 1

This phase ships only the `campaign-analyst` role and its 40-scenario suite. A
`campaign-manager` role (holding `campaign:launch`/`campaign:send`/`campaign:budget-commit`)
is deliberately out of scope for this phase; the privileged tools exist on `MarketingWorld`
now so the boundary is real and provable, not because that role is being built next.
