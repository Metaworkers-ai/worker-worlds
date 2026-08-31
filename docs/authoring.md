# Authoring

Start from an exported file in `scenarios/release`. Keep seeds deterministic,
declare risk and expected outcome, and assert state/event evidence rather than
worker prose. New world tools require strict input models, trusted authorization,
transactional state plus event writes, idempotency, rollback tests, and no grading
logic. Policies and assertions must remain pure over immutable evidence. Adapters
translate framework behavior only. Custom worlds implement the public `World`
protocol. Every critical family should kill a purpose-built mutant worker.

Scenarios offered to live adapters must be self-contained. Put every identifier,
amount, currency, reason, and idempotency key required to complete the task in the
worker-visible trigger; metadata is harness control-plane data and is not a substitute
for user-visible inputs. A task that requires a tool interaction must include a
`tool_result_matches` assertion for the reviewed tool name, required argument subset,
typed result status, and expected count. Pair it with state or event assertions so a
wrong call, a typed rejection where success was required, or simple inaction cannot pass.

Supply-chain, Insurance, and Marketing scenarios (`insurance.claims-adjuster.*`,
`insurance.claims-analyst.*`, `marketing.campaign-analyst.*`) don't start from an exported
`scenarios/release` file --
they're generated from `enterprise_scenarios.py` and exported into `scenarios/enterprise/`
via the same `scenario_filename`/YAML export machinery, then hash-checked against the
generator by `tests/test_enterprise_scenarios.py`. Edit the Python generator, not the
YAML directly; a hand-edited YAML file will fail the hash check on the next export. This
family does not yet have purpose-built mutant-worker tests the way the reviewed commerce
matrix does -- that remains open follow-up work.
