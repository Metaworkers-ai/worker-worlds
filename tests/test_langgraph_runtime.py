from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from worker_worlds.adapters import LangGraphAdapter
from worker_worlds.contracts import Scenario, TerminalReason
from worker_worlds.errors import AdapterError, ProviderError
from worker_worlds.grading import DeterministicGrader
from worker_worlds.langgraph_runtime import LangGraphRunContext, LangGraphRuntime
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorld


class ToolAwareFakeChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolAwareFakeChatModel:
        del tools, kwargs
        return self


def _runtime(call_id: str) -> LangGraphRuntime:
    model = ToolAwareFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "refund_order",
                        "args": {"order_id": "ord_900", "amount_minor": 1},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
                usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            ),
            AIMessage(
                content="complete",
                usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            ),
        ]
    )
    return LangGraphRuntime(
        lambda tools, _context: create_agent(model, cast(Any, tools)),
        model_provider="openai",
        model_name="fake-chat",
        model_version="1.0.0",
    )


async def test_real_langgraph_fake_model_round_trip(happy_scenario: Scenario) -> None:
    runtime = _runtime("call_langgraph")
    record = await Runner(DeterministicGrader()).run(
        happy_scenario, StubWorld(), LangGraphAdapter(runtime)
    )
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert record.model_tokens == 8
    assert record.tool_call_count == 1
    assert record.turns[0].tool_call is not None
    assert str(record.turns[0].tool_call.id) == "call_langgraph"
    assert len(record.events) == 1
    assert str(record.events[0].request_id) == "call_langgraph"
    terminal = record.turns[-1]
    assert terminal.model_provider == "openai"
    assert terminal.model_name == "fake-chat"
    assert terminal.model_version == "1.0.0"


async def test_actual_graph_multiple_calls_have_stable_evidence_order(
    happy_scenario: Scenario,
) -> None:
    model = ToolAwareFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "refund_order",
                        "args": {"order_id": "ord_900", "amount_minor": 1},
                        "id": "call_first_graph",
                        "type": "tool_call",
                    }
                ],
                id="resp_lookup",
                response_metadata={"request_id": "req_lookup", "retry_count": 1},
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "refund_order",
                        "args": {"order_id": "ord_900", "amount_minor": 1},
                        "id": "call_refund_graph",
                        "type": "tool_call",
                    }
                ],
                id="resp_refund",
                response_metadata={"request_id": "req_refund"},
            ),
            AIMessage(content="complete", id="resp_final"),
        ]
    )
    runtime = LangGraphRuntime(
        lambda tools, _context: create_agent(model, cast(Any, tools)),
        model_provider="openai",
        model_name="fake-chat",
    )
    record = await Runner(DeterministicGrader()).run(
        happy_scenario, StubWorld(), LangGraphAdapter(runtime)
    )
    calls = [turn.tool_call for turn in record.turns if turn.tool_call is not None]
    results = [turn.tool_result for turn in record.turns if turn.tool_result is not None]
    assert [str(call.id) for call in calls] == ["call_first_graph", "call_refund_graph"]
    assert [str(result.call_id) for result in results] == [
        "call_first_graph",
        "call_refund_graph",
    ]
    assert [str(event.request_id) for event in record.events] == [
        "call_first_graph",
        "call_refund_graph",
    ]
    terminal = record.turns[-1]
    assert terminal.provider_response_ids == ("resp_lookup", "resp_refund", "resp_final")
    assert terminal.provider_request_ids == ("req_lookup", "req_refund")
    assert terminal.provider_retry_count == 1


async def test_ten_graph_runs_use_isolated_thread_ids(happy_scenario: Scenario) -> None:
    memory = MemorySaver()
    contexts: list[LangGraphRunContext] = []
    runtimes: list[LangGraphRuntime] = []
    for index in range(10):
        model = ToolAwareFakeChatModel(responses=[AIMessage(content=f"complete-{index}")])

        def factory(
            tools: list[Any],
            context: LangGraphRunContext,
            *,
            graph_model: ToolAwareFakeChatModel = model,
        ) -> object:
            contexts.append(context)
            return create_agent(
                graph_model,
                cast(Any, tools),
                checkpointer=memory,
            )

        runtimes.append(LangGraphRuntime(factory))

    records = await asyncio.gather(
        *(
            Runner(DeterministicGrader()).run(
                happy_scenario, StubWorld(), LangGraphAdapter(runtime)
            )
            for runtime in runtimes
        )
    )
    assert all(record.terminal_reason is TerminalReason.COMPLETED for record in records)
    assert len({runtime.last_thread_id for runtime in runtimes}) == 10
    assert len(contexts) == 10
    for context in contexts:
        checkpoint = memory.get({"configurable": {"thread_id": context.thread_id}})
        assert checkpoint is not None
        messages = checkpoint["channel_values"]["messages"]
        user_messages = [message.content for message in messages if message.type == "human"]
        assert user_messages == [context.scenario.trigger.content]


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class ModelBehaviorError(Exception):
    pass


