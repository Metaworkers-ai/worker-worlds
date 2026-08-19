# Authoring

Start from an exported file in `scenarios/release`. Keep seeds deterministic,
declare risk and expected outcome, and assert state/event evidence rather than
worker prose. New world tools require strict input models, trusted authorization,
transactional state plus event writes, idempotency, rollback tests, and no grading
logic. Policies and assertions must remain pure over immutable evidence. Adapters
translate framework behavior only. Custom worlds implement the public `World`
protocol. Every critical family should kill a purpose-built mutant worker.
