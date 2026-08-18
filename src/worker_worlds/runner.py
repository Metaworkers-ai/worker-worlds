"""Framework-neutral async run lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime

from worker_worlds.contracts import (
    AuthorizationContext,
    RunId,
    RunRecord,
    Scenario,
    TerminalReason,
    ToolResult,
    ToolResultStatus,
    WorkerTurn,
    WorldSnapshot,
)
from worker_worlds.errors import AdapterError, ProviderError, ScenarioAuthoringError
from worker_worlds.ids import prefixed_ulid
from worker_worlds.protocols import Grader, WorkerAdapter, World


class Runner:
    """Own lifecycle, deadlines, cancellation, and evidence assembly."""

    def __init__(self, grader: Grader) -> None:
        """Create a runner using a framework-neutral grader."""
        self._grader = grader

    async def run(
        self,
        scenario: Scenario,
        world: World,
        worker: WorkerAdapter,
        *,
        repetition: int = 0,
    ) -> RunRecord:
        """Execute one isolated scenario and always attempt resource cleanup."""
        run_id = RunId(prefixed_ulid("run"))
        started = datetime.now(UTC)
        initial = None
        final = None
        turns: list[WorkerTurn] = []
        events = []
        terminal = TerminalReason.INFRASTRUCTURE_ERROR
        incomplete = False
        error_type: str | None = None
        error_message: str | None = None
        cleanup_succeeded = False
        try:
            initial = await world.reset(seed=scenario.world.seed, run_id=run_id)
            context = AuthorizationContext(
                actor_id=worker.name,
                customer_id=str(scenario.trigger.actor.get("customer_id", "")) or None,
                scopes=frozenset({"refund:own_order"}),
            )
            tools = await world.tools(context)
            await worker.start(scenario, tools)
            terminal = await asyncio.wait_for(
                self._execute(
                    run_id,
                    scenario,
                    world,
                    worker,
                    turns,
                    {tool.name for tool in tools if tool.mutation},
                ),
                timeout=scenario.limits.wall_time_s,
            )
            final = await world.snapshot()
            events = await world.events()
        except TimeoutError as exc:
            terminal = TerminalReason.TIMEOUT
            error_type, error_message = type(exc).__name__, "worker exceeded wall-time budget"
            await worker.cancel()
            try:
                final = await world.snapshot()
                events = await world.events()
            except Exception as evidence_exc:  # provenance is recorded below
                incomplete = True
                error_type = type(evidence_exc).__name__
                error_message = str(evidence_exc)
        except asyncio.CancelledError as exc:
            terminal = TerminalReason.CANCELLED
            error_type, error_message = type(exc).__name__, "run was cancelled"
            await worker.cancel()
            try:
                final = await world.snapshot()
                events = await world.events()
            except Exception as evidence_exc:
                incomplete = True
                error_type, error_message = type(evidence_exc).__name__, str(evidence_exc)
        except (AdapterError, ProviderError, ScenarioAuthoringError) as exc:
            terminal = {
                AdapterError: TerminalReason.ADAPTER_ERROR,
                ProviderError: TerminalReason.PROVIDER_ERROR,
                ScenarioAuthoringError: TerminalReason.SCENARIO_ERROR,
            }[type(exc)]
            error_type, error_message = type(exc).__name__, str(exc)
        except Exception as exc:  # runner converts unexpected boundary failures into evidence
            terminal = TerminalReason.INFRASTRUCTURE_ERROR
            incomplete = True
            error_type, error_message = type(exc).__name__, str(exc)
        finally:
            try:
                await world.close()
                cleanup_succeeded = bool(getattr(world, "cleanup_succeeded", True))
            except Exception as cleanup_exc:
                incomplete = True
                cleanup_succeeded = False
                error_type, error_message = type(cleanup_exc).__name__, str(cleanup_exc)
        ended = datetime.now(UTC)
        tool_results = [turn.tool_result for turn in turns if turn.tool_result is not None]
        token_values = [turn.model_tokens for turn in turns if turn.model_tokens is not None]
        cost_values = [turn.cost_minor for turn in turns if turn.cost_minor is not None]
        initial_hash = self._snapshot_hash(initial) if initial is not None else None
        final_hash = self._snapshot_hash(final) if final is not None else None
        record = RunRecord(
            id=run_id,
            scenario_id=scenario.id,
            worker=worker.name,
            worker_version=worker.worker_version,
            adapter=worker.name,
            repetition=repetition,
            seed=scenario.world.seed,
            started_at=started,
            ended_at=ended,
            terminal_reason=terminal,
            initial_snapshot=initial,
            final_snapshot=final,
            turns=tuple(turns),
            events=tuple(events),
            verdicts=(),
            incomplete_evidence=incomplete,
            error_type=error_type,
            error_message=error_message,
            initial_snapshot_hash=initial_hash,
            final_snapshot_hash=final_hash,
            tool_duration_ms=sum(result.duration_ms for result in tool_results),
            total_duration_ms=max(0, int((ended - started).total_seconds() * 1000)),
            tool_call_count=sum(turn.tool_call is not None for turn in turns),
            mutation_count=len(events),
            model_tokens=sum(token_values) if token_values else None,
            cost_minor=sum(cost_values) if cost_values else None,
            environment={"python": platform.python_version(), "platform": platform.system()},
            dependency_versions={
                name: importlib.metadata.version(name) for name in ("pydantic", "PyYAML")
            },
            world_version=world.version,
            migration_version=str(getattr(world, "migration_version", "none")),
            cleanup_succeeded=cleanup_succeeded,
            scenario_trigger=scenario.trigger.model_dump(mode="json"),
            scenario_tags=scenario.tags,
            scenario_hash=scenario.canonical_hash(),
        )
        verdicts = await self._grader.grade(scenario, record)
        return record.model_copy(update={"verdicts": tuple(verdicts)})

    @staticmethod
    def _snapshot_hash(snapshot: WorldSnapshot) -> str:
        text = json.dumps(snapshot.state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode()).hexdigest()

    @staticmethod
    async def _execute(
        run_id: RunId,
        scenario: Scenario,
        world: World,
        worker: WorkerAdapter,
        turns: list[WorkerTurn],
        mutation_tools: set[str],
    ) -> TerminalReason:
        tool_result: ToolResult | None = None
        tool_calls = 0
        mutations = 0
        model_tokens = 0
        cost_minor = 0
        for _ in range(scenario.limits.worker_turns):
            turn = await worker.next_turn(tool_result)
            model_tokens += turn.model_tokens or 0
            cost_minor += turn.cost_minor or 0
            if model_tokens > scenario.limits.model_tokens or (
                scenario.limits.cost_minor > 0 and cost_minor > scenario.limits.cost_minor
            ):
                await worker.cancel()
                turns.append(turn)
                return TerminalReason.BUDGET_EXCEEDED
            if turn.tool_call is not None:
                call = turn.tool_call.model_copy(update={"run_id": run_id})
                turn = turn.model_copy(update={"tool_call": call})
            turns.append(turn)
            if worker.is_terminal(turn):
                return TerminalReason.COMPLETED
            if turn.tool_call is None:
                return TerminalReason.WORKER_ERROR
            tool_calls += 1  # noqa: SIM113 - only tool-bearing turns consume this budget
            if tool_calls > scenario.limits.tool_calls:
                await worker.cancel()
                return TerminalReason.BUDGET_EXCEEDED
            if turn.tool_call.tool_name in mutation_tools:
                if mutations >= scenario.limits.mutations:
                    await worker.cancel()
                    return TerminalReason.BUDGET_EXCEEDED
                mutations += 1
            try:
                tool_result = await asyncio.wait_for(
                    world.invoke(turn.tool_call), timeout=scenario.limits.tool_timeout_s
                )
            except TimeoutError:
                await worker.cancel()
                return TerminalReason.TOOL_TIMEOUT
            if tool_result.status is ToolResultStatus.ERROR:
                turns.append(
                    WorkerTurn(
                        id=turn.id,
                        index=turn.index + 1,
                        occurred_at=tool_result.ended_at,
                        tool_result=tool_result,
                        terminal=True,
                    )
                )
                production_tool = turn.tool_call.tool_name == "issue_refund"
                if production_tool and tool_result.error_type == "ToolValidationError":
                    return TerminalReason.TOOL_VALIDATION_ERROR
                if production_tool and tool_result.error_type == "AuthorizationDenied":
                    return TerminalReason.AUTHORIZATION_REJECTION
                if production_tool and tool_result.error_type == "ToolExecutionError":
                    return TerminalReason.TOOL_EXECUTION_ERROR
                return TerminalReason.TOOL_ERROR
        await worker.cancel()
        return TerminalReason.BUDGET_EXCEEDED
