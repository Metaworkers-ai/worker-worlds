from __future__ import annotations

import asyncio

import pytest
from agents import Agent
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.usage import Usage

from worker_worlds.adapters import OpenAIAgentsAdapter
from worker_worlds.contracts import Scenario, TerminalReason, ToolResultStatus
from worker_worlds.errors import AdapterError, ProviderError
from worker_worlds.grading import DeterministicGrader
from worker_worlds.openai_agents_runtime import OpenAIAgentsRuntime
from worker_worlds.openai_testing import (
    ModelStep,
    ScriptedModel,
    assistant_message,
    function_call,
)
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorld


async def test_real_sdk_fake_model_round_trips_tool_and_usage(
    happy_scenario: Scenario,
) -> None:
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "refund_order",
                        {"order_id": "ord_900", "amount_minor": 1},
                        call_id="call_openai_sdk",
                    )
                ],
                usage=Usage(requests=2, input_tokens=3, output_tokens=2, total_tokens=5),
                response_id="resp_tool",
                request_id="req_tool",
            ),
            ModelStep(
                output=[assistant_message("refund complete")],
                usage=Usage(requests=1, input_tokens=3, output_tokens=2, total_tokens=5),
                response_id="resp_final",
                request_id="req_final",
            ),
        ],
    )
    agent = Agent(name="test", instructions="Use tools", model=model)
    record = await Runner(DeterministicGrader()).run(
        happy_scenario,
        StubWorld(),
        OpenAIAgentsAdapter(OpenAIAgentsRuntime(agent)),
    )
    model.assert_complete()
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert record.model_tokens == 10
    assert record.cost_minor is None
    assert record.tool_call_count == 1
    assert record.turns[0].tool_call is not None
    assert str(record.turns[0].tool_call.id) == "call_openai_sdk"
    results = [turn.tool_result for turn in record.turns if turn.tool_result is not None]
    assert len(results) == 1 and results[0].status is ToolResultStatus.SUCCESS
    terminal = record.turns[-1]
    assert terminal.provider_response_ids == ("resp_tool", "resp_final")
    assert terminal.provider_request_ids == ("req_tool", "req_final")
    assert terminal.provider_retry_count == 1


async def test_real_sdk_unauthorized_tool_result_returns_without_mutation(
    happy_scenario: Scenario,
) -> None:
    scenario = happy_scenario.model_copy(
        update={
            "trigger": happy_scenario.trigger.model_copy(
                update={"actor": {"customer_id": "cus_intruder"}}
            )
        }
    )
    model = ScriptedModel(
        [
            [
                function_call(
                    "refund_order",
                    {"order_id": "ord_900", "amount_minor": 1},
                    call_id="call_denied_sdk",
                )
            ],
            [assistant_message("request denied")],
        ]
    )
    record = await Runner(DeterministicGrader()).run(
        scenario,
        StubWorld(),
        OpenAIAgentsAdapter(OpenAIAgentsRuntime(Agent(name="test", model=model))),
    )
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert not record.events
    results = [turn.tool_result for turn in record.turns if turn.tool_result is not None]
    assert len(results) == 1 and results[0].error_type == "AuthorizationDenied"


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize(
    ("failure", "expected", "message"),
    [
        (AuthenticationError("bad credentials"), ProviderError, "authentication failed"),
        (RateLimitError("slow down"), ProviderError, "rate limit exceeded"),
        (APIConnectionError("offline"), ProviderError, "connection failed"),
        (ModelBehaviorError("invalid output"), AdapterError, "invalid model behavior"),
        (MaxTurnsExceeded("too many"), AdapterError, "maximum turns"),
        (LookupError("unexpected SDK defect"), AdapterError, r"SDK failure \(LookupError\)"),
    ],
)
async def test_sdk_failures_have_distinct_sanitized_boundaries(
    happy_scenario: Scenario,
    failure: Exception,
    expected: type[Exception],
    message: str,
) -> None:
    model = ScriptedModel([ModelStep(error=failure)])
    runtime = OpenAIAgentsRuntime(Agent(name="failure", model=model))
    with pytest.raises(expected, match=message) as captured:
        await runtime.run_with_tools(happy_scenario, [])
    assert str(failure) not in str(captured.value)
    assert captured.value.__cause__ is None


async def test_runtime_cancel_interrupts_actual_sdk_model_deadline(
    happy_scenario: Scenario,
) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def block_model(_call: object) -> ModelStep:
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    model = ScriptedModel([ModelStep(responder=block_model)])
    runtime = OpenAIAgentsRuntime(Agent(name="blocked", model=model))
    task = asyncio.create_task(runtime.run_with_tools(happy_scenario, []))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await runtime.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_secret_canary_never_enters_run_evidence(happy_scenario: Scenario) -> None:
    secret = "sk-secret-canary-must-not-appear"
    model = ScriptedModel([ModelStep(error=AuthenticationError(secret))])
    record = await Runner(DeterministicGrader()).run(
        happy_scenario,
        StubWorld(),
        OpenAIAgentsAdapter(OpenAIAgentsRuntime(Agent(name="secret", model=model))),
    )
    assert record.terminal_reason is TerminalReason.PROVIDER_ERROR
    assert secret not in record.model_dump_json()
    assert record.error_message == "openai-agents provider invocation failed"
