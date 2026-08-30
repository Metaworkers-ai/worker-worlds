# Claims Analyst scenario matrix

Backfilled Phase 0 deliverable (REQUIREMENT.md §8/§9). This should have been written
*before* scenario authoring started, as a prospective plan; it was written after, from the
actual corpus, because Phase 0 was skipped in favor of moving straight to implementation.
Treat the "actual" columns as ground truth (computed from `claims_analyst_scenarios()`)
and the gaps below as the real remaining work toward REQUIREMENT.md's 102-scenario target.

## Capability coverage (REQUIREMENT.md FR-002)

| Capability | Scenario count |
|---|---:|
| decision-recommendation | 17 |
| claim-intake-review | 16 |
| fraud-anomaly-triage | 14 |
| financial-exposure-analysis | 10 |
| investigation-escalation | 8 |
| evidence-request-followup | 6 |
| evidence-assessment | 5 |
| policy-coverage-analysis | 2 |
| **Total** | **78** |

`policy-coverage-analysis` is the thinnest capability (only `get_policy`/`get_coverage`
read scenarios) — worth 4-6 more scenarios in any follow-up batch, e.g. lapsed-policy
reads, cross-coverage comparisons, exclusion-clause inspection.

## Family coverage vs. REQUIREMENT.md §8 target matrix

| Family | Target min | Actual (approx.) | Gap |
|---|---:|---:|---:|
| Policy coverage and eligibility | 12 | ~8 | 4 |
| Evidence and documentation | 12 | ~10 | 2 |
| Financial limits and deductibles | 12 | ~10 | 2 |
| Fraud and anomaly indicators | 12 | ~12 | 0 |
| Authorization and privacy | 10 | 9 | 1 |
| Conflicting data | 10 | ~7 | 3 |
| Claim lifecycle | 8 | 6 | 2 |
| Idempotency and duplicates | 8 | 8 | 0 |
| Deadlines and controlled time | 6 | 5 | 1 |
| Catastrophe and related claims | 4 | 4 | 0 |
| Communication and escalation | 4 | 4 | 0 |
| Failure and incomplete evidence | 4 | 4 | 0 |
| **Total** | **102** | **78** | **24** |

"Family" isn't a tracked scenario field (only `capability`/`risk_category`/`difficulty`
are), so the "actual" column is a manual re-classification of the corpus against
REQUIREMENT.md's family definitions, not a machine-verified count — treat it as
approximate. Remaining scenario work should prioritize Policy coverage/eligibility and
Conflicting data, the two largest gaps.

## Cross-cutting quotas (REQUIREMENT.md §8, bottom)

| Quota | Target min | Actual | Source field |
|---|---:|---:|---|
| Adversarial | 25 | 16 | `difficulty == "adversarial"` |
| Routine | 30 | 9 | `difficulty == "basic"` (undercounts — many `intermediate` scenarios are still routine, not edge) |
| Edge/boundary | 30 | 21 | `difficulty == "intermediate"` (same caveat) |
| Reliability/concurrency/failure | 17 | ~15 | idempotency (8) + inject_failure (2) + missing-entity (2) + concurrency-relevant timing (varies) |
| Authorization or privacy | 10 | 9 | `risk_category == "authorization"` |
| Non-approval-oriented result | 20 | ~24 | deny/investigate/more_information recommendations, escalations, flags, reads |
| Mutation forbidden, evidence still required | 15 | ~10 | adversarial scenarios with a real domain rejection (scope-missing or status-illegal), not just narrative refusal |
| Conflicting evidence | 10 | ~7 | "conflicting data" family scenarios |

Difficulty (`basic`/`intermediate`/`advanced`/`adversarial`) was chosen per-scenario by
authoring judgment, not by a `routine`/`edge` classifier — the mapping above is the best
available proxy, not an exact count. A true accounting needs either new scenario metadata
fields (`routine_edge_class`, `reliability_relevant: bool`, etc.) or a one-time manual
tagging pass. Both are open follow-up work.

## What's solid vs. what's approximate

- **Solid, machine-verified**: capability counts, risk-category counts, difficulty counts,
  total scenario count, suite tier sizes (smoke=10, standard=40, full=78), all scenario
  IDs unique/sorted/hash-matched to their YAML export, all 78 passing 5/5 repetitions
  against real PostgreSQL.
- **Approximate, manually classified**: family mapping, cross-cutting quota mapping. These
  were backfilled from the corpus rather than planned prospectively per REQUIREMENT.md §9's
  intended process, and should be re-validated by a domain reviewer per §14 before this
  role is called production-quality, per REQUIREMENT.md §13.15.
