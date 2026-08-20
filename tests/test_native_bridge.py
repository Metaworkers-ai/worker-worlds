from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from worker_worlds.adapters import NativeAdapter, NativeDecision
from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
    RunRecord,
    Scenario,
    TerminalReason,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
)
from worker_worlds.grading import DeterministicGrader
from worker_worlds.native_bridge import (
    NativeBridgeClosedError,
    NativeBridgeError,
    NativeToolBridge,
    NativeToolHandler,
)
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorld


@dataclass
class ScriptedBridgeRuntime:
    calls: list[tuple[str, dict[str, object], str]]
    concurrent: bool = False
    results: list[ToolResult] = field(default_factory=list)
    cancelled: bool = False

    async def run_with_tools(
        self, scenario: Scenario, tools: list[NativeToolHandler]
    ) -> NativeDecision:
        del scenario
        handlers = {handler.spec.name: handler for handler in tools}

        async def invoke(item: tuple[str, dict[str, object], str]) -> ToolResult:
            name, raw_arguments, request_id = item
            arguments = dict(raw_arguments)
            result = await handlers[name].invoke(arguments, request_id)  # type: ignore[arg-type]
            self.results.append(result)
            return result

        if self.concurrent:
            await asyncio.gather(*(invoke(item) for item in self.calls))
        else:
            for item in self.calls:
                await invoke(item)
        return NativeDecision(message="provider complete", terminal=True, model_tokens=3)

    async def cancel(self) -> None:
        self.cancelled = True


class EarlyExitRuntime:
    def __init__(self) -> None:
        self.child: asyncio.Task[ToolResult] | None = None
        self.cancelled = False

    async def run_with_tools(
        self, scenario: Scenario, tools: list[NativeToolHandler]
    ) -> NativeDecision:
        del scenario
        self.child = asyncio.ensure_future(
            tools[0].invoke({"order_id": "ord_900", "amount_minor": 1}, "call_pending")
        )
        await asyncio.sleep(0)
        return NativeDecision(message="incorrect early exit", terminal=True)

    async def cancel(self) -> None:
        self.cancelled = True
        if self.child is not None:
            await asyncio.gather(self.child, return_exceptions=True)


class ExplodingRuntime(EarlyExitRuntime):
    def __init__(self, *, submit: bool = True, cancel_fails: bool = False) -> None:
        super().__init__()
        self.submit = submit
        self.cancel_fails = cancel_fails

    async def run_with_tools(
        self, scenario: Scenario, tools: list[NativeToolHandler]
    ) -> NativeDecision:
        del scenario
        if self.submit:
            self.child = asyncio.ensure_future(
                tools[0].invoke(_refund(), "call_before_provider_error")  # type: ignore[arg-type]
            )
            await asyncio.sleep(0)
        raise RuntimeError("provider exploded with fake-secret-123")

    async def cancel(self) -> None:
        await super().cancel()
        if self.cancel_fails:
            raise RuntimeError("cancel leaked fake-secret-456")


class SlowWorld(StubWorld):
    async def invoke(self, call: ToolCall) -> ToolResult:
        await asyncio.sleep(0.05)
        return await super().invoke(call)


def _refund(amount: object = 1) -> dict[str, object]:
    return {"order_id": "ord_900", "amount_minor": amount}


async def _run(
    scenario: Scenario, runtime: ScriptedBridgeRuntime | EarlyExitRuntime | ExplodingRuntime
) -> RunRecord:
    return await Runner(DeterministicGrader()).run(
        scenario,
        StubWorld(),
        NativeAdapter(runtime),
    )


@pytest.mark.parametrize("count", [1, 2])
async def test_one_and_sequential_calls_round_trip(happy_scenario: Scenario, count: int) -> None:
    runtime = ScriptedBridgeRuntime(
        [("refund_order", _refund(), f"call_sequential_{index}") for index in range(count)]
    )
    record = await _run(happy_scenario, runtime)
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert [str(result.call_id) for result in runtime.results] == [
        f"call_sequential_{index}" for index in range(count)
    ]
    assert len(record.events) == count
    assert record.tool_call_count == count
    assert len([turn for turn in record.turns if turn.tool_result is not None]) == count


