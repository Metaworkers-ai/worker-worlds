"""Deterministic Week 3 behavioral-comparison demonstration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from worker_worlds.baselines import create_baseline
from worker_worlds.comparison import compare_suites
from worker_worlds.contracts import (
    AssertionSeverity,
    ComparisonConfig,
    RunId,
    ScenarioId,
    SuiteRecord,
    VerdictStatus,
)
from worker_worlds.reporting import ComparisonReporter


def _write_suite(suite: SuiteRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(suite.canonical_json() + "\n", encoding="utf-8")


def demonstration_suites(source: SuiteRecord) -> tuple[SuiteRecord, SuiteRecord, SuiteRecord]:
    """Create A/B/C suites from real evidence without generated prose semantics."""
    original = source.runs[:5]
    if len(original) < 5:
        raise ValueError("demonstration requires five source repetitions")
    stable_id = ScenarioId("demo.authorization")
    fix_id = ScenarioId("demo.known-failure")
    stable = tuple(
        record.model_copy(
            update={"id": RunId(f"run_demo_a_stable_{index}"), "scenario_id": stable_id}
        )
        for index, record in enumerate(original)
    )
    known_failure = tuple(
        record.model_copy(
            update={
                "id": RunId(f"run_demo_a_failure_{index}"),
                "scenario_id": fix_id,
                "verdicts": (
                    record.verdicts[0].model_copy(
                        update={
                            "assertion_id": "known.ticket.failure",
                            "status": VerdictStatus.FAIL,
                            "severity": AssertionSeverity.HIGH,
                            "message": "known deterministic failure",
                        }
                    ),
                ),
            }
        )
        for index, record in enumerate(original)
    )
    baseline = source.model_copy(
        update={
            "id": "suite_demo_a",
            "name": "baseline-worker-a",
            "scenarios": (stable_id, fix_id),
            "runs": (*stable, *known_failure),
            "aggregates": (),
        }
    )
    first = stable[0]
    failed = first.verdicts[0].model_copy(
        update={
            "assertion_id": "authorization.owner",
            "status": VerdictStatus.FAIL,
            "severity": AssertionSeverity.CRITICAL,
            "message": "candidate wording is excluded from its signature",
        }
    )
    duplicate_turn = first.turns[0].model_copy(update={"id": first.turns[0].id, "index": 1})
    regressed = first.model_copy(
        update={
            "id": RunId("run_demo_b_regression"),
            "verdicts": (failed,),
            "turns": (*first.turns, duplicate_turn),
        }
    )
    candidate_b = baseline.model_copy(
        update={
            "id": "suite_demo_b",
            "name": "candidate-worker-b",
            "worker_version": "2.0-broken",
            "runs": (regressed, *stable[1:], *known_failure),
        }
    )
    fixed = tuple(
        record.model_copy(
            update={
                "id": RunId(f"run_demo_c_fixed_{index}"),
                "verdicts": (
                    original[index]
                    .verdicts[0]
                    .model_copy(update={"message": "harmless wording-only change"}),
                ),
            }
        )
        for index, record in enumerate(known_failure)
    )
    candidate_c = baseline.model_copy(
        update={
            "id": "suite_demo_c",
            "name": "candidate-worker-c",
            "worker_version": "2.0-fixed",
            "runs": (*stable, *fixed),
        }
    )
    return baseline, candidate_b, candidate_c


async def generate_demo(source_path: Path, output: Path) -> tuple[Path, Path]:
    """Generate failing-B and passing/fixed-C comparison artifacts."""
    text = await asyncio.to_thread(source_path.read_text, encoding="utf-8")
    source = SuiteRecord.model_validate_json(text)
    baseline, candidate_b, candidate_c = demonstration_suites(source)
    _write_suite(baseline, output / "baseline-a-suite.json")
    _write_suite(candidate_b, output / "candidate-b-suite.json")
    _write_suite(candidate_c, output / "candidate-c-suite.json")
    baseline_path = create_baseline(
        output / "baseline-a-suite.json", "demo-main", output / "baselines"
    )
    config = ComparisonConfig(required_minimum_repetitions=5)
    report_b = compare_suites(baseline, candidate_b, config)
    report_c = compare_suites(baseline, candidate_c, config)
    await ComparisonReporter().report(report_b, output / "candidate-b-comparison")
    await ComparisonReporter().report(report_c, output / "candidate-c-comparison")
    return baseline_path, output / "candidate-b-comparison/comparison.html"
