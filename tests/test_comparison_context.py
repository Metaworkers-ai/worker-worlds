from __future__ import annotations

from worker_worlds.comparison_context import (
    ContextCompatibility,
    compare_contextual_suites,
    context_compatibility,
)
from worker_worlds.contracts import RunId, RunRecord, Scenario, SuiteRecord, VerdictStatus
from worker_worlds.evaluation import EvaluationContext
from worker_worlds.grading import DeterministicGrader
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite import aggregate_scenario


async def _suite(scenario: Scenario) -> SuiteRecord:
    run = await Runner(DeterministicGrader()).run(scenario, StubWorld(), StubWorkerAdapter())
    records: tuple[RunRecord, ...] = tuple(
        run.model_copy(update={"id": RunId(f"run_context_{index}"), "repetition": index})
        for index in range(5)
    )
    return SuiteRecord(
        id="suite_context",
        name="context test",
        worker="stub",
        worker_version="1.0",
        world="stub-commerce",
        started_at=records[0].started_at,
        ended_at=records[-1].ended_at,
        scenarios=(scenario.id,),
        aggregates=(aggregate_scenario(scenario, list(records), 5),),
        runs=records,
        configuration_hash="context-config",
    )


def _context(scenario: Scenario) -> EvaluationContext:
    return EvaluationContext(
        catalog_version="1.0.0",
        domain_id="commerce",
        role_id="refund-specialist",
        suite_id="commerce.refund-specialist.smoke",
        suite_revision="1.0.0",
        scenario_ids=(scenario.id,),
        scenario_hashes={str(scenario.id): scenario.canonical_hash()},
        agent_id="local-stub",
        agent_version="1.0.0",
        world_name="stub",
        world_version="1.0",
        seeds=(scenario.world.seed,),
        limits=scenario.limits,
    )


async def test_contextual_comparison_is_deterministic(happy_scenario: Scenario) -> None:
    suite = await _suite(happy_scenario)
    context = _context(happy_scenario)
    first = compare_contextual_suites(suite, suite, context, context)
    second = compare_contextual_suites(suite, suite, context, context)
    assert first.canonical_json() == second.canonical_json()
    assert first.compatibility is ContextCompatibility.COMPATIBLE
    assert first.passed


async def test_suite_revision_mismatch_cannot_pass(happy_scenario: Scenario) -> None:
    suite = await _suite(happy_scenario)
    baseline = _context(happy_scenario)
    candidate = baseline.model_copy(update={"suite_revision": "1.1.0"})
    compatibility, reasons = context_compatibility(baseline, candidate)
    result = compare_contextual_suites(suite, suite, baseline, candidate)
    assert compatibility is ContextCompatibility.INCOMPATIBLE
    assert reasons == ("suite revision differs",)
    assert not result.passed


async def test_incomplete_evidence_cannot_pass_context_gate(happy_scenario: Scenario) -> None:
    suite = await _suite(happy_scenario)
    incomplete_run = suite.runs[0].model_copy(
        update={"cleanup_succeeded": False, "incomplete_evidence": True}
    )
    incomplete = suite.model_copy(update={"runs": (incomplete_run, *suite.runs[1:])})
    context = _context(happy_scenario)
    result = compare_contextual_suites(incomplete, incomplete, context, context)
    assert not result.passed


async def test_new_critical_failure_cannot_pass_context_gate(happy_scenario: Scenario) -> None:
    suite = await _suite(happy_scenario)
    context = _context(happy_scenario)
    failed_verdict = suite.runs[0].verdicts[0].model_copy(update={"status": VerdictStatus.FAIL})
    failed_run = suite.runs[0].model_copy(
        update={"id": RunId("run_context_critical"), "verdicts": (failed_verdict,)}
    )
    candidate = suite.model_copy(update={"runs": (failed_run, *suite.runs[1:])})
    result = compare_contextual_suites(suite, candidate, context, context)
    assert result.compatibility is ContextCompatibility.COMPATIBLE
    assert result.compatibility_reasons == ()
    assert result.report.verdict.new_critical == 1
    assert not result.report.verdict.passed
    assert result.passed is False


