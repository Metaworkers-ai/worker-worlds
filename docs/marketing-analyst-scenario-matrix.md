# Marketing Campaign Analyst scenario matrix (Phase 1, prospective)

Written **before** scenario authoring, unlike `docs/claims-analyst-scenario-matrix.md`
(which was explicitly backfilled — see its own note). This is the Phase 1 target: 40
scenarios, `campaign-analyst`, suite sizes `smoke=8, standard=20, full=40`
(`_SUITE_SIZE_OVERRIDES` in `catalog.py`). `campaign_analyst_scenarios()` must produce
exactly this distribution; `tests/test_enterprise_scenarios.py` and `tests/test_catalog.py`
assert the totals below as ground truth once authored, and this file is the plan those
counts are checked against, not the other way around.

A `campaign-manager` role, and scaling this suite toward the ~100-scenario range Insurance
Claims Analyst eventually reached, is explicit Phase 2 follow-up (ADR 008) — not attempted
here.

## Capability coverage target

| Capability | Scenario count | Primary tool(s) |
|---|---:|---|
| launch-recommendation | 8 | `record_launch_recommendation` |
| campaign-intake-review | 7 | `search_campaigns`, `inspect_campaign_brief` |
| performance-anomaly-triage | 6 | `flag_campaign_for_review`, `get_related_campaigns` |
| budget-exposure-analysis | 6 | `calculate_budget_exposure` |
| creative-compliance-assessment | 5 | `list_creative_assets`, `get_creative_asset` |
| audience-data-followup | 3 | `request_suppression_update` |
| risk-escalation | 3 | `escalate_compliance_review` |
| audience-segment-analysis | 2 | `get_audience_segment` |
| **Total** | **40** | |

`audience-segment-analysis` is deliberately the thinnest capability (only direct
segment/budget-envelope reads), mirroring `policy-coverage-analysis` being Insurance Claims
Analyst's thinnest capability — the segment/budget-envelope facts are more often exercised
indirectly, through `calculate_budget_exposure`, than read directly.

## Difficulty distribution target

| Difficulty | Count | Purpose |
|---|---:|---|
| basic | 10 | Single-call reads and straightforward recommendations against the baseline fixture |
| intermediate | 14 | Multi-fact reads, non-default seed bands, related-campaign/duplicate-segment cases |
| advanced | 8 | Multi-call sequences, idempotency, related-campaign cross-referencing |
| adversarial | 8 | Authority-boundary probes (privileged-tool and missing-scope attempts), illegal status transitions |

## Risk-category distribution target

| Risk category | Count | Covers |
|---|---:|---|
| operational | 18 | Intake, creative/compliance review, data follow-up, escalation, general triage |
| financial | 14 | Budget-cap, channel-sub-cap, and platform-fee boundary scenarios via `calculate_budget_exposure` |
| authorization | 8 | Missing-scope mutations, privileged-tool rejection (`launch_campaign`, `send_campaign_communication`, `allocate_campaign_budget`), advertiser-boundary checks |

## Fixture seed bands exercised (from `build_marketing_state`)

| Band | Seeds | Boundary condition |
|---|---|---|
| baseline | `< 100` or `>= 1300` | Draft campaign, within budget, valid chronology |
| below platform fee | `100–199` | `net_deployable_minor == 0` |
| exceeds total budget cap | `200–299` | `exceeds_total_cap == True` |
| exceeds channel sub-cap | `300–399` | `exceeds_channel_cap == True` |
| data requested | `400–499` | `status == data_requested` |
| under compliance review | `500–599` | `status == under_review` |
| already live | `600–699` | `status == live` (illegal-transition target) |
| completed | `700–799` | `status == completed` (illegal-transition target) |
| rejected | `800–899` | `status == rejected` (illegal-transition target) |
| invalid flight window | `900–999` | `within_flight_window == False` |
| invalid intake chronology | `1000–1099` | `intake_chronology_valid == False` |
| shared-segment duplicate | `1100–1199` | related campaign reuses the primary's segment |
| suspended advertiser | `1200–1299` | `advertiser_active == False` |

Every scenario's seed is chosen from the band that produces the fact its assertions need —
mirroring Insurance's `ANALYST_SEED_FLOOR` seed-banding technique, adapted to a fresh
numbering (marketing has no legacy fixture below a floor to preserve).

## Cross-cutting checks every scenario must satisfy (mirrors
`docs/claims-analyst-review-rubric.md`'s mechanical checks)

- Every tool call scripted in `metadata.stub_tool_calls` has a matching `tool_result_matches`
  assertion; every mutation has a positive state/event assertion; every forbidden action has
  a `no_action` assertion.
- No scenario ever scripts `campaign:launch`, `campaign:send`, or `campaign:budget-commit`
  scopes — those three are proven unreachable by a dedicated `test_enterprise_domains.py`
  test instead, not by omission alone.
- Every ID, amount, and date referenced in the prompt is inline in the prompt content — no
  hidden fixture knowledge.
- Scenario IDs are `marketing.campaign-analyst.NNN`, zero-padded 001–040, unique, sorted.
