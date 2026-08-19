# ADR 005: Behavioral comparison and policy identity

## Decision

Behavioral comparisons operate on immutable `SuiteRecord` evidence and use inspectable,
versioned semantic outcome signatures. Generated wording, trace identifiers, timestamps,
filesystem paths, and secrets are excluded. Event and tool ordering remains significant.

Each policy has a stable registry name, semantic version, implementation hash, and required
evidence version. A policy semantic-version difference is a compatibility warning when the
required evidence remains compatible; a policy major-version or required-evidence mismatch is
incompatible. Tenant-specific policy configuration may eventually supply parameters at the
policy boundary, but may not replace trusted authorization or host executable policy code.

Five repetitions are a smoke-test distribution, not proof of equivalence. Reports include Wilson
intervals and low-sample markers. Critical regressions bypass statistical and practical-significance
thresholds.
