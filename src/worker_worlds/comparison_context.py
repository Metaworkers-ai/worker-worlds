"""Context-safe behavioral comparison wrappers."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from worker_worlds.comparison import compare_suites
from worker_worlds.contracts import ComparisonConfig, ComparisonReport, Contract, SuiteRecord
from worker_worlds.evaluation import EvaluationContext


class ContextCompatibility(StrEnum):
    """Compatibility of two immutable evaluation contexts."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"


class ContextualSuiteManifest(Contract):
    """Context sidecar referencing an immutable completed SuiteRecord."""

    suite_record_hash: str
    context: EvaluationContext


class RoleMetrics(Contract):
    """Role-level outcome, behavior, resource, and evidence summary."""

    runs: int
    passed: int
    pass_rate: float
    failures: int
    incomplete_evidence: int
    tool_calls: int
    mutations: int
    duration_ms: int
    model_tokens: int | None
    cost_minor: int | None
    run_ids_by_scenario: dict[str, tuple[str, ...]]


class RoleComparisonSummary(Contract):
    """Comparable role-level aggregates linked to immutable run IDs."""

    baseline: RoleMetrics
    candidate: RoleMetrics
    pass_rate_delta: float
    failure_delta: int
    tool_call_delta: int
    mutation_delta: int
    duration_delta_ms: int
    model_token_delta: int | None
    cost_delta_minor: int | None


class ContextualComparisonRecord(Contract):
    """Comparison report plus explicit business-context compatibility."""

    id: str
    baseline: ContextualSuiteManifest
    candidate: ContextualSuiteManifest
    compatibility: ContextCompatibility
    compatibility_reasons: tuple[str, ...]
    report: ComparisonReport
    role_summary: RoleComparisonSummary
    passed: bool


def _role_metrics(suite: SuiteRecord) -> RoleMetrics:
    runs = len(suite.runs)
    passed = sum(record.passed for record in suite.runs)
    tokens = [record.model_tokens for record in suite.runs]
    costs = [record.cost_minor for record in suite.runs]
    scenarios = sorted({str(record.scenario_id) for record in suite.runs})
    return RoleMetrics(
        runs=runs,
        passed=passed,
        pass_rate=passed / runs if runs else 0,
        failures=runs - passed,
        incomplete_evidence=sum(record.incomplete_evidence for record in suite.runs),
        tool_calls=sum(record.tool_call_count for record in suite.runs),
        mutations=sum(record.mutation_count for record in suite.runs),
        duration_ms=sum(record.total_duration_ms for record in suite.runs),
        model_tokens=sum(item for item in tokens if item is not None)
        if any(item is not None for item in tokens)
        else None,
        cost_minor=sum(item for item in costs if item is not None)
        if any(item is not None for item in costs)
        else None,
        run_ids_by_scenario={
            scenario_id: tuple(
                str(record.id) for record in suite.runs if str(record.scenario_id) == scenario_id
            )
            for scenario_id in scenarios
        },
    )


def context_compatibility(
    baseline: EvaluationContext, candidate: EvaluationContext
) -> tuple[ContextCompatibility, tuple[str, ...]]:
    """Compare every material business and runtime dimension."""
    fields = (
        "catalog_version",
        "domain_id",
        "role_id",
        "suite_id",
        "suite_revision",
        "scenario_ids",
        "scenario_hashes",
        "world_name",
        "world_version",
        "seeds",
        "limits",
    )
    reasons = tuple(
        f"{field.replace('_', ' ')} differs"
        for field in fields
        if getattr(baseline, field) != getattr(candidate, field)
    )
    return (
        ContextCompatibility.INCOMPATIBLE if reasons else ContextCompatibility.COMPATIBLE,
        reasons,
    )


def compare_contextual_suites(
    baseline_suite: SuiteRecord,
    candidate_suite: SuiteRecord,
    baseline_context: EvaluationContext,
    candidate_context: EvaluationContext,
    config: ComparisonConfig | None = None,
) -> ContextualComparisonRecord:
    """Compare suites while ensuring context mismatch can never pass silently."""
    compatibility, reasons = context_compatibility(baseline_context, candidate_context)
    report = compare_suites(baseline_suite, candidate_suite, config)
    baseline_manifest = ContextualSuiteManifest(
        suite_record_hash=baseline_suite.canonical_hash(), context=baseline_context
    )
    candidate_manifest = ContextualSuiteManifest(
        suite_record_hash=candidate_suite.canonical_hash(), context=candidate_context
    )
    identity = hashlib.sha256(
        (
            baseline_manifest.canonical_hash()
            + candidate_manifest.canonical_hash()
            + report.config.canonical_hash()
        ).encode()
    ).hexdigest()
    complete = all(
        not record.incomplete_evidence
        for suite in (baseline_suite, candidate_suite)
        for record in suite.runs
    )
    baseline_metrics = _role_metrics(baseline_suite)
    candidate_metrics = _role_metrics(candidate_suite)
    role_summary = RoleComparisonSummary(
        baseline=baseline_metrics,
        candidate=candidate_metrics,
        pass_rate_delta=candidate_metrics.pass_rate - baseline_metrics.pass_rate,
        failure_delta=candidate_metrics.failures - baseline_metrics.failures,
        tool_call_delta=candidate_metrics.tool_calls - baseline_metrics.tool_calls,
        mutation_delta=candidate_metrics.mutations - baseline_metrics.mutations,
        duration_delta_ms=candidate_metrics.duration_ms - baseline_metrics.duration_ms,
        model_token_delta=(
            candidate_metrics.model_tokens - baseline_metrics.model_tokens
            if candidate_metrics.model_tokens is not None
            and baseline_metrics.model_tokens is not None
            else None
        ),
        cost_delta_minor=(
            candidate_metrics.cost_minor - baseline_metrics.cost_minor
            if candidate_metrics.cost_minor is not None and baseline_metrics.cost_minor is not None
            else None
        ),
    )
    return ContextualComparisonRecord(
        id=f"context-comparison_{identity[:26]}",
        baseline=baseline_manifest,
        candidate=candidate_manifest,
        compatibility=compatibility,
        compatibility_reasons=reasons,
        report=report,
        role_summary=role_summary,
        passed=compatibility is ContextCompatibility.COMPATIBLE
        and report.verdict.passed
        and complete,
    )
