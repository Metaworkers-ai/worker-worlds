# Domain and role catalog

The checked catalog at `catalog/v1/catalog.json` is the business-facing index over immutable
scenario contracts. It is deliberately separate from `Scenario`, `RunRecord`, and `SuiteRecord`,
so taxonomy changes cannot rewrite historical evidence hashes.

## Domains and roles

- Retail & E-commerce: Customer Support Agent, Refund Specialist, Order Operations Specialist,
  Inventory Controller, Fulfillment Coordinator, Support Escalation Manager, and Supply Chain
  Analyst.
- Insurance: Claims Adjuster, Insurance Claims Analyst.

Every role references explicit capabilities. Every scenario classification contains one domain,
one or more roles, one capability, one bounded difficulty, one risk category, and the immutable
scenario content hash. Smoke, standard, and full suites contain sorted scenario IDs and an explicit
revision. Custom direct-scenario execution remains supported.

Commerce family allocation follows the operation being evaluated: refund/payment cases map to
refunds, order-identity cases to support/order operations, inventory/catalog cases to inventory,
shipment/fulfillment cases to fulfillment, ticket cases to support/escalation, and adversarial cases
to every role whose authority boundary they exercise. Supply-chain and insurance scenarios use
their dedicated deterministic domain models and tools; they are not relabelled refund scenarios.

Scenario classification covers 235 scenarios in total: 200 reviewed commerce scenarios under
`scenarios/release`, 25 enterprise scenarios under `scenarios/enterprise` (13 supply-chain and 12
insurance), and 10 `examples/scenarios` demonstration fixtures. Of these, 225 are live-ready (the
200 commerce plus 25 enterprise scenarios); the 10 demonstration fixtures remain classified for
compatibility and UI discovery but are excluded from live suites and are permitted only with the
deterministic local stub. The full-tier suites reflect this exactly: Supply Chain Analyst Full
contains all 13 supply-chain scenarios, and Claims Adjuster Full contains all 12 insurance
scenarios.

Run `make catalog-check`, `make schemas-check`, and `make openapi-check` before committing a catalog
change. Additive labels, descriptions, classifications, and new versioned IDs preserve schema major
version 1. Reinterpreting an existing ID or changing suite membership requires a suite revision;
breaking a catalog meaning requires a new catalog major version.

## Test-only fault injection

`GetStockoutRiskInput.inject_delay_ms` (supply-chain `get_stockout_risk` tool) is intentional
test-only fault injection, not a production feature: a stub-driven scenario can set it to force
that tool call to stall so the runner's tool-timeout and cancellation path can be exercised
deterministically. It is hard-capped at 60 seconds and defaults to `0`. The one scenario that uses
it, `commerce.supply-chain.timeout-check`, is explicitly `live_ready: false` and only ever runs
against the deterministic local stub. No scenario approved for live adapters asks a worker to set
this field, and the 60-second cap bounds the worst case even if a live worker did.
