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
    ScheduledInjection,
    TerminalReason,
    ToolResult,
    ToolResultStatus,
    WorkerTurn,
    WorldSnapshot,
)
from worker_worlds.errors import AdapterError, InjectionError, ProviderError, ScenarioAuthoringError
from worker_worlds.ids import prefixed_ulid
from worker_worlds.native_bridge import NativeBridgePendingError
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
            injections = self._scheduled_injections(scenario)
            delivered: set[str] = set()
            await self._deliver_injections(world, injections, delivered, "before_worker")
            if len(delivered) > scenario.limits.injections:
                raise InjectionError("injection budget exceeded")
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
                    injections,
                    delivered,
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
        except InjectionError as exc:
            terminal = (
                TerminalReason.INJECTION_BUDGET_EXCEEDED
                if "budget" in str(exc)
                else TerminalReason.INJECTION_ERROR
            )
            error_type, error_message = type(exc).__name__, str(exc)
            try:
                final = await world.snapshot()
                events = await world.events()
            except Exception as evidence_exc:
                incomplete = True
                error_type, error_message = type(evidence_exc).__name__, str(evidence_exc)
        except (AdapterError, ProviderError, ScenarioAuthoringError) as exc:
            if isinstance(exc, AdapterError):
                terminal = TerminalReason.ADAPTER_ERROR
            elif isinstance(exc, ProviderError):
                terminal = TerminalReason.PROVIDER_ERROR
            else:
                terminal = TerminalReason.SCENARIO_ERROR
            error_type, error_message = type(exc).__name__, str(exc)
            if isinstance(exc, NativeBridgePendingError):
                incomplete = True
                await worker.cancel()
                try:
                    final = await world.snapshot()
                    events = await world.events()
                except Exception as evidence_exc:
                    error_type = type(evidence_exc).__name__
                    error_message = str(evidence_exc)
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
        if bool(getattr(worker, "cancellation_failed", False)):
            incomplete = True
            error_type = "NativeCancellationError"
            error_message = "native runtime cancellation failed"
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
        injections: tuple[ScheduledInjection, ...],
        delivered: set[str],
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
            if bool(getattr(worker, "incomplete_native_evidence", False)):
                raise NativeBridgePendingError(
                    "native provider terminated with pending tool requests"
                )
            if worker.is_terminal(turn):
                await Runner._deliver_injections(world, injections, delivered, "before_terminal")
                if len(delivered) > scenario.limits.injections:
                    raise InjectionError("injection budget exceeded")
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
                if bool(getattr(worker, "continues_after_tool_error", False)):
                    continue
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
            event_types = {event.event_type for event in await world.events()}
            await Runner._deliver_injections(
                world,
                injections,
                delivered,
                "after_tool",
                tool_name=turn.tool_call.tool_name,
                tool_count=tool_calls,
                event_types=event_types,
            )
            if len(delivered) > scenario.limits.injections:
                raise InjectionError("injection budget exceeded")
        await worker.cancel()
        return TerminalReason.BUDGET_EXCEEDED

    @staticmethod
    def _scheduled_injections(scenario: Scenario) -> tuple[ScheduledInjection, ...]:
        raw = scenario.metadata.get("injections", [])
        if not isinstance(raw, list):
            raise InjectionError("scenario metadata injections must be a list")
        try:
            return tuple(ScheduledInjection.model_validate(item) for item in raw)
        except ValueError as exc:
            raise InjectionError(f"invalid injection schedule: {exc}") from exc

    @staticmethod
    async def _deliver_injections(
        world: World,
        injections: tuple[ScheduledInjection, ...],
        delivered: set[str],
        point: str,
        *,
        tool_name: str | None = None,
        tool_count: int = 0,
        event_types: set[str] | None = None,
    ) -> None:
        for injection in injections:
            if injection.id in delivered:
                continue
            matches = (
                injection.trigger == point
                or (
                    point == "after_tool"
                    and injection.trigger == "after_tool"
                    and injection.after_tool == tool_name
                )
                or (
                    point == "after_tool"
                    and injection.trigger == "after_nth_tool"
                    and injection.after_nth_tool == tool_count
                )
                or (
                    point == "after_tool"
                    and injection.trigger == "after_event"
                    and injection.after_event in (event_types or set())
                )
                or (point == "before_worker" and injection.trigger == "at_time")
            )
            if not matches:
                continue
            payload = dict(injection.payload)
            payload["injection_id"] = injection.id
            try:
                if injection.trigger == "at_time" and injection.at is not None:
                    current = await world.snapshot()
                    if injection.at > current.captured_at:
                        await world.advance_time(injection.at - current.captured_at)
                await world.inject(injection.event_type, payload)
            except Exception as exc:
                raise InjectionError(f"injection {injection.id} failed: {exc}") from exc
            delivered.add(injection.id)
