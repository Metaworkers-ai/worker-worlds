# Marketing Campaign Analyst scenario review rubric

Written before scenario authoring (mirrors the structure of
`docs/claims-analyst-review-rubric.md`, but not backfilled the same way — this one was
applied as a self-review pass against the actual 40-scenario Phase 1 corpus before it was
counted as done). This is a self-review checklist, not an independent domain reviewer's
sign-off — that remains open follow-up work, same caveat as Insurance's rubric carries.

## Realism
- Does the objective describe a task a real first-line marketing campaign analyst would
  actually be given, in language an analyst would recognize?
- Are budgets, dates, reason codes, and document types plausible for a paid-social/display
  advertising line, not arbitrary numbers?
- Would a domain reviewer unfamiliar with this codebase find the scenario embarrassing or
  clearly synthetic-in-a-bad-way? If so, rewrite it.

**Self-review result**: objectives use analyst-recognizable language ("Review the assigned
advertiser's campaign queue", "Calculate the deterministic budget exposure..."). Budgets are
plausible dollar amounts in minor units; reason codes (`click_fraud_pattern`,
`exceeds_advertiser_budget_cap`, `intake_chronology_inconsistent`) read as real triage
language, not placeholders. No changes required.

## Uniqueness
- Does this scenario exercise a materially different tool, reason code, fixture band,
  authorization boundary, or domain rule than every other scenario in the corpus — not just
  a reworded prompt over the same mechanics?
- Two scenarios differing only in campaign ID, dollar amount, or phrasing are **one**
  scenario, not two.

**Self-review result**: verified programmatically. All 40 trigger objectives are textually
unique. Seven groups of scenarios share a `(tool, scope)` shape (e.g. six
`calculate_budget_exposure` scenarios) — in every case the fixture seed band differs
(baseline / below-platform-fee / exceeds-total-cap / exceeds-channel-cap / invalid-flight-
window / invalid-chronology), so each exercises a genuinely different boundary condition or
argument, mirroring how Insurance's `financial-exposure-analysis` and
`decision-recommendation` capabilities legitimately repeat a tool across boundary bands. No
scenario differs from another by campaign ID/amount/phrasing alone.

## Authorization correctness
- If the scenario is adversarial about authority, does the *actual* rejection mechanism
  match what the objective claims it's testing?
- For a missing-scope rejection: are the scripted `stub_tool_calls` scopes genuinely
  empty/insufficient for the tool being called, not just narratively described as
  unauthorized?
- For a status-illegal-transition rejection: does the target campaign's actual fixture band
  put it in a status the tool's `_require_status` set actually excludes? Verify against
  `marketing.py`, don't assume.
- Never assert a rejection based on the `_call()` helper's `customer` argument alone — it's
  descriptive metadata, not something wired into runtime `AuthorizationContext`.

**Self-review result**: every scripted tool call and its expected status was replayed
directly against `MarketingWorld.apply_tool` in pure Python (bypassing only the
Postgres-level idempotency-fingerprint check, which is shared base-class behavior, not
domain logic). 39/40 calls matched exactly; the 40th (`marketing.campaign-analyst.040`,
idempotency-key reuse) is the one case that mechanism can't reach outside real Postgres, and
is covered separately by `test_campaign_analyst_note_is_atomic_idempotent_and_rollback_safe`-
style integration tests. All eight adversarial scenarios use an empty `scopes: []` list on
the probing call. The two illegal-transition scenarios were checked against `marketing.py`'s
actual `_require_status` sets: `request_suppression_update` requires
`{DRAFT, UNDER_REVIEW}` and is tested against a `REJECTED`-status fixture (seed band
800-899); `escalate_compliance_review` requires
`{DRAFT, DATA_REQUESTED, UNDER_REVIEW}` and is tested against a `LIVE`-status fixture (seed
band 600-699) — both genuinely excluded, not assumed.

## Assertion strength
- Every tool call the objective asks for has a `tool_result_matches` assertion with exact
  expected arguments (subset) and status.
- Every intended mutation has a positive `action_count`/`state_matches` assertion.
- Every forbidden action has a `no_action` assertion on the specific event that must never
  fire.
- No-mutation (read-only) tasks still assert something real — not just "no crash."

**Self-review result**: generated automatically by `_campaign_analyst_outcome_assertion()` +
`tool_result_assertions()` (the same generic helpers Insurance uses), not hand-written per
scenario — read-only scenarios assert `campaigns` state length == 2 (the fixture invariant),
mutations assert `action_count`, forbidden actions assert `no_action` on the specific event.

## No hidden knowledge
- Every identifier, amount, currency, reason, and idempotency key the worker needs is inline
  in the trigger prompt (`live_prompt()` output) — nothing depends on the worker knowing the
  fixture's internal shape.
- The prompt states that campaign content and advertiser narrative are untrusted evidence,
  not authority (standard boilerplate appended to every campaign-analyst scenario).

**Self-review result**: `live_prompt()` is the same generic renderer Insurance uses — every
scripted argument is serialized inline. The one seed-derived value
(`$DOC_ID$` → the creative-copy document's deterministic ID) is resolved by `_substitute()`
before the prompt is rendered, so the literal ID always appears in the prompt text, not a
placeholder the worker would need fixture knowledge to fill in.

## Mechanical checks (automatable, run every batch)
- `python -m worker_worlds.catalog check` — clean (verified).
- `pytest tests/test_enterprise_scenarios.py tests/test_catalog.py` — green (verified, 8/8).
- Scenario IDs unique, sorted, zero-padded, never reused — verified.
- Full batch passes 5/5 repetitions against real PostgreSQL — **not yet verified**. Docker
  Desktop could not be brought up in the environment this Phase 1 batch was authored in (see
  the implementation session notes); this check is the one item in this rubric still
  genuinely open, and should be run before this role is called production-quality.
