from __future__ import annotations

from worker_worlds.comparison_context import (
    ContextCompatibility,
    compare_contextual_suites,
    context_compatibility,
)
from worker_worlds.contracts import RunId, RunRecord, Scenario, SuiteRecord
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
