# Marketing Campaign Analyst scenario matrix (Phase 2, retrospective)

Companion to `docs/marketing-analyst-scenario-matrix.md` (Phase 1, scenarios 001-040).
Unlike that document, this one is **backfilled** — written after `campaign_analyst_scenarios()`
was extended, describing the batch as authored, the same relationship
`docs/claims-analyst-scenario-matrix.md` has to its own corpus. It is referenced directly from
the `campaign_analyst_scenarios()` docstring and from the `seed_overrides_phase2` comment in
`src/worker_worlds/enterprise_scenarios.py`.

## Scope: scenarios 041-150

Phase 2 adds 110 scenarios (indices 41-150) to the existing Phase 1 batch (indices 1-40),
taking the `campaign-analyst` full suite from 40 to 150 scenarios. Every Phase 1 definition and
seed is unchanged — Phase 2 is purely additive, appended as a second `definitions_phase2` tuple
merged after the original `definitions` tuple in `campaign_analyst_scenarios()`.

Per the coverage comment directly above `definitions_phase2`, Phase 2 specifically adds:

- A new capability, **`analyst-note-taking`**, covering the previously-untested
  `add_campaign_note` tool (0 scenarios in Phase 1, 16 in Phase 2).
- **Cross-advertiser mutation-boundary coverage** via `_authorize_mutation` — distinct from the
  missing-scope adversarial mechanism Phase 1 already covered.
- **Cross-advertiser read-boundary coverage** for every read tool besides `search_campaigns`
  (which Phase 1 already exercised).
- The two fixture bands Phase 1 left unused: `700-799` (completed) and `800-899` (rejected).
- The previously-untested `partial_budget_approve` recommendation kind.
- The related campaign (`cmp_101`) used as a primary mutation/read target, not just a
  cross-reference.
- 3-call chains (Phase 1 tool sequences topped out at two calls).
- Idempotency-conflict coverage on every mutation tool besides `record_launch_recommendation`
  (which Phase 1 already covered).

