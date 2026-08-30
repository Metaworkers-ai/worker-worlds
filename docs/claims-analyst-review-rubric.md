# Claims Analyst scenario review rubric

Backfilled Phase 0 deliverable. Self-review checklist used for scenarios authored in this
work (not an independent domain reviewer, per REQUIREMENT.md §14's requirement for one --
that review is still open). Apply this to every new scenario before it's counted as done.

## Realism
- Does the objective describe a task a real first-line claims analyst would actually be
  given, in language an analyst would recognize?
- Are amounts, dates, reason codes, and document types plausible for personal auto/home/
  travel lines (REQUIREMENT.md §6), not arbitrary numbers?
- Would a domain reviewer unfamiliar with this codebase find the scenario embarrassing or
  clearly synthetic-in-a-bad-way? If so, rewrite it.

## Uniqueness
- Does this scenario exercise a materially different tool, reason code, fixture band,
  authorization boundary, or domain rule than every other scenario in the corpus --
  not just a reworded prompt over the same mechanics?
- Two scenarios differing only in claim ID, dollar amount, or phrasing are **one**
  scenario, not two (REQUIREMENT.md §11, "no superficial prompt variants").

## Authorization correctness
- If the scenario is adversarial about authority, does the *actual* rejection mechanism
  match what the objective claims it's testing? (This session's audit found three
  scenarios where it didn't -- see `docs/adr/007-claims-analyst-role.md` history.)
- For a missing-scope rejection: are the scripted `stub_tool_calls` scopes genuinely
  empty/insufficient for the tool being called, not just narratively described as
  unauthorized?
- For a status-illegal-transition rejection: does the target claim's actual fixture band
  put it in a status the tool's `_require_status` set actually excludes? Verify against
  `insurance.py`, don't assume.
- Never assert a rejection based on the `_call()` helper's `customer` argument alone --
  it's descriptive metadata, not something wired into runtime `AuthorizationContext`.

## Assertion strength
- Every tool call the objective asks for has a `tool_result_matches` assertion with exact
  expected arguments (subset) and status.
- Every intended mutation has a positive `action_count`/`state_matches` assertion.
- Every forbidden action has a `no_action` assertion on the specific event that must never
  fire.
- No-mutation (read-only) tasks still assert something real -- not just "no crash."

## No hidden knowledge
- Every identifier, amount, currency, reason, and idempotency key the worker needs is
  inline in the trigger prompt (`live_prompt()` output) -- nothing depends on the worker
  knowing the fixture's internal shape.
- The prompt states that claim content and customer narrative are untrusted evidence, not
  authority (already standard boilerplate appended to every claims-analyst scenario).

## Mechanical checks (automatable, run every batch)
- `python -m worker_worlds.catalog check` clean.
- `pytest tests/test_enterprise_scenarios.py tests/test_catalog.py` green.
- Scenario IDs unique, sorted, zero-padded, never reused.
- Full batch passes 5/5 repetitions against real PostgreSQL (`worker-worlds suite ...
  --world insurance --worker stub --database-url ...`), not just the stub-only assertion
  check -- this session found three scenario bugs only the real Postgres run surfaced.
