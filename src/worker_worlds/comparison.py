"""Deterministic semantic suite comparison and regression gating."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable

from worker_worlds.contracts import (
    AssertionSeverity,
    ComparisonClassification,
    ComparisonConfig,
    ComparisonReport,
    ComparisonVerdict,
    CompatibilityLevel,
    DistributionSummary,
    FailureModeDelta,
    OutcomeSignature,
    RunRecord,
    ScenarioComparison,
    SuiteRecord,
    Verdict,
    VerdictStatus,
)
from worker_worlds.ids import deterministic_ulid

SIGNATURE_VERSION = "1.0"


def _argument_category(name: str, value: object) -> str:
    if isinstance(value, bool):
        category = str(value).lower()
    elif isinstance(value, int):
        category = "negative" if value < 0 else "zero" if value == 0 else "positive"
    elif isinstance(value, str) and (name in {"currency", "status"} or name.endswith("_id")):
        category = value
    else:
        category = type(value).__name__
    return f"{name}:{category}"


def _major(version: str | None) -> str | None:
    return version.split(".", 1)[0] if version else None


def outcome_signature(record: RunRecord) -> OutcomeSignature:
    """Build an inspectable signature that excludes incidental trace identity."""
    failed = [verdict for verdict in record.verdicts if verdict.status is not VerdictStatus.PASS]
    tools = tuple(turn.tool_call.tool_name for turn in record.turns if turn.tool_call is not None)
    argument_categories = tuple(
        f"{turn.tool_call.tool_name}["
        + ",".join(
            _argument_category(name, value)
            for name, value in sorted(turn.tool_call.arguments.items())
            if name not in {"api_key", "password", "secret", "access_token"}
        )
        + "]"
        for turn in record.turns
        if turn.tool_call is not None
    )
    events = tuple(event.event_type for event in record.events)
    mutation_categories = tuple(sorted({item.split(".", 1)[0] for item in events}))
    changed_entities = tuple(sorted({event.entity.type for event in record.events}))
    authorization = tuple(
        "authorized"
        if event.authorization is not None and bool(event.authorization.scopes)
        else "missing_or_denied"
        for event in record.events
    )
    payload = {
        "version": SIGNATURE_VERSION,
        "passed": record.passed,
        "incomplete_evidence": record.incomplete_evidence,
        "failed_assertions": sorted(verdict.assertion_id for verdict in failed),
        "failed_policies": sorted(
            f"{verdict.policy_name}@{verdict.evaluator_version}"
            for verdict in failed
            if verdict.policy_name
        ),
        "severities": sorted({verdict.severity.value for verdict in failed}),
        "terminal_reason": record.terminal_reason.value,
        "mutation_categories": mutation_categories,
        "changed_entities": changed_entities,
        "event_sequence": events,
        "tool_sequence": tools,
        "tool_argument_categories": argument_categories,
        "authorization_outcomes": authorization,
        "resource_violations": sorted(
            verdict.assertion_id
            for verdict in failed
            if verdict.assertion_type == "resource_within"
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return OutcomeSignature(digest=hashlib.sha256(encoded.encode()).hexdigest(), **payload)


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """Return a 95% Wilson score interval without heavyweight dependencies."""
    if total == 0:
        return 0.0, 1.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    radius = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(runs: Iterable[RunRecord], requested: int, minimum: int) -> DistributionSummary:
    """Summarize outcome variability and operational distributions."""
    records = list(runs)
    signatures = [outcome_signature(record) for record in records]
    outcomes = Counter(item.digest for item in signatures)
    completed = len(records)
    passed = sum(record.passed for record in records)
    errors = sum(record.incomplete_evidence for record in records)
    rate = passed / completed if completed else 0
    low, high = wilson_interval(passed, completed)
    variability = 0 if completed == 0 else 1 - max(outcomes.values(), default=0) / completed
    severities = Counter(
        verdict.severity.value
        for record in records
        for verdict in record.verdicts
        if verdict.status is not VerdictStatus.PASS
    )
    terminals = Counter(record.terminal_reason.value for record in records)
    tools = Counter(
        turn.tool_call.tool_name
        for record in records
        for turn in record.turns
        if turn.tool_call is not None
    )
    mutations = Counter(event.event_type for record in records for event in record.events)
    durations = [record.total_duration_ms for record in records]
    return DistributionSummary(
        requested=requested,
        completed=completed,
        passed=passed,
        failed=completed - passed - errors,
        errors=errors,
        pass_rate=rate,
        wilson_low=low,
        wilson_high=high,
        variability=variability,
        low_sample=completed < minimum,
        outcome_counts=dict(sorted(outcomes.items())),
        severity_counts=dict(sorted(severities.items())),
        terminal_counts=dict(sorted(terminals.items())),
        tool_counts=dict(sorted(tools.items())),
        mutation_counts=dict(sorted(mutations.items())),
        duration_p50_ms=_percentile(durations, 0.5),
        duration_p95_ms=_percentile(durations, 0.95),
        tokens_total=sum(record.model_tokens or 0 for record in records)
        if any(record.model_tokens is not None for record in records)
        else None,
        cost_minor_total=sum(record.cost_minor or 0 for record in records)
        if any(record.cost_minor is not None for record in records)
        else None,
        infrastructure_error_rate=(
            sum(record.terminal_reason.value == "infrastructure_error" for record in records)
            / completed
            if completed
            else 0
        ),
    )


def _failed(record: RunRecord) -> dict[str, Verdict]:
    return {
        f"{verdict.assertion_id}|{verdict.policy_name or ''}": verdict
        for verdict in record.verdicts
        if verdict.status is not VerdictStatus.PASS
    }


def _failure_deltas(
    baseline: list[RunRecord], candidate: list[RunRecord]
) -> tuple[FailureModeDelta, ...]:
    baseline_modes = Counter(key for record in baseline for key in _failed(record))
    candidate_modes = Counter(key for record in candidate for key in _failed(record))
    deltas: list[FailureModeDelta] = []
    for identity in sorted(set(baseline_modes) | set(candidate_modes)):
        before, after = baseline_modes[identity], candidate_modes[identity]
        if before == after:
            continue
        candidate_examples = [record for record in candidate if identity in _failed(record)]
        baseline_examples = [record for record in baseline if identity in _failed(record)]
        examples = candidate_examples or baseline_examples
        verdicts = [_failed(record)[identity] for record in examples]
        rank = {"info": 0, "medium": 1, "high": 2, "critical": 3}
        deltas.append(
            FailureModeDelta(
                kind="policy" if any(verdict.policy_name for verdict in verdicts) else "assertion",
                identity=identity,
                change="new" if before == 0 else "removed" if after == 0 else "rate_changed",
                severity=max(
                    (verdict.severity.value for verdict in verdicts),
                    key=lambda value: rank[value],
                    default=None,
                ),
                baseline_rate=before / len(baseline) if baseline else 0,
                candidate_rate=after / len(candidate) if candidate else 0,
                baseline_run_ids=tuple(record.id for record in baseline_examples[:3]),
                candidate_run_ids=tuple(record.id for record in candidate_examples[:3]),
                verdict_ids=tuple(verdict.id for verdict in verdicts[:3]),
                evidence_refs=tuple(
                    ref for verdict in verdicts[:3] for ref in verdict.evidence_refs[:3]
                ),
                event_ids=tuple(event.id for record in examples[:3] for event in record.events[:3]),
                outcome_signatures=tuple(
                    outcome_signature(record).digest for record in examples[:3]
                ),
                representative_evidence={
                    "events": [
                        event.model_dump(mode="json")
                        for record in examples[:1]
                        for event in record.events[:3]
                    ],
                    "tool_calls": [
                        turn.tool_call.model_dump(mode="json")
                        for record in examples[:1]
                        for turn in record.turns
                        if turn.tool_call is not None
                    ][:3],
                    "initial_snapshot_hash": examples[0].initial_snapshot_hash
                    if examples
                    else None,
                    "final_snapshot_hash": examples[0].final_snapshot_hash if examples else None,
                },
            )
        )
    return tuple(deltas)


def _compatibility(
    baseline: list[RunRecord], candidate: list[RunRecord], config: ComparisonConfig
) -> tuple[CompatibilityLevel, tuple[str, ...]]:
    reasons: list[str] = []
    level = CompatibilityLevel.COMPATIBLE
    for record in (*baseline, *candidate):
        if record.incomplete_evidence:
            reasons.append("required evidence is incomplete")
            level = CompatibilityLevel.INCOMPATIBLE
    world_majors = {_major(record.world_version) for record in (*baseline, *candidate)}
    if len(world_majors) > 1:
        reasons.append("world major versions differ")
        level = CompatibilityLevel.INCOMPATIBLE
    baseline_hashes = {record.scenario_hash for record in baseline if record.scenario_hash}
    candidate_hashes = {record.scenario_hash for record in candidate if record.scenario_hash}
    if baseline_hashes != candidate_hashes:
        reasons.append("scenario content hashes differ")
        if not config.allow_scenario_hash_mismatch:
            level = CompatibilityLevel.INCOMPATIBLE
        elif level is CompatibilityLevel.COMPATIBLE:
            level = CompatibilityLevel.WARNING
    adapters = {record.adapter for record in (*baseline, *candidate)}
    if len(adapters) > 1 and level is CompatibilityLevel.COMPATIBLE:
        reasons.append("adapter identities differ")
        level = CompatibilityLevel.WARNING
    return level, tuple(dict.fromkeys(reasons))


def _effective_config(
    config: ComparisonConfig, scenario_id: str, tags: tuple[str, ...]
) -> ComparisonConfig:
    data = config.model_dump(mode="json")
    for tag in sorted(tags):
        data.update(config.tag_overrides.get(tag, {}))
    data.update(config.scenario_overrides.get(scenario_id, {}))
    return ComparisonConfig.model_validate(data)


def compare_suites(
    baseline: SuiteRecord,
    candidate: SuiteRecord,
    config: ComparisonConfig | None = None,
    *,
    baseline_source: str = "baseline.json",
    candidate_source: str = "candidate.json",
) -> ComparisonReport:
    """Compare immutable suites and apply a deterministic regression gate."""
    selected = config or ComparisonConfig()
    scenario_ids = sorted(set(baseline.scenarios) | set(candidate.scenarios), key=str)
    comparisons: list[ScenarioComparison] = []
    global_reasons: list[str] = []
    all_reasons: list[str] = []
    globally_incompatible = False
    if baseline.world != candidate.world:
        global_reasons.append("world identities differ")
        globally_incompatible = True
    policy_versions = {
        verdict.evaluator_version
        for suite in (baseline, candidate)
        for record in suite.runs
        for verdict in record.verdicts
        if verdict.policy_name
    }
    if len({_major(version) for version in policy_versions}) > 1:
        global_reasons.append("policy major versions differ")
        globally_incompatible = True
    elif len(policy_versions) > 1:
        global_reasons.append("policy minor versions differ")
    assertion_versions = {
        verdict.evaluator_version
        for suite in (baseline, candidate)
        for record in suite.runs
        for verdict in record.verdicts
    }
    if len({_major(version) for version in assertion_versions}) > 1:
        global_reasons.append("assertion evaluator major versions differ")
        globally_incompatible = True
    elif len(assertion_versions) > 1:
        global_reasons.append("assertion evaluator minor versions differ")
    if baseline.worker != candidate.worker:
        global_reasons.append("worker identities differ as an explicit comparison dimension")
    environments = {
        json.dumps(record.environment, sort_keys=True, separators=(",", ":"))
        for suite in (baseline, candidate)
        for record in suite.runs
    }
    if len(environments) > 1:
        global_reasons.append("additive environment metadata differs")
    for scenario_id in scenario_ids:
        before = [record for record in baseline.runs if record.scenario_id == scenario_id]
        after = [record for record in candidate.runs if record.scenario_id == scenario_id]
        tags = tuple(sorted({tag for record in (*before, *after) for tag in record.scenario_tags}))
        effective = _effective_config(selected, str(scenario_id), tags)
        compatibility, reasons = _compatibility(before, after, effective)
        if globally_incompatible:
            compatibility = CompatibilityLevel.INCOMPATIBLE
            reasons = (*reasons, *global_reasons)
        elif global_reasons and compatibility is CompatibilityLevel.COMPATIBLE:
            compatibility = CompatibilityLevel.WARNING
            reasons = (*reasons, *global_reasons)
        if not before or not after:
            compatibility = CompatibilityLevel.INCOMPATIBLE
            reasons = (*reasons, "scenario missing from one suite")
        baseline_aggregate = next(
            (item for item in baseline.aggregates if item.scenario_id == scenario_id), None
        )
        candidate_aggregate = next(
            (item for item in candidate.aggregates if item.scenario_id == scenario_id), None
        )
        bsum = summarize(
            before,
            baseline_aggregate.requested_repetitions if baseline_aggregate else len(before),
            effective.required_minimum_repetitions,
        )
        csum = summarize(
            after,
            candidate_aggregate.requested_repetitions if candidate_aggregate else len(after),
            effective.required_minimum_repetitions,
        )
        delta = csum.pass_rate - bsum.pass_rate
        flake = csum.variability - bsum.variability
        failure_deltas = _failure_deltas(before, after)
        findings: list[ComparisonClassification] = []
        if compatibility is CompatibilityLevel.INCOMPATIBLE:
            findings.append(ComparisonClassification.INCOMPATIBLE)
        elif csum.low_sample or bsum.low_sample:
            findings.append(ComparisonClassification.INSUFFICIENT_EVIDENCE)
        if bsum.pass_rate == 1 and csum.pass_rate < 1:
            findings.append(ComparisonClassification.NEW_FAILURE)
        elif bsum.pass_rate < 1 and csum.pass_rate == 1:
            findings.append(ComparisonClassification.FIXED)
        elif bsum.pass_rate == csum.pass_rate == 1:
            findings.append(ComparisonClassification.UNCHANGED_PASS)
        elif bsum.pass_rate == csum.pass_rate:
            findings.append(ComparisonClassification.UNCHANGED_FAILURE)
        elif failure_deltas:
            findings.append(ComparisonClassification.FAILURE_MODE_CHANGED)
        if flake > effective.maximum_flakiness_increase:
            findings.append(ComparisonClassification.FLAKINESS_INCREASED)
        elif flake < -effective.maximum_flakiness_increase:
            findings.append(ComparisonClassification.FLAKINESS_DECREASED)
        if csum.duration_p95_ms > bsum.duration_p95_ms * (
            1 + effective.maximum_p95_latency_increase
        ):
            findings.append(ComparisonClassification.PERFORMANCE_REGRESSED)
        if (csum.cost_minor_total or 0) > (bsum.cost_minor_total or 0) * (
            1 + effective.maximum_cost_increase
        ) and (csum.cost_minor_total or 0) > 0:
            findings.append(ComparisonClassification.COST_REGRESSED)
        if csum.infrastructure_error_rate > max(
            bsum.infrastructure_error_rate, effective.maximum_infrastructure_error_rate
        ):
            findings.append(ComparisonClassification.INFRASTRUCTURE_REGRESSED)
        primary_order = [
            ComparisonClassification.INCOMPATIBLE,
            ComparisonClassification.NEW_FAILURE,
            ComparisonClassification.INFRASTRUCTURE_REGRESSED,
            ComparisonClassification.FAILURE_MODE_CHANGED,
            ComparisonClassification.FLAKINESS_INCREASED,
            ComparisonClassification.PERFORMANCE_REGRESSED,
            ComparisonClassification.COST_REGRESSED,
            ComparisonClassification.FIXED,
            ComparisonClassification.FLAKINESS_DECREASED,
            ComparisonClassification.INSUFFICIENT_EVIDENCE,
            ComparisonClassification.UNCHANGED_FAILURE,
            ComparisonClassification.UNCHANGED_PASS,
        ]
        unique = tuple(dict.fromkeys(findings))
        primary = next(item for item in primary_order if item in unique)
        comparisons.append(
            ScenarioComparison(
                scenario_id=scenario_id,
                compatibility=compatibility,
                compatibility_reasons=reasons,
                primary_classification=primary,
                findings=unique,
                baseline=bsum,
                candidate=csum,
                pass_rate_delta=delta,
                flakiness_delta=flake,
                failure_deltas=failure_deltas,
                baseline_run_ids=tuple(record.id for record in before),
                candidate_run_ids=tuple(record.id for record in after),
                tags=tags,
            )
        )
        all_reasons.extend(reasons)
    return _report(
        baseline, candidate, selected, comparisons, all_reasons, baseline_source, candidate_source
    )


def _report(
    baseline: SuiteRecord,
    candidate: SuiteRecord,
    config: ComparisonConfig,
    scenarios: list[ScenarioComparison],
    compatibility_reasons: list[str],
    baseline_source: str,
    candidate_source: str,
) -> ComparisonReport:
    incompatible = any(item.compatibility is CompatibilityLevel.INCOMPATIBLE for item in scenarios)
    critical = sum(
        round(delta.candidate_rate * item.candidate.completed)
        if delta.candidate_rate > delta.baseline_rate
        and delta.severity == AssertionSeverity.CRITICAL.value
        else 0
        for item in scenarios
        for delta in item.failure_deltas
        if delta.change in {"new", "rate_changed"}
    )
    high = sum(
        round(delta.candidate_rate * item.candidate.completed)
        if delta.candidate_rate > delta.baseline_rate
        and delta.severity == AssertionSeverity.HIGH.value
        else 0
        for item in scenarios
        for delta in item.failure_deltas
    )
    reasons: list[str] = []
    warnings: list[str] = []
    if incompatible:
        reasons.append("comparison contains incompatible artifacts")
    if critical > config.critical_occurrences_allowed:
        reasons.append(
            "new critical failure modes "
            f"{critical} exceed allowed {config.critical_occurrences_allowed}"
        )
    if high > config.new_high_failures_allowed:
        reasons.append(
            f"new high failure modes {high} exceed allowed {config.new_high_failures_allowed}"
        )
    for item in scenarios:
        effective = _effective_config(config, str(item.scenario_id), item.tags)
        if item.candidate.pass_rate < effective.minimum_candidate_pass_rate:
            reasons.append(f"{item.scenario_id}: candidate pass rate below minimum")
        if item.pass_rate_delta < -effective.maximum_pass_rate_decrease:
            reasons.append(f"{item.scenario_id}: pass-rate decrease exceeds threshold")
        if item.candidate.infrastructure_error_rate > effective.maximum_infrastructure_error_rate:
            reasons.append(f"{item.scenario_id}: infrastructure error rate exceeds threshold")
        if item.flakiness_delta > effective.maximum_flakiness_increase:
            warnings.append(f"{item.scenario_id}: outcome variability increased")
        if (
            "latency" not in effective.excluded_informational_metrics
            and item.candidate.duration_p50_ms
            > item.baseline.duration_p50_ms * (1 + effective.maximum_p50_latency_increase)
        ):
            reasons.append(f"{item.scenario_id}: p50 latency increase exceeds threshold")
        if (
            "latency" not in effective.excluded_informational_metrics
            and item.candidate.duration_p95_ms
            > item.baseline.duration_p95_ms * (1 + effective.maximum_p95_latency_increase)
        ):
            reasons.append(f"{item.scenario_id}: p95 latency increase exceeds threshold")
        baseline_tokens = item.baseline.tokens_total or 0
        candidate_tokens = item.candidate.tokens_total or 0
        if (
            "tokens" not in effective.excluded_informational_metrics
            and candidate_tokens > baseline_tokens * (1 + effective.maximum_token_increase)
        ):
            reasons.append(f"{item.scenario_id}: token increase exceeds threshold")
        baseline_cost = item.baseline.cost_minor_total or 0
        candidate_cost = item.candidate.cost_minor_total or 0
        if (
            "cost" not in effective.excluded_informational_metrics
            and candidate_cost > baseline_cost * (1 + effective.maximum_cost_increase)
        ):
            reasons.append(f"{item.scenario_id}: cost increase exceeds threshold")
        if item.baseline.low_sample or item.candidate.low_sample:
            message = f"{item.scenario_id}: low sample; no strong equivalence claim"
            (reasons if effective.insufficient_samples == "fail" else warnings).append(message)
    overall = (
        CompatibilityLevel.INCOMPATIBLE
        if incompatible
        else CompatibilityLevel.WARNING
        if compatibility_reasons
        else CompatibilityLevel.COMPATIBLE
    )
    baseline_hash, candidate_hash = baseline.canonical_hash(), candidate.canonical_hash()
    identity = hashlib.sha256(
        f"{baseline_hash}:{candidate_hash}:{config.canonical_hash()}".encode()
    ).hexdigest()
    return ComparisonReport(
        id="comparison_" + deterministic_ulid(0, identity),
        created_at=candidate.ended_at,
        baseline_name=baseline.name,
        baseline_hash=baseline_hash,
        candidate_hash=candidate_hash,
        compatibility=overall,
        compatibility_reasons=tuple(dict.fromkeys(compatibility_reasons)),
        config=config,
        scenarios=tuple(scenarios),
        verdict=ComparisonVerdict(
            passed=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            warnings=tuple(dict.fromkeys(warnings)),
            new_critical=critical,
            new_high=high,
        ),
        reproduction_commands=(
            f"worker-worlds compare --baseline {baseline_source} --candidate {candidate_source}",
        ),
        baseline_source=baseline_source,
        candidate_source=candidate_source,
    )
