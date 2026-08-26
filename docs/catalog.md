# Domain and role catalog

The checked catalog at `catalog/v1/catalog.json` is the business-facing index over immutable
scenario contracts. It is deliberately separate from `Scenario`, `RunRecord`, and `SuiteRecord`,
so taxonomy changes cannot rewrite historical evidence hashes.

## Domains and roles

- Retail & E-commerce: Customer Support Agent, Refund Specialist, Order Operations Specialist,
  Inventory Controller, Fulfillment Coordinator, Support Escalation Manager, and Supply Chain
  Analyst.
- Insurance: Claims Adjuster.

Every role references explicit capabilities. Every scenario classification contains one domain,
one or more roles, one capability, one bounded difficulty, one risk category, and the immutable
scenario content hash. Smoke, standard, and full suites contain sorted scenario IDs and an explicit
revision. Custom direct-scenario execution remains supported.

Commerce family allocation follows the operation being evaluated: refund/payment cases map to
refunds, order-identity cases to support/order operations, inventory/catalog cases to inventory,
shipment/fulfillment cases to fulfillment, ticket cases to support/escalation, and adversarial cases
to every role whose authority boundary they exercise. Supply-chain and insurance scenarios use
their dedicated deterministic domain models and tools; they are not relabelled refund scenarios.

The live suite surface contains 225 scenarios: 200 generated commerce scenarios, 13 supply-chain
scenarios, and 12 insurance scenarios. Ten additional `examples/scenarios` fixtures remain
classified for compatibility and UI discovery but are excluded from live suites and are permitted
only with the deterministic local stub.

Run `make catalog-check`, `make schemas-check`, and `make openapi-check` before committing a catalog
change. Additive labels, descriptions, classifications, and new versioned IDs preserve schema major
version 1. Reinterpreting an existing ID or changing suite membership requires a suite revision;
breaking a catalog meaning requires a new catalog major version.
