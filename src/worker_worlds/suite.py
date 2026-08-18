"""Repeated bounded suite execution and deterministic aggregation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from worker_worlds.contracts import (
    RunRecord,
    Scenario,
    ScenarioAggregate,
    SuiteRecord,
    TerminalReason,
    VerdictStatus,
)
from worker_worlds.ids import prefixed_ulid
from worker_worlds.protocols import WorkerAdapter, World
from worker_worlds.runner import Runner

WorldFactory = Callable[[Scenario], World]
WorkerFactory = Callable[[], WorkerAdapter]


def aggregate_scenario(
    scenario: Scenario, runs: list[RunRecord], requested: int
) -> ScenarioAggregate:
    """Aggregate repetitions without discarding any source record."""
    ordered = sorted(runs, key=lambda record: (record.repetition, str(record.id)))
    errors = sum(
        record.incomplete_evidence or record.terminal_reason is TerminalReason.INFRASTRUCTURE_ERROR
        for record in ordered
    )
    passed = sum(record.passed for record in ordered)
    failed = len(ordered) - passed - errors
    verdict_counts = Counter(
        f"{verdict.severity.value}:{verdict.status.value}"
        for record in ordered
        for verdict in record.verdicts
    )
    failure_reasons = Counter(
        verdict.reason_code
        for record in ordered
        for verdict in record.verdicts
        if verdict.status is not VerdictStatus.PASS
    )
    terminal = Counter(record.terminal_reason.value for record in ordered)
    tokens = [record.model_tokens for record in ordered if record.model_tokens is not None]
    costs = [record.cost_minor for record in ordered if record.cost_minor is not None]
    return ScenarioAggregate(
        scenario_id=scenario.id,
        worker=ordered[0].worker if ordered else "unknown",
        worker_version=ordered[0].worker_version if ordered else "unknown",
        requested_repetitions=requested,
        completed_repetitions=len(ordered),
        run_ids=tuple(record.id for record in ordered),
        passed=passed,
        failed=failed,
        errors=errors,
        pass_rate=passed / len(ordered) if ordered else 0,
        verdict_counts=dict(sorted(verdict_counts.items())),
        failure_reasons=dict(sorted(failure_reasons.items())),
        terminal_reasons=dict(sorted(terminal.items())),
        duration_ms=tuple(record.total_duration_ms for record in ordered),
        model_tokens=sum(tokens) if tokens else None,
        cost_minor=sum(costs) if costs else None,
        infrastructure_errors=sum(
            record.terminal_reason is TerminalReason.INFRASTRUCTURE_ERROR for record in ordered
        ),
        insufficient_sample=len(ordered) < 30,
    )


class SuiteRunner:
    """Run scenarios repeatedly with bounded global and provider concurrency."""

    def __init__(
        self,
        runner: Runner,
        *,
        concurrency: int = 4,
        provider_concurrency: int = 2,
    ) -> None:
        """Configure bounded execution limits."""
        if concurrency <= 0 or provider_concurrency <= 0:
            raise ValueError("suite concurrency limits must be positive")
        self._runner = runner
        self._global = asyncio.Semaphore(concurrency)
        self._provider = asyncio.Semaphore(provider_concurrency)

    async def run(
        self,
        name: str,
        scenarios: list[Scenario],
        world_factory: WorldFactory,
        worker_factory: WorkerFactory,
        *,
        repetitions: int = 5,
    ) -> SuiteRecord:
        """Execute every repetition and preserve each RunRecord."""
        if repetitions <= 0:
            raise ValueError("repetitions must be positive")
        started = datetime.now(UTC)

        async def execute(scenario: Scenario, repetition: int) -> RunRecord:
            derived = scenario.model_copy(
                update={
                    "world": scenario.world.model_copy(
                        update={"seed": scenario.world.seed + repetition}
                    )
                }
            )
            async with self._global, self._provider:
                return await self._runner.run(
                    derived,
                    world_factory(derived),
                    worker_factory(),
                    repetition=repetition,
                )

        runs = list(
            await asyncio.gather(
                *(
                    execute(scenario, repetition)
                    for scenario in scenarios
                    for repetition in range(repetitions)
                )
            )
        )
        runs.sort(key=lambda record: (str(record.scenario_id), record.repetition, str(record.id)))
        aggregates = tuple(
            aggregate_scenario(
                scenario,
                [record for record in runs if record.scenario_id == scenario.id],
                repetitions,
            )
            for scenario in sorted(scenarios, key=lambda item: str(item.id))
        )
        worker = runs[0].worker if runs else "unknown"
        worker_version = runs[0].worker_version if runs else "unknown"
        configuration = {
            "name": name,
            "scenario_hashes": [scenario.canonical_hash() for scenario in scenarios],
            "repetitions": repetitions,
        }
        configuration_hash = hashlib.sha256(
            json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return SuiteRecord(
            id=prefixed_ulid("suite"),
            name=name,
            worker=worker,
            worker_version=worker_version,
            world=scenarios[0].world.name if scenarios else "unknown",
            started_at=started,
            ended_at=datetime.now(UTC),
            scenarios=tuple(
                scenario.id for scenario in sorted(scenarios, key=lambda item: str(item.id))
            ),
            aggregates=aggregates,
            runs=tuple(runs),
            configuration_hash=configuration_hash,
        )
