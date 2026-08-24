from __future__ import annotations

from datetime import UTC, datetime

import pytest

from worker_worlds.adapters import (
    DeterministicFakeRuntime,
    LangGraphAdapter,
    NativeAdapter,
    NativeDecision,
    OpenAIAgentsAdapter,
)
from worker_worlds.contracts import Scenario, ToolResult, ToolResultStatus, ToolSpec
from worker_worlds.protocols import WorkerAdapter


def _runtime() -> DeterministicFakeRuntime:
    return DeterministicFakeRuntime(
        [
            NativeDecision(
                tool_name="get_order", arguments={"order_id": "ord_900"}, model_tokens=3
            ),
            NativeDecision(
                tool_name="issue_refund",
                arguments={
                    "order_id": "ord_900",
                    "amount_minor": 1,
                    "currency": "USD",
                    "idempotency_key": "k",
                },
                model_tokens=4,
            ),
            NativeDecision(message="done", terminal=True, model_tokens=2, cost_minor=1),
        ]
    )


@pytest.mark.parametrize("adapter_type", [LangGraphAdapter, OpenAIAgentsAdapter])
async def test_native_adapter_conformance(
    adapter_type: type[NativeAdapter], happy_scenario: Scenario
) -> None:
    runtime = _runtime()
    adapter = adapter_type(runtime)
    tools = [
        ToolSpec(name="get_order", description="get", input_schema={"type": "object"}),
        ToolSpec(
            name="issue_refund",
            description="refund",
            input_schema={"type": "object"},
            mutation=True,
        ),
    ]
    await adapter.start(happy_scenario, tools)
    assert isinstance(adapter, WorkerAdapter)
    assert [item["name"] for item in adapter.native_tools()] == ["get_order", "issue_refund"]
    first = await adapter.next_turn(None)
    assert first.tool_call is not None and first.tool_call.tool_name == "get_order"
    now = datetime.now(UTC)
    result = ToolResult(
        call_id=first.tool_call.id,
        status=ToolResultStatus.SUCCESS,
        output={},
        started_at=now,
        ended_at=now,
    )
    second = await adapter.next_turn(result)
    assert second.tool_result == result
    assert second.tool_call is not None and second.tool_call.tool_name == "issue_refund"
    terminal = await adapter.next_turn(result)
    assert adapter.is_terminal(terminal)
    assert terminal.model_tokens == 2 and terminal.cost_minor == 1
    assert isinstance(terminal.model_dump(mode="json"), dict)
    await adapter.cancel()
    assert runtime.cancelled


def test_optional_adapter_dependency_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(Exception, match="worker-worlds\\[langgraph\\]"):
        LangGraphAdapter()


async def test_legacy_runtime_exception_diagnostic_is_sanitized(
    happy_scenario: Scenario,
) -> None:
    class ExplodingLegacyRuntime:
        async def decide(self, scenario, tools, tool_result, turn_index):  # type: ignore[no-untyped-def]
            del scenario, tools, tool_result, turn_index
            raise RuntimeError("provider returned fake-secret-legacy")

        async def cancel(self) -> None:
            return None

    adapter = NativeAdapter(ExplodingLegacyRuntime())
    await adapter.start(
        happy_scenario,
        [ToolSpec(name="get_order", description="get", input_schema={})],
    )
    with pytest.raises(Exception) as error:
        await adapter.next_turn(None)
    assert str(error.value) == "native worker invocation failed (RuntimeError)"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