def _insurance_context(scenario: Scenario, role_id: str, suite_id: str) -> EvaluationContext:
    return EvaluationContext(
        catalog_version="1.0.0",
        domain_id="insurance",
        role_id=role_id,
        suite_id=suite_id,
        suite_revision="1.0.0",
        scenario_ids=(scenario.id,),
        scenario_hashes={str(scenario.id): scenario.canonical_hash()},
        agent_id="local-stub",
        agent_version="1.0.0",
        world_name="insurance",
        world_version="1.0",
        seeds=(scenario.world.seed,),
        limits=scenario.limits,
    )


async def test_claims_analyst_and_claims_adjuster_results_are_incompatible(
    happy_scenario: Scenario,
) -> None:
    """A Claims Analyst run can never be silently compared against a Claims Adjuster run."""
    suite = await _suite(happy_scenario)
    baseline = _insurance_context(
        happy_scenario, "claims-adjuster", "insurance.claims-adjuster.smoke"
    )
    candidate = _insurance_context(
        happy_scenario, "claims-analyst", "insurance.claims-analyst.smoke"
    )
    compatibility, reasons = context_compatibility(baseline, candidate)
    result = compare_contextual_suites(suite, suite, baseline, candidate)
    assert compatibility is ContextCompatibility.INCOMPATIBLE
    assert "role id differs" in reasons
    assert "suite id differs" in reasons
    assert not result.passed


async def test_claims_analyst_matching_context_compares_cleanly(happy_scenario: Scenario) -> None:
    """Two Claims Analyst runs with the same evaluation context compare compatibly."""
    suite = await _suite(happy_scenario)
    context = _insurance_context(happy_scenario, "claims-analyst", "insurance.claims-analyst.smoke")
    result = compare_contextual_suites(suite, suite, context, context)
    assert result.compatibility is ContextCompatibility.COMPATIBLE
    assert result.compatibility_reasons == ()
    assert result.passed


def _marketing_context(scenario: Scenario, role_id: str, suite_id: str) -> EvaluationContext:
    return EvaluationContext(
        catalog_version="1.0.0",
        domain_id="marketing",
        role_id=role_id,
        suite_id=suite_id,
        suite_revision="1.0.0",
        scenario_ids=(scenario.id,),
        scenario_hashes={str(scenario.id): scenario.canonical_hash()},
        agent_id="local-stub",
        agent_version="1.0.0",
        world_name="marketing",
        world_version="1.0",
        seeds=(scenario.world.seed,),
        limits=scenario.limits,
    )


async def test_campaign_analyst_and_claims_analyst_results_are_incompatible(
    happy_scenario: Scenario,
) -> None:
    """A Campaign Analyst run can never be silently compared against a Claims Analyst run."""
    suite = await _suite(happy_scenario)
    baseline = _insurance_context(
        happy_scenario, "claims-analyst", "insurance.claims-analyst.smoke"
    )
    candidate = _marketing_context(
        happy_scenario, "campaign-analyst", "marketing.campaign-analyst.smoke"
    )
    compatibility, reasons = context_compatibility(baseline, candidate)
    result = compare_contextual_suites(suite, suite, baseline, candidate)
    assert compatibility is ContextCompatibility.INCOMPATIBLE
    assert "domain id differs" in reasons
    assert "role id differs" in reasons
    assert "suite id differs" in reasons
    assert not result.passed


async def test_campaign_analyst_matching_context_compares_cleanly(
    happy_scenario: Scenario,
) -> None:
    """Two Campaign Analyst runs with the same evaluation context compare compatibly."""
    suite = await _suite(happy_scenario)
    context = _marketing_context(
        happy_scenario, "campaign-analyst", "marketing.campaign-analyst.smoke"
    )
    result = compare_contextual_suites(suite, suite, context, context)
    assert result.compatibility is ContextCompatibility.COMPATIBLE
    assert result.compatibility_reasons == ()
    assert result.passed
