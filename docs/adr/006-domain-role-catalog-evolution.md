# ADR 006: Additive domain and role catalog evolution

Status: accepted

Worker Worlds keeps schema major version 1 and treats existing `Scenario`, `RunRecord`, and
`SuiteRecord` bytes as frozen evidence. Domain, role, capability, suite, and evaluation-context
contracts are additive records with their own semantic versions. Scenario classification and suite
membership live in a checked external catalog, so catalog changes do not alter scenario hashes.

Catalog IDs are stable lowercase dotted or hyphenated identifiers. Catalog collections are sorted,
unknown fields are rejected, and all references are validated within one domain. A catalog revision
may add definitions or publish a new suite revision without changing schema major 1. Removing or
reinterpreting a published identifier requires a new catalog major version.

Evaluation provenance is stored in sidecar manifests that reference canonical run and suite hashes.
It is not injected into legacy evidence models because defaulted fields would change hashes after
older records are parsed and reserialized.
