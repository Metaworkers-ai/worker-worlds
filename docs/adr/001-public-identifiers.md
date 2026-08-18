# ADR 001: ULID public identifiers

Status: accepted, 2026-08-18.

Worker Worlds uses canonical 26-character uppercase ULIDs for newly generated
run, event, call, turn, verdict, and commerce entity identifiers. ULIDs sort by
creation time, remain portable JSON strings, and require no Python runtime
upgrade. Deterministic fixtures derive the 80-bit randomness component from
world version, seed, and entity purpose. Existing v1 string identifiers remain
readable; this is an additive generation policy, not a schema-major change.
