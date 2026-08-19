# Worker Worlds v1 release-candidate technical report

## Problem and method

Response-only tests miss consequential state transitions. Worker Worlds executes
deterministic local example workers in isolated seeded commerce worlds, retains
tool/state/event evidence, grades ten assertion primitives and ten policy rules,
and compares semantic outcome distributions.

## Scope and findings

The world covers customers, orders, products, inventory, refunds, shipments,
tickets, email, escalation, messy facts, controlled time, and specialized release
mutations. The release corpus is 200 YAML scenarios across seven risk families.
Only deterministic fake/local LangGraph and OpenAI Agents SDK paths were tested;
no provider model and no paid API was used. The purpose-built unauthorized-refund
candidate is expected to produce a new critical signature and fail regardless of
sample size. Exact measured performance is recorded in `artifacts/` after final
validation; no broader performance or statistical claim is made.

## Reproduction and caveats

Run `make verify`, the commands in `docs/quickstart.md`, and the demonstration
tests. World fidelity is bounded by modeled commerce semantics. Provider queues
and model variance are excluded. Core does not sandbox processes or networks, and
operators must supply those controls when worker code is untrusted. All fixtures
are synthetic; no production customer data is used.