async def test_concurrent_calls_have_stable_fifo_order_and_exact_evidence(
    happy_scenario: Scenario,
) -> None:
    runtime = ScriptedBridgeRuntime(
        [
            ("refund_order", _refund(), "call_first"),
            ("refund_order", _refund(2), "call_second"),
        ],
        concurrent=True,
    )
    record = await _run(happy_scenario, runtime)
    calls = [turn.tool_call for turn in record.turns if turn.tool_call is not None]
    results = [turn.tool_result for turn in record.turns if turn.tool_result is not None]
    assert [str(call.id) for call in calls] == ["call_first", "call_second"]
    assert [str(result.call_id) for result in results] == ["call_first", "call_second"]
    assert [str(event.request_id) for event in record.events] == ["call_first", "call_second"]
    assert len({result.started_at for result in results}) == 2


async def test_validation_failure_returns_to_provider_and_loop_continues(
    happy_scenario: Scenario,
) -> None:
    runtime = ScriptedBridgeRuntime([("refund_order", _refund("invalid"), "call_invalid")])
    record = await _run(happy_scenario, runtime)
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert runtime.results[0].status is ToolResultStatus.ERROR
    assert runtime.results[0].error_type == "InvalidToolArguments"
    assert not record.events


async def test_model_cannot_supply_authorization_and_rejection_does_not_mutate(
    happy_scenario: Scenario,
) -> None:
    scenario = happy_scenario.model_copy(
        update={
            "trigger": happy_scenario.trigger.model_copy(
                update={"actor": {"customer_id": "cus_intruder"}}
            )
        }
    )
    arguments = _refund()
    arguments["authorization"] = {"customer_id": "cus_102", "scopes": ["refund:own_order"]}
    runtime = ScriptedBridgeRuntime([("refund_order", arguments, "call_unauthorized")])
    record = await _run(scenario, runtime)
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert runtime.results[0].error_type == "AuthorizationDenied"
    assert not record.events


async def test_provider_exit_with_pending_call_is_incomplete_and_cannot_pass(
    happy_scenario: Scenario,
) -> None:
    runtime = EarlyExitRuntime()
    record = await _run(happy_scenario, runtime)
    assert record.terminal_reason is TerminalReason.ADAPTER_ERROR
    assert record.incomplete_evidence
    assert not record.passed
    assert runtime.cancelled
    assert runtime.child is not None and runtime.child.done()


async def test_provider_exception_with_callback_is_drained_and_sanitized(
    happy_scenario: Scenario,
) -> None:
    runtime = ExplodingRuntime()
    adapter = NativeAdapter(runtime)
    record = await Runner(DeterministicGrader()).run(happy_scenario, StubWorld(), adapter)
    assert record.terminal_reason is TerminalReason.ADAPTER_ERROR
    assert record.incomplete_evidence and not record.passed
    assert record.error_type == "NativeBridgePendingError"
    assert "fake-secret" not in (record.error_message or "")
    assert runtime.cancelled
    assert runtime.child is not None and runtime.child.done()
    assert adapter.pending_native_calls == 0
    assert adapter.queued_native_calls == 0


async def test_provider_exception_without_pending_is_sanitized_not_incomplete(
    happy_scenario: Scenario,
) -> None:
    runtime = ExplodingRuntime(submit=False)
    record = await _run(happy_scenario, runtime)
    assert record.terminal_reason is TerminalReason.ADAPTER_ERROR
    assert not record.incomplete_evidence
    assert record.error_message == "native worker invocation failed (RuntimeError)"
    assert "fake-secret" not in repr(record)


