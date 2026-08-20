"""Credential-guarded provider smoke tests; never enabled by default."""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _enabled() -> bool:
    return os.environ.get("WORKER_WORLDS_LIVE_SMOKE") == "1"


def _pricing(model: str) -> tuple[Decimal, Decimal]:
    """Return explicit USD-per-million rates for bounded live-test accounting."""
    if model in {"gpt-4.1-mini", "gpt-4.1-mini-2025-04-14"}:
        return Decimal("0.40"), Decimal("1.60")
    if model in {"gpt-5-mini", "gpt-5-mini-2025-08-07"}:
        return Decimal("0.25"), Decimal("2.00")
    input_rate = os.environ.get("WORKER_WORLDS_LIVE_INPUT_USD_PER_MILLION")
    output_rate = os.environ.get("WORKER_WORLDS_LIVE_OUTPUT_USD_PER_MILLION")
    if input_rate is None or output_rate is None:
        pytest.fail("custom live models require explicit input and output pricing rates")
    return Decimal(input_rate), Decimal(output_rate)


def _measured_cost_minor(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Calculate token cost in currency minor units using the configured model rates."""
    input_rate, output_rate = _pricing(model)
    return (
        (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate)
        * Decimal(100)
        / Decimal(1_000_000)
    )


def _live_ceilings() -> tuple[int, int, int]:
    maximum_tokens = int(os.environ.get("WORKER_WORLDS_LIVE_MAX_TOKENS", "32"))
    maximum_cost_minor = int(os.environ.get("WORKER_WORLDS_LIVE_MAX_COST_MINOR", "5"))
    maximum_retries = int(os.environ.get("WORKER_WORLDS_LIVE_MAX_RETRIES", "0"))
    if (
        not 1 <= maximum_tokens <= 64
        or not 0 < maximum_cost_minor <= 5
        or not 0 <= maximum_retries <= 2
    ):
        raise ValueError("live smoke ceilings exceed the release-test maximum")
    return maximum_tokens, maximum_cost_minor, maximum_retries


def test_live_cost_measurement_is_directional() -> None:
    assert _measured_cost_minor("gpt-4.1-mini", 1_000_000, 0) == Decimal(40)
    assert _measured_cost_minor("gpt-4.1-mini", 0, 1_000_000) == Decimal(160)


def test_live_retry_ceiling_is_enforced_without_a_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_WORLDS_LIVE_MAX_RETRIES", "3")
    with pytest.raises(ValueError, match="ceilings exceed"):
        _live_ceilings()


@pytest.mark.parametrize("adapter_name", ["langgraph", "openai-agents"])
def test_optional_live_provider_smoke(adapter_name: str) -> None:
    """Exercise each real runtime with one bounded synthetic provider request."""
    if not _enabled():
        pytest.skip("set WORKER_WORLDS_LIVE_SMOKE=1 and explicitly authorize paid calls")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY is required only after live smoke is explicitly enabled")
    maximum_tokens, maximum_cost_minor, maximum_retries = _live_ceilings()
    model = os.environ.get("WORKER_WORLDS_LIVE_MODEL", "gpt-4.1-mini")

    async def execute() -> None:
        if adapter_name == "openai-agents":
            from agents import Agent, ModelRetrySettings, ModelSettings

            from worker_worlds.openai_agents_runtime import OpenAIAgentsRuntime
            from worker_worlds.scenarios import load_scenario

            scenario = load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
            runtime = OpenAIAgentsRuntime(
                Agent(
                    name="worker-worlds-live-smoke",
                    instructions="Return exactly the word READY. Do not call tools.",
                    model=model,
                    model_settings=ModelSettings(
                        max_tokens=maximum_tokens,
                        retry=ModelRetrySettings(max_retries=maximum_retries),
                    ),
                ),
                max_turns=1,
            )
            try:
                decision = await asyncio.wait_for(runtime.run_with_tools(scenario, []), timeout=30)
                assert decision.terminal
                assert decision.provider_response_ids
                assert decision.provider_retry_count <= maximum_retries
                assert decision.model_input_tokens is not None
                assert decision.model_output_tokens is not None
                cost_minor = _measured_cost_minor(
                    model, decision.model_input_tokens, decision.model_output_tokens
                )
                assert cost_minor <= Decimal(maximum_cost_minor)
                print(
                    f"adapter={adapter_name} package_version={version('openai-agents')} "
                    "provider=openai response_id_present=true runtime_exercised=true "
                    f"retries={decision.provider_retry_count} cost_minor={cost_minor}"
                )
            finally:
                await runtime.cancel()
            return

        from langchain_openai import ChatOpenAI
        from langgraph.prebuilt import create_react_agent

        from worker_worlds.langgraph_runtime import LangGraphRuntime
        from worker_worlds.scenarios import load_scenario

        scenario = load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
        chat_model = ChatOpenAI(
            model=model,
            max_tokens=maximum_tokens,
            max_retries=maximum_retries,
            temperature=0,
        )
        langgraph_runtime = LangGraphRuntime(
            lambda tools, _context: create_react_agent(chat_model, tools),
            model_provider="openai",
            model_name=model,
        )
        try:
            decision = await asyncio.wait_for(
                langgraph_runtime.run_with_tools(scenario, []), timeout=30
            )
            assert decision.terminal
            assert decision.model_provider == "openai"
            assert decision.model_name == model
            assert decision.provider_response_ids
            assert decision.provider_retry_count <= maximum_retries
            assert decision.model_input_tokens is not None
            assert decision.model_output_tokens is not None
            cost_minor = _measured_cost_minor(
                model, decision.model_input_tokens, decision.model_output_tokens
            )
            assert cost_minor <= Decimal(maximum_cost_minor)
            print(
                f"adapter={adapter_name} package_version={version('langgraph')} "
                f"provider=openai model={model} response_id_present=true "
                f"runtime_exercised=true retries={decision.provider_retry_count} "
                f"cost_minor={cost_minor}"
            )
        finally:
            await langgraph_runtime.cancel()

    asyncio.run(execute())
