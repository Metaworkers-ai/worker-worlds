from __future__ import annotations

from pathlib import Path

import pytest

from worker_worlds.baselines import create_baseline, load_baseline
from worker_worlds.comparison import compare_suites, outcome_signature, wilson_interval
from worker_worlds.contracts import (
    AssertionSeverity,
    ComparisonClassification,
    ComparisonConfig,
    RunId,
    RunRecord,
    Scenario,
    SuiteRecord,
    TerminalReason,
    VerdictStatus,
)
from worker_worlds.grading import DeterministicGrader
from worker_worlds.reporting import ComparisonReporter
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite import aggregate_scenario


async def _run(happy_scenario: Scenario) -> RunRecord:
    return await Runner(DeterministicGrader()).run(happy_scenario, StubWorld(), StubWorkerAdapter())


def _suite(scenario: Scenario, run: RunRecord, *, count: int = 5) -> SuiteRecord:
    records = tuple(
        run.model_copy(update={"id": RunId(f"run_{index}"), "repetition": index})
        for index in range(count)
    )
    return SuiteRecord(
        id="suite_test",
        name="test",
        worker="stub",
        worker_version="1.0",
        world="stub-commerce",
        started_at=records[0].started_at,
        ended_at=records[-1].ended_at,
        scenarios=(scenario.id,),
        aggregates=(aggregate_scenario(scenario, list(records), count),),
        runs=records,
        configuration_hash="config",
    )


async def test_outcome_signature_ignores_wording_and_identity(happy_scenario: Scenario) -> None:
    record = await _run(happy_scenario)
    signature = outcome_signature(record)
    changed = record.model_copy(
        update={
            "id": RunId("different"),
            "verdicts": tuple(
                verdict.model_copy(update={"message": "different generated wording"})
                for verdict in record.verdicts
            ),
        }
    )
    assert outcome_signature(changed).digest == signature.digest
    different_event = record.events[0].model_copy(update={"event_type": "refund.duplicate_issued"})
    assert (
        outcome_signature(record.model_copy(update={"events": (different_event,)})).digest
        != signature.digest
    )


async def test_critical_regression_bypasses_low_sample(happy_scenario: Scenario) -> None:
    run = await _run(happy_scenario)
    baseline = _suite(happy_scenario, run)
    failed_verdict = run.verdicts[0].model_copy(update={"status": VerdictStatus.FAIL})
    failed = run.model_copy(update={"id": RunId("run_failed"), "verdicts": (failed_verdict,)})
    candidate = baseline.model_copy(update={"runs": (*baseline.runs[:-1], failed)})
    report = compare_suites(
        baseline,
        candidate,
        ComparisonConfig(required_minimum_repetitions=30),
    )
    scenario = report.scenarios[0]
    assert scenario.primary_classification is ComparisonClassification.NEW_FAILURE
    assert report.verdict.new_critical == 1
    assert not report.verdict.passed
    assert scenario.failure_deltas[0].candidate_run_ids == (RunId("run_failed"),)
    assert ComparisonClassification.FLAKINESS_INCREASED in scenario.findings


async def test_fixed_and_unchanged_behavior(happy_scenario: Scenario) -> None:
    run = await _run(happy_scenario)
    passing = _suite(happy_scenario, run)
    failed_verdict = run.verdicts[0].model_copy(update={"status": VerdictStatus.FAIL})
    failed_run = run.model_copy(update={"verdicts": (failed_verdict,)})
    failing = _suite(happy_scenario, failed_run)
    fixed = compare_suites(failing, passing)
    assert fixed.scenarios[0].primary_classification is ComparisonClassification.FIXED
    unchanged = compare_suites(passing, passing)
    assert unchanged.scenarios[0].primary_classification is ComparisonClassification.UNCHANGED_PASS


async def test_baseline_hash_and_all_report_formats(
    happy_scenario: Scenario, tmp_path: Path
) -> None:
    run = await _run(happy_scenario)
    suite = _suite(happy_scenario, run)
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(suite.canonical_json(), encoding="utf-8")
    baseline_path = create_baseline(suite_path, "main", tmp_path / "baselines")
    baseline = load_baseline(baseline_path)
    assert baseline.suite_hash == suite.canonical_hash()
    report = compare_suites(baseline.suite, suite)
    paths = await ComparisonReporter(split_threshold_bytes=1).report(report, tmp_path / "report")
    assert all(path.exists() for path in paths)
    filename = ComparisonReporter._scenario_filename(happy_scenario.id)
    assert (tmp_path / "report/scenarios" / filename).exists()
    assert "http://" not in paths[2].read_text(encoding="utf-8")


def test_wilson_interval_is_bounded() -> None:
    low, high = wilson_interval(4, 5)
    assert 0 <= low < 0.8 < high <= 1


def test_altered_baseline_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="unable to load baseline"):
        load_baseline(path)


async def test_incompatible_scenario_content_is_not_compared_silently(
    happy_scenario: Scenario,
) -> None:
    run = await _run(happy_scenario)
    baseline = _suite(happy_scenario, run)
    candidate = baseline.model_copy(
        update={
            "runs": tuple(
                record.model_copy(update={"scenario_hash": "materially-different"})
                for record in baseline.runs
            )
        }
    )
    report = compare_suites(baseline, candidate)
    assert report.scenarios[0].primary_classification is ComparisonClassification.INCOMPATIBLE
    assert not report.verdict.passed


async def test_tag_override_changes_gate_deterministically(happy_scenario: Scenario) -> None:
    run = await _run(happy_scenario)
    tagged = run.model_copy(update={"scenario_tags": ("lenient",)})
    baseline = _suite(happy_scenario, tagged)
    high = run.verdicts[0].model_copy(
        update={"status": VerdictStatus.FAIL, "severity": AssertionSeverity.HIGH}
    )
    failed = tagged.model_copy(update={"id": RunId("run_high"), "verdicts": (high,)})
    candidate = baseline.model_copy(update={"runs": (*baseline.runs[:-1], failed)})
    config = ComparisonConfig(
        new_high_failures_allowed=1,
        tag_overrides={"lenient": {"maximum_pass_rate_decrease": 0.25}},
    )
    first = compare_suites(baseline, candidate, config)
    second = compare_suites(baseline, candidate, config)
    assert first.verdict.passed
    assert first.canonical_json() == second.canonical_json()


async def test_operational_regression_findings(happy_scenario: Scenario) -> None:
    run = await _run(happy_scenario)
    baseline = _suite(happy_scenario, run)
    slower = tuple(
        record.model_copy(
            update={
                "total_duration_ms": 1000,
                "cost_minor": 100,
                "terminal_reason": (
                    TerminalReason.INFRASTRUCTURE_ERROR if index == 0 else record.terminal_reason
                ),
            }
        )
        for index, record in enumerate(baseline.runs)
    )
    candidate = baseline.model_copy(update={"runs": slower})
    report = compare_suites(
        baseline,
        candidate,
        ComparisonConfig(maximum_infrastructure_error_rate=0.1),
    )
    findings = report.scenarios[0].findings
    assert ComparisonClassification.PERFORMANCE_REGRESSED in findings
    assert ComparisonClassification.COST_REGRESSED in findings
    assert ComparisonClassification.INFRASTRUCTURE_REGRESSED in findings