async def test_duplicate_ids_mismatched_results_and_idempotent_close() -> None:
    bridge = NativeToolBridge(
        [ToolSpec(name="read", description="read", input_schema={})],
        AuthorizationContext(actor_id="test"),
    )
    first = asyncio.create_task(bridge.submit("read", {}, request_id="call_same"))
    call = await bridge.next_call()
    with pytest.raises(NativeBridgeError, match="duplicate native request id"):
        await bridge.submit("read", {}, request_id="call_same")
    now = datetime.now(UTC)
    mismatched = ToolResult(
        call_id=CallId("call_other"),
        status=ToolResultStatus.SUCCESS,
        started_at=now,
        ended_at=now,
    )
    with pytest.raises(NativeBridgeError, match="no pending"):
        bridge.resolve(mismatched)
    bridge.resolve(mismatched.model_copy(update={"call_id": call.id}))
    assert await first == mismatched.model_copy(update={"call_id": call.id})
    with pytest.raises(NativeBridgeError, match="no pending"):
        bridge.resolve(mismatched.model_copy(update={"call_id": call.id}))
    await bridge.close()
    await bridge.close()
    with pytest.raises(NativeBridgeClosedError):
        await bridge.submit("read", {})


async def test_unknown_tool_and_queued_close_are_safe() -> None:
    bridge = NativeToolBridge(
        [ToolSpec(name="read", description="read", input_schema={})],
        AuthorizationContext(actor_id="test"),
    )
    with pytest.raises(NativeBridgeError, match="unknown tool"):
        await bridge.submit("delete", {})
    queued = asyncio.create_task(bridge.submit("read", {}, request_id="call_queued"))
    await asyncio.sleep(0)
    assert bridge.queued_count == 1
    await bridge.close()
    with pytest.raises(NativeBridgeClosedError):
        await queued
    assert bridge.pending_count == 0 and bridge.queued_count == 0


async def test_cancellation_fails_callback_and_leaves_no_pending() -> None:
    bridge = NativeToolBridge(
        [ToolSpec(name="read", description="read", input_schema={})],
        AuthorizationContext(actor_id="test"),
    )
    pending = asyncio.create_task(bridge.submit("read", {}))
    await bridge.next_call()
    await bridge.close()
    with pytest.raises(NativeBridgeClosedError):
        await pending
    assert bridge.pending_count == 0
    assert bridge.queued_count == 0


async def test_bridge_tool_timeout_cancels_provider(happy_scenario: Scenario) -> None:
    scenario = happy_scenario.model_copy(
        update={"limits": happy_scenario.limits.model_copy(update={"tool_timeout_s": 0.001})}
    )
    runtime = ScriptedBridgeRuntime([("refund_order", _refund(), "call_timeout")])
    record = await Runner(DeterministicGrader()).run(
        scenario,
        SlowWorld(),
        NativeAdapter(runtime),
    )
    assert record.terminal_reason is TerminalReason.TOOL_TIMEOUT
    assert runtime.cancelled


async def test_bridge_budget_cancels_before_world_mutation(happy_scenario: Scenario) -> None:
    scenario = happy_scenario.model_copy(
        update={"limits": happy_scenario.limits.model_copy(update={"tool_calls": 0})}
    )
    runtime = ScriptedBridgeRuntime([("refund_order", _refund(), "call_over_budget")])
    record = await _run(scenario, runtime)
    assert record.terminal_reason is TerminalReason.BUDGET_EXCEEDED
    assert runtime.cancelled
    assert not record.events


async def test_external_runner_cancellation_drains_bridge(happy_scenario: Scenario) -> None:
    runtime = ScriptedBridgeRuntime([("refund_order", _refund(), "call_cancelled")])
    world = SlowWorld()
    task = asyncio.create_task(
        Runner(DeterministicGrader()).run(
            happy_scenario,
            world,
            NativeAdapter(runtime),
        )
    )
    await asyncio.sleep(0.001)
    task.cancel()
    record = await task
    assert record.terminal_reason is TerminalReason.CANCELLED
    assert runtime.cancelled
    assert world.closed


async def test_runtime_cancel_failure_is_sanitized_and_cleanup_completes(
    happy_scenario: Scenario,
) -> None:
    scenario = happy_scenario.model_copy(
        update={"limits": happy_scenario.limits.model_copy(update={"tool_calls": 0})}
    )
    runtime = ExplodingRuntime(cancel_fails=True)
    adapter = NativeAdapter(runtime)
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), adapter)
    assert record.incomplete_evidence and not record.passed
    assert record.error_type == "NativeCancellationError"
    assert record.error_message == "native runtime cancellation failed"
    assert "fake-secret" not in repr(record)
    assert runtime.child is not None and runtime.child.done()
    assert adapter.pending_native_calls == 0