A `campaign-manager` role remains explicit follow-up work, not attempted in Phase 2 either
(unchanged from ADR 008 / Phase 1's scope note).

## Capability coverage (Phase 2 additions vs. combined total)

| Capability | Phase 1 | Phase 2 | Total | Primary tool(s) |
|---|---:|---:|---:|---|
| launch-recommendation | 8 | 21 | 29 | `record_launch_recommendation` |
| performance-anomaly-triage | 6 | 21 | 27 | `flag_campaign_for_review`, `get_related_campaigns` |
| campaign-intake-review | 7 | 12 | 19 | `search_campaigns`, `inspect_campaign_brief` |
| risk-escalation | 3 | 12 | 15 | `escalate_compliance_review` |
| analyst-note-taking | 0 | 16 | 16 | `add_campaign_note` |
| budget-exposure-analysis | 6 | 7 | 13 | `calculate_budget_exposure` |
| creative-compliance-assessment | 5 | 8 | 13 | `list_creative_assets`, `get_creative_asset` |
| audience-data-followup | 3 | 8 | 11 | `request_suppression_update` |
| audience-segment-analysis | 2 | 5 | 7 | `get_audience_segment` |
| **Total** | **40** | **110** | **150** | |

`analyst-note-taking` is the only capability introduced from zero — every other capability was
already exercised in Phase 1 and simply gained more boundary-condition coverage in Phase 2.

## Difficulty distribution (Phase 2 additions vs. combined total)

| Difficulty | Phase 1 | Phase 2 | Total |
|---|---:|---:|---:|
| basic | 10 | 10 | 20 |
| intermediate | 14 | 34 | 48 |
| advanced | 8 | 31 | 39 |
| adversarial | 8 | 35 | 43 |
| **Total** | **40** | **110** | **150** |

Growth is concentrated in `adversarial` and `advanced`, matching the Phase 2 focus on boundary
conditions (cross-advertiser checks, idempotency conflicts, illegal transitions) rather than
additional simple reads, though Phase 2 does add a proportional share of new `basic` scenarios
too.

## Risk-category distribution (Phase 2 additions vs. combined total)

| Risk category | Phase 1 | Phase 2 | Total | Covers |
|---|---:|---:|---:|---|
| operational | 18 | 53 | 71 | Intake, creative/compliance review, note-taking, data follow-up, escalation, general triage |
| authorization | 8 | 29 | 37 | Missing-scope mutations, cross-advertiser read/mutation boundaries, privileged-tool rejection |
| financial | 14 | 28 | 42 | Budget-cap, channel-sub-cap, and platform-fee boundary scenarios via `calculate_budget_exposure` |
| **Total** | **40** | **110** | **150** | |

`authorization` grows the most proportionally, driven by the new cross-advertiser
read/mutation-boundary coverage described above.

## Fixture seed bands exercised in Phase 2

Phase 2 uses the same `build_marketing_state` seed-band convention Phase 1 established (see
`docs/marketing-analyst-scenario-matrix.md`'s band table). `seed_overrides_phase2` in
`enterprise_scenarios.py` maps a subset of indices 41-150 to a non-default seed; every index
absent from that dict falls back to `seed_overrides.get(index, index)` and lands in the
`baseline` band (`< 100` or `>= 1300`), the same default Phase 1 uses.

Bands hit by an explicit Phase 2 seed override:

| Band | Phase 2 overrides using this band |
|---|---:|
| completed (`700-799`) | 9 |
| shared-segment duplicate (`1100-1199`) | 8 |
| suspended advertiser (`1200-1299`) | 7 |
| exceeds total budget cap (`200-299`) | 6 |
| rejected (`800-899`) | 5 |
| invalid intake chronology (`1000-1099`) | 5 |
| exceeds channel sub-cap (`300-399`) | 4 |
| invalid flight window (`900-999`) | 4 |
| data requested (`400-499`) | 3 |
| already live (`600-699`) | 3 |
| under compliance review (`500-599`) | 2 |

The `completed` and `rejected` bands were the two Phase 1 left unused (per the coverage comment
above `definitions_phase2`); Phase 2 exercises both. The `below platform fee` band (`100-199`)
has no explicit Phase 2 override — it was already covered in Phase 1 and Phase 2 did not need to
re-exercise it.

## Cross-advertiser boundary coverage

Phase 2 introduces `actor_customer_overrides`, a separate mechanism from Phase 1's missing-scope
adversarial checks. A handful of indices (48, 51-60, 148) override the scenario's actor identity
to `other_adv_9001` instead of the real `adv_500`, simulating a caller from outside the assigned
advertiser. Every index absent from that map keeps the real actor. This exercises
`_authorize_mutation`/`_authorize_read` in `marketing.py`, distinct from `_require_scope`
(the mechanism Phase 1's adversarial scenarios exercise with an empty `scopes: []` list).

## Authority boundary — unchanged from Phase 1

Phase 2 adds no new privileged-tool coverage and does not touch the authority boundary itself:
`launch_campaign`, `send_campaign_communication`, and `allocate_campaign_budget` remain
structurally unreachable by `campaign-analyst` — no Phase 2 scenario scripts
`campaign:launch`, `campaign:send`, or `campaign:budget-commit`. That boundary is proven the
same way Phase 1 documents it: by
`test_campaign_analyst_cannot_launch_send_or_allocate_under_any_analyst_scope_combination` in
`tests/test_enterprise_domains.py`, not by scenario omission alone.

## Cross-cutting checks (mirrors Phase 1's rubric)

- Every tool call scripted in `metadata.stub_tool_calls` has a matching `tool_result_matches`
  assertion; every mutation has a positive state/event assertion; every forbidden action has a
  `no_action` assertion — the same generic assertion helpers Phase 1 uses
  (`_campaign_analyst_outcome_assertion()` / `tool_result_assertions()`), not hand-written per
  scenario.
- Scenario IDs are `marketing.campaign-analyst.NNN`, zero-padded 041-150, unique, sorted,
  continuing directly from Phase 1's 001-040 range with no reuse.
- Every ID, amount, and date referenced in the prompt is inline in the prompt content, per the
  same `live_prompt()` renderer Phase 1 and Insurance use.

## Test coverage (verified, not assumed)

- `tests/test_enterprise_scenarios.py::test_enterprise_scenarios_are_checked_and_catalogued`
  round-trips every generated scenario (all 150, Phase 1 and Phase 2 together) against its
  exported YAML file under `scenarios/enterprise/`.
- `tests/test_enterprise_scenarios.py::test_new_roles_have_smoke_standard_and_full_suites`
  asserts the `campaign-analyst` full suite contains exactly 150 scenario IDs.
- `tests/test_catalog.py::test_builtin_catalog_is_complete_and_canonical` asserts the catalog's
  total classification count (607, which includes all 150 campaign-analyst scenarios).
- `tests/test_enterprise_domains.py` exercises the domain/runtime behavior Phase 2 scenarios
  depend on, including `test_campaign_analyst_note_is_atomic_idempotent_and_rollback_safe`
  (the `add_campaign_note`/`analyst-note-taking` atomicity guarantee) and
  `test_campaign_analyst_cannot_launch_send_or_allocate_under_any_analyst_scope_combination`
  (the unchanged authority boundary).
- A full-batch run against real PostgreSQL, and an independent domain reviewer's sign-off, carry
  the same open-item caveat Phase 1's rubric (`docs/marketing-analyst-review-rubric.md`) already
  records — not yet verified for Phase 2 either.