class FailingGraph:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def ainvoke(self, _state: object, config: object) -> object:
        del config
        raise self.failure


@pytest.mark.parametrize(
    ("failure", "expected", "message"),
    [
        (AuthenticationError("secret"), ProviderError, "authentication failed"),
        (RateLimitError("secret"), ProviderError, "rate limit exceeded"),
        (APIConnectionError("secret"), ProviderError, "connection failed"),
        (ModelBehaviorError("bad output"), AdapterError, "invalid behavior"),
        (TimeoutError("late"), AdapterError, "execution timed out"),
        (LookupError("broken"), AdapterError, r"execution failed \(LookupError\)"),
    ],
)
async def test_langgraph_failures_are_distinct_and_sanitized(
    happy_scenario: Scenario,
    failure: Exception,
    expected: type[Exception],
    message: str,
) -> None:
    runtime = LangGraphRuntime(lambda _tools, _context: FailingGraph(failure))
    with pytest.raises(expected, match=message) as captured:
        await runtime.run_with_tools(happy_scenario, [])
    assert str(failure) not in str(captured.value)
    assert captured.value.__cause__ is None


async def test_graph_construction_and_adapter_contract_failures_are_distinct(
    happy_scenario: Scenario,
) -> None:
    secret = "construction-secret-canary"

    def construction_failure(_tools: list[Any], _context: LangGraphRunContext) -> object:
        raise RuntimeError(secret)

    with pytest.raises(AdapterError, match=r"construction failed \(RuntimeError\)") as error:
        await LangGraphRuntime(construction_failure).run_with_tools(happy_scenario, [])
    assert secret not in str(error.value)
    with pytest.raises(AdapterError, match="async runnable"):
        await LangGraphRuntime(lambda _tools, _context: object()).run_with_tools(happy_scenario, [])


async def test_langgraph_cancel_interrupts_graph_task(happy_scenario: Scenario) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingGraph:
        async def ainvoke(self, _state: object, config: object) -> object:
            del config
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

    runtime = LangGraphRuntime(lambda _tools, _context: BlockingGraph())
    task = asyncio.create_task(runtime.run_with_tools(happy_scenario, []))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await runtime.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_langgraph_runner_timeout_cancels_graph_and_cleans_bridge(
    happy_scenario: Scenario,
) -> None:
    cancelled = asyncio.Event()

    class BlockingGraph:
        async def ainvoke(self, _state: object, config: object) -> object:
            del config
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

    scenario = happy_scenario.model_copy(
        update={"limits": happy_scenario.limits.model_copy(update={"wall_time_s": 0.01})}
    )
    adapter = LangGraphAdapter(LangGraphRuntime(lambda _tools, _context: BlockingGraph()))
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), adapter)
    assert record.terminal_reason is TerminalReason.TIMEOUT
    assert record.cleanup_succeeded
    assert adapter.pending_native_calls == 0
    assert adapter.queued_native_calls == 0
    assert cancelled.is_set()


async def test_langgraph_secret_canary_never_enters_run_evidence(
    happy_scenario: Scenario,
) -> None:
    secret = "langgraph-secret-canary-must-not-appear"
    adapter = LangGraphAdapter(
        LangGraphRuntime(lambda _tools, _context: FailingGraph(AuthenticationError(secret)))
    )
    record = await Runner(DeterministicGrader()).run(happy_scenario, StubWorld(), adapter)
    assert record.terminal_reason is TerminalReason.PROVIDER_ERROR
    assert secret not in record.model_dump_json()
    assert record.error_message == "langgraph provider invocation failed"
