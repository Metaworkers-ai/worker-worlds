# ADR 004: deterministic grading and derived reports

Status: accepted, 2026-08-18.

RunRecord snapshots and event history remain the source of truth. Assertion
evaluators and commerce policies are pure deterministic functions that return
bounded evidence references rather than copying traces into verdicts. Invalid
assertions and missing required evidence produce error verdicts. Core policies
never use an LLM.

SuiteRecord preserves every RunRecord and adds only deterministic aggregates.
Canonical JSON is the machine source; JUnit and static HTML are derived formats
that cannot reinterpret verdicts. Reporter-controlled output is structurally
redacted and HTML-escaped. Five repetitions are explicitly marked as
insufficient samples and never imply statistical significance.
