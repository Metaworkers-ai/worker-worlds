# Worker Worlds v1 release-candidate technical report

## Problem and method

Response-only tests miss consequential state transitions. Worker Worlds executes
deterministic local example workers in isolated seeded enterprise worlds, retains
tool/state/event evidence, grades eleven assertion primitives and ten policy rules,
and compares semantic outcome distributions.

## Scope and findings

The implemented worlds cover commerce customers, orders, products, inventory, refunds, shipments,
tickets, email, and escalation plus supply-chain and insurance claims workflows. The live corpus is
225 YAML scenarios: 200 commerce, 13 supply-chain, and 12 insurance. Ten legacy demonstration
fixtures are stub-only and excluded from live suites, bringing the total classified scenario count
to 235. Deterministic fake/local LangGraph and OpenAI Agents SDK paths are covered by
the release gate. One explicitly authorized pre-hardening OpenAI insurance run passed 6 of 12 and
revealed prompts that allowed clarification or inaction; it is not a current release result. All 224
live prompts and assertions were subsequently hardened, and a paid post-hardening replay remains
pending explicit authorization. The purpose-built unauthorized-refund
candidate is expected to produce a new critical signature and fail regardless of
sample size. Exact measured performance is recorded in `artifacts/` after final
validation; no broader performance or statistical claim is made.

## Reproduction and caveats

Run `make verify`, the commands in `docs/quickstart.md`, and the demonstration
tests. World fidelity is bounded by modeled commerce semantics. Provider queues
and model variance are excluded. Core does not sandbox processes or networks, and
operators must supply those controls when worker code is untrusted. All fixtures
are synthetic; no production customer data is used.
