"""Durable suite execution over framework-neutral worlds and workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from worker_worlds.contracts import Limits, RunRecord, Scenario, SuiteRecord, TerminalReason
from worker_worlds.grading import DeterministicGrader
from worker_worlds.protocols import WorkerAdapter, World
from worker_worlds.reporting import HtmlReporter, JsonReporter, JUnitReporter, SuiteJsonReporter
from worker_worlds.runner import Runner
from worker_worlds.scenario_identity import scenario_content_hash
from worker_worlds.suite import aggregate_scenario
from worker_worlds.suite_jobs import (
    PostgresSuiteJobRepository,
    SuiteBudget,
    SuiteJobRecord,
    SuiteScenarioStatus,
)

WorldFactory = Callable[[Scenario], World]
AsyncWorkerFactory = Callable[[], Awaitable[WorkerAdapter]]
_LOGGER = logging.getLogger(__name__)


class SuiteBudgetExceeded(RuntimeError):
    """A configured aggregate suite budget was exhausted."""


def _bundle_evidence(output: Path, manifest: str) -> None:
    """Write deterministic manifest and ZIP bytes for one suite job."""
    manifest_path = output / "manifest.json"
    descriptor, temporary_manifest_name = tempfile.mkstemp(
        prefix=".manifest-", suffix=".json", dir=output
    )
    temporary_manifest = Path(temporary_manifest_name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(manifest + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary_manifest.replace(manifest_path)
    bundle = output / "evidence.zip"
    descriptor, temporary_bundle_name = tempfile.mkstemp(
        prefix=".evidence-", suffix=".zip", dir=output
    )
    os.close(descriptor)
    temporary_bundle = Path(temporary_bundle_name)
    with zipfile.ZipFile(temporary_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in output.rglob("*") if item.is_file() and item != bundle):
            if path in {temporary_manifest, temporary_bundle} or path.name.startswith(".evidence-"):
                continue
            info = zipfile.ZipInfo(str(path.relative_to(output)))
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    temporary_bundle.replace(bundle)


class DurableSuiteService:
    """Execute persisted suite jobs with bounded concurrency and cancellation."""

    def __init__(
        self,
        repository: PostgresSuiteJobRepository,
        scenarios: dict[str, Scenario],
        world_factory: WorldFactory,
        worker_factory: AsyncWorkerFactory,
        artifact_directory: Path,
        *,
        concurrency: int = 4,
    ) -> None:
        """Bind a durable job to pure runtime factories."""
        if concurrency <= 0 or concurrency > 32:
            raise ValueError("suite concurrency must be between 1 and 32")
        self._repository = repository
        self._scenarios = scenarios
        self._world_factory = world_factory
        self._worker_factory = worker_factory
        self._artifact_directory = artifact_directory
        self._semaphore = asyncio.Semaphore(concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, job_id: str) -> None:
        """Schedule a job once in this process; PostgreSQL prevents double claim."""
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._execute(job_id))

    def is_active(self, job_id: str) -> bool:
        """Return whether this process still owns a live task for the job."""
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    async def cancel(self, job_id: str) -> SuiteJobRecord:
        """Persist cancellation before propagating it to active work."""
        job = await self._repository.request_cancel(job_id)
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        if job.status.value == "cancelled":
            return await self.ensure_terminal_evidence(job)
        return job

    async def ensure_terminal_evidence(self, job: SuiteJobRecord) -> SuiteJobRecord:
        """Create complete evidence for a terminal job, including zero-run cancellation."""
        records = await self._existing_records(job)
        suite_path = await self._write_suite(job, records)
        relative_path = str(suite_path.relative_to(self._artifact_directory))
        published_job = job.model_copy(update={"suite_record_path": relative_path})
        await self._finalize_evidence(published_job, suite_path)
        terminal = await self._repository.attach_evidence(job.id, relative_path)
        if terminal.suite_record_path != relative_path:
            return terminal
        return terminal

    @staticmethod
    def _expected_scenario_hash(job: SuiteJobRecord, scenario_id: str) -> str | None:
        context = job.configuration.get("evaluation_context")
        if not isinstance(context, dict):
            return None
        hashes = context.get("scenario_hashes")
        if not isinstance(hashes, dict):
            return None
        value = hashes.get(scenario_id)
        return value if isinstance(value, str) else None

    async def wait(self, job_id: str) -> SuiteJobRecord:
        """Wait for a locally active job and return its persisted terminal state."""
        task = self._tasks.get(job_id)
        if task is not None:
            await task
        return await self._repository.get(job_id)

    async def _execute(self, job_id: str) -> None:
        if not await self._repository.claim(job_id):
            return
        execution_task = asyncio.current_task()

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self._repository.heartbeat_seconds)
                try:
                    owned = await self._repository.heartbeat(job_id)
                except Exception as exc:
                    _LOGGER.warning("suite executor heartbeat failed (%s)", type(exc).__name__)
                    owned = False
                if not owned:
                    if execution_task is not None:
                        execution_task.cancel()
                    return

        heartbeat_task = asyncio.create_task(heartbeat())
        records: list[RunRecord] = []
        try:
            job = await self._repository.get(job_id)
            records = await self._existing_records(job)
            raw_budget = job.configuration.get("suite_budget")
            budget = SuiteBudget.model_validate(raw_budget) if raw_budget is not None else None
            if budget is not None and len(job.scenarios) > budget.scenarios:
                raise SuiteBudgetExceeded("suite scenario budget exceeded")
            usage = {
                "tool_calls": sum(record.tool_call_count for record in records),
                "model_tokens": sum(record.model_tokens or 0 for record in records),
                "mutations": sum(record.mutation_count for record in records),
                "cost_minor": sum(record.cost_minor or 0 for record in records),
            }
            usage_lock = asyncio.Lock()

            async def account(record: RunRecord) -> None:
                async with usage_lock:
                    usage["tool_calls"] += record.tool_call_count
                    usage["model_tokens"] += record.model_tokens or 0
                    usage["mutations"] += record.mutation_count
                    usage["cost_minor"] += record.cost_minor or 0
                    if budget is None:
                        return
                    for field in ("tool_calls", "model_tokens", "mutations"):
                        if usage[field] > getattr(budget, field):
                            raise SuiteBudgetExceeded(f"suite {field} budget exceeded")
                    if budget.cost_minor > 0 and usage["cost_minor"] > budget.cost_minor:
                        raise SuiteBudgetExceeded("suite cost budget exceeded")

            async def run_scenario(scenario_id: str) -> None:
                scenario = self._scenarios.get(scenario_id)
                if scenario is None:
                    raise ValueError(f"suite scenario is unavailable: {scenario_id}")
                configured_seed = job.configuration.get("seed_override")
                if configured_seed is not None:
                    if not isinstance(configured_seed, int) or isinstance(configured_seed, bool):
                        raise ValueError("suite seed override must be an integer")
                    scenario = scenario.model_copy(
                        update={
                            "world": scenario.world.model_copy(update={"seed": configured_seed})
                        }
                    )
                configured_limits = job.configuration.get("limits_override")
                if configured_limits is not None:
                    scenario = scenario.model_copy(
                        update={"limits": Limits.model_validate(configured_limits)}
                    )
                expected_hash = self._expected_scenario_hash(job, str(scenario.id))
                if expected_hash is not None and scenario_content_hash(scenario) != expected_hash:
                    raise ValueError(
                        f"suite scenario hash does not match reviewed context: {scenario.id}"
                    )
                async with self._semaphore:
                    if not await self._repository.scenario_started(job_id, scenario.id):
                        return
                    configured_retries = job.configuration.get("infrastructure_retries", 1)
                    if not isinstance(configured_retries, int) or isinstance(
                        configured_retries, bool
                    ):
                        raise ValueError("suite infrastructure retries must be an integer")
                    retries = min(3, max(0, configured_retries))
                    record: RunRecord | None = None
                    for attempt in range(retries + 1):
                        worker = await self._worker_factory()
                        produced = await Runner(DeterministicGrader()).run(
                            scenario, self._world_factory(scenario), worker
                        )
                        run_directory = (
                            self._artifact_directory
                            / job_id
                            / "attempts"
                            / self._repository.executor_id
                        )
                        reporter = JsonReporter(run_directory)
                        await reporter.report(produced)
                        if reporter.output_path is None:
                            raise RuntimeError("run reporter did not publish evidence")
                        record = RunRecord.model_validate_json(
                            reporter.output_path.read_text(encoding="utf-8")
                        )
                        relative_path = str(
                            reporter.output_path.relative_to(self._artifact_directory / job_id)
                        )
                        registered = await self._repository.register_run_evidence(
                            job_id,
                            scenario.id,
                            record.id,
                            record.canonical_hash(),
                            relative_path,
                        )
                        if not registered:
                            raise RuntimeError("suite executor lease ownership was lost")
                        records.append(record)
                        await account(record)
                        retryable = (
                            record.incomplete_evidence
                            and record.terminal_reason is TerminalReason.INFRASTRUCTURE_ERROR
                            and record.error_type == "InfrastructureError"
                        )
                        if not retryable or attempt == retries:
                            break
                        if not await self._repository.scenario_retrying(job_id, scenario.id):
                            raise RuntimeError("suite executor lease ownership was lost")
                    if record is None:
                        raise RuntimeError("suite scenario produced no run evidence")
                    status = (
                        SuiteScenarioStatus.PASSED
                        if record.passed
                        else SuiteScenarioStatus.ERROR
                        if record.incomplete_evidence
                        or record.terminal_reason is TerminalReason.INFRASTRUCTURE_ERROR
                        else SuiteScenarioStatus.FAILED
                    )
                    persisted = await self._repository.scenario_finished(
                        job_id,
                        scenario.id,
                        status=status,
                        run_id=record.id,
                        record_hash=record.canonical_hash(),
                        terminal_reason=record.terminal_reason.value,
                        error_type=record.error_type,
                        error_message=record.error_message,
                    )
                    if not persisted:
                        raise RuntimeError("suite executor lease ownership was lost")

            tasks = [
                asyncio.create_task(run_scenario(str(item.scenario_id))) for item in job.scenarios
            ]
            try:
                gathered = asyncio.gather(*tasks)
                if budget is None:
                    await gathered
                else:
                    try:
                        await asyncio.wait_for(asyncio.shield(gathered), timeout=budget.deadline_s)
                    except TimeoutError as exc:
                        gathered.cancel()
                        await asyncio.gather(gathered, return_exceptions=True)
                        raise SuiteBudgetExceeded("suite deadline budget exceeded") from exc
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            current = await self._repository.get(job_id)
            if current.cancel_requested:
                terminal = await self._repository.finish(job_id)
                await self.ensure_terminal_evidence(terminal)
                return
            terminal = await self._repository.finish(job_id)
            await self.ensure_terminal_evidence(terminal)
        except asyncio.CancelledError:
            current = await self._repository.get(job_id)
            if not current.cancel_requested:
                return
            terminal = await self._repository.finish(job_id)
            await self.ensure_terminal_evidence(terminal)
        except Exception as exc:
            current = await self._repository.get(job_id)
            if current.status.value in {"cancelled", "completed", "failed"}:
                _LOGGER.warning(
                    "terminal suite evidence publication deferred (%s)", type(exc).__name__
                )
            else:
                terminal = await self._repository.finish(job_id, error=exc)
                await self.ensure_terminal_evidence(terminal)
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            self._tasks.pop(job_id, None)

    async def _write_suite(self, job: SuiteJobRecord, records: list[RunRecord]) -> Path:
        suite = self._suite_record(job, records)
        output = self._artifact_directory / job.id / "publications" / self._repository.executor_id
        suite_path = await SuiteJsonReporter().report(suite, output)
        await JUnitReporter().report(suite, output)
        await HtmlReporter().report(suite, output)
        return suite_path

    async def _finalize_evidence(self, job: SuiteJobRecord, suite_path: Path) -> None:
        """Bundle the exact persisted suite bytes and terminal database job snapshot."""
        suite_text = await asyncio.to_thread(suite_path.read_text, encoding="utf-8")
        persisted = SuiteRecord.model_validate_json(suite_text)
        manifest = json.dumps(
            {
                "schema_version": "1.0",
                "job": job.model_dump(mode="json"),
                "suite_record_hash": persisted.canonical_hash(),
                "run_record_hashes": {
                    str(record.id): record.canonical_hash() for record in persisted.runs
                },
                "evaluation_context": job.configuration.get("evaluation_context"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        await asyncio.to_thread(_bundle_evidence, suite_path.parent, manifest)

    async def _existing_records(self, job: SuiteJobRecord) -> list[RunRecord]:
        """Recover only attempt files whose hashes were fenced into PostgreSQL."""
        directory = (self._artifact_directory / job.id).resolve()
        records: list[RunRecord] = []
        seen: set[str] = set()
        for run_id, expected_hash, relative_path in await self._repository.run_evidence(job.id):
            path = (directory / relative_path).resolve()
            if directory not in path.parents or not path.is_file() or path.is_symlink():
                raise RuntimeError(f"stored suite run evidence path is invalid: {run_id}")
            try:
                record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                raise RuntimeError(f"stored suite run evidence is invalid: {path.name}") from exc
            if str(record.id) != run_id or record.canonical_hash() != expected_hash:
                raise RuntimeError(f"stored suite run evidence hash mismatch: {path.name}")
            context_hash = self._expected_scenario_hash(job, str(record.scenario_id))
            if context_hash is not None and record.scenario_hash != context_hash:
                raise RuntimeError(f"stored suite scenario hash mismatch: {path.name}")
            if run_id not in seen:
                records.append(record)
                seen.add(run_id)
        return records

    def _suite_record(self, job: SuiteJobRecord, records: list[RunRecord]) -> SuiteRecord:
        ordered = sorted(records, key=lambda item: (str(item.scenario_id), str(item.id)))
        scenario_by_id = {str(record.scenario_id) for record in ordered}
        scenarios = tuple(
            item.scenario_id for item in job.scenarios if str(item.scenario_id) in scenario_by_id
        )
        aggregates = tuple(
            aggregate_scenario(
                next(
                    scenario for scenario in self._scenarios.values() if scenario.id == scenario_id
                ),
                [record for record in ordered if record.scenario_id == scenario_id],
                len([record for record in ordered if record.scenario_id == scenario_id]),
            )
            for scenario_id in scenarios
        )
        configuration_hash = hashlib.sha256(
            json.dumps(job.configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return SuiteRecord(
            id=job.id,
            name=job.suite_id,
            worker=ordered[0].worker if ordered else job.agent_id,
            worker_version=ordered[0].worker_version if ordered else "unknown",
            world=job.world,
            started_at=job.started_at or job.created_at,
            ended_at=datetime.now(UTC),
            scenarios=scenarios,
            aggregates=aggregates,
            runs=tuple(ordered),
            configuration_hash=configuration_hash,
        )
