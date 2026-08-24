from __future__ import annotations

import asyncio
import os
from typing import Any, cast

import pytest
from agents import Agent
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from worker_worlds.adapters import LangGraphAdapter, OpenAIAgentsAdapter
from worker_worlds.contracts import Scenario, TerminalReason
from worker_worlds.database import DatabaseSettings, connect, migrate
from worker_worlds.grading import DeterministicGrader
from worker_worlds.langgraph_runtime import LangGraphRuntime
from worker_worlds.openai_agents_runtime import OpenAIAgentsRuntime
from worker_worlds.openai_testing import ScriptedModel, assistant_message, function_call
from worker_worlds.postgres_world import PostgresWorld
from worker_worlds.protocols import WorkerAdapter
from worker_worlds.runner import Runner


@pytest.fixture(scope="session")
def real_adapter_postgres_settings() -> DatabaseSettings:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    return DatabaseSettings(url=url)


@pytest.fixture(autouse=True, scope="session")
async def real_adapter_migrated(real_adapter_postgres_settings: DatabaseSettings) -> None:
    assert await migrate(real_adapter_postgres_settings) == "006"


def _arguments() -> dict[str, object]:
    return {
        "order_id": "ord_900",
        "amount_minor": 2499,
        "currency": "USD",
        "idempotency_key": "real-adapter-refund",
    }


class ToolAwareFakeChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolAwareFakeChatModel:
        del tools, kwargs
        return self


@pytest.mark.parametrize("adapter_name", ["openai-agents", "langgraph"])
async def test_real_adapter_contract_against_postgres(
    adapter_name: str,
    happy_scenario: Scenario,
    real_adapter_postgres_settings: DatabaseSettings,
) -> None:
    if adapter_name == "openai-agents":
        model = ScriptedModel(
            [
                [function_call("get_order", {"order_id": "ord_900"}, call_id="call_lookup")],
                [function_call("issue_refund", _arguments(), call_id="call_real_openai")],
                [assistant_message("complete")],
            ]
        )
        adapter: OpenAIAgentsAdapter | LangGraphAdapter = OpenAIAgentsAdapter(
            OpenAIAgentsRuntime(Agent(name="postgres-openai", model=model))
        )
    else:
        graph_model = ToolAwareFakeChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "issue_refund",
                            "args": _arguments(),
                            "id": "call_real_langgraph",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="complete"),
            ]
        )
        adapter = LangGraphAdapter(
            LangGraphRuntime(lambda tools, _context: create_agent(graph_model, cast(Any, tools)))
        )
    world = PostgresWorld(real_adapter_postgres_settings, f"e2e.{adapter_name}")
    record = await Runner(DeterministicGrader()).run(happy_scenario, world, adapter)
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert record.passed, record.model_dump_json()
    assert record.tool_call_count == (2 if adapter_name == "openai-agents" else 1)
    assert len(record.events) == 1
    assert record.events[0].event_type == "refund.issued"
    assert record.cleanup_succeeded


@pytest.mark.parametrize("adapter_name", ["openai-agents", "langgraph"])
async def test_real_adapter_unauthorized_refund_keeps_postgres_unchanged(
    adapter_name: str,
    happy_scenario: Scenario,
    real_adapter_postgres_settings: DatabaseSettings,
) -> None:
    unauthorized = happy_scenario.model_copy(
        update={
            "trigger": happy_scenario.trigger.model_copy(
                update={"actor": {"customer_id": "cus_intruder"}}
            )
        }
    )
    if adapter_name == "openai-agents":
        model = ScriptedModel(
            [
                [function_call("issue_refund", _arguments(), call_id="call_denied_postgres")],
                [assistant_message("authorization denied")],
            ]
        )
        adapter: WorkerAdapter = OpenAIAgentsAdapter(
            OpenAIAgentsRuntime(Agent(name="unauthorized-openai", model=model))
        )
    else:
        graph_model = ToolAwareFakeChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "issue_refund",
                            "args": _arguments(),
                            "id": "call_denied_langgraph",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="authorization denied"),
            ]
        )
        adapter = LangGraphAdapter(
            LangGraphRuntime(lambda tools, _context: create_agent(graph_model, cast(Any, tools)))
        )
    world = PostgresWorld(real_adapter_postgres_settings, f"e2e.{adapter_name}-unauthorized")
    record = await Runner(DeterministicGrader()).run(unauthorized, world, adapter)
    results = [turn.tool_result for turn in record.turns if turn.tool_result is not None]
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert not record.incomplete_evidence
    assert record.initial_snapshot is not None and record.final_snapshot is not None
    assert record.initial_snapshot.state == record.final_snapshot.state
    assert not record.events
    assert len(results) == 1 and results[0].error_type == "AuthorizationDenied"
    assert record.cleanup_succeeded


class PausedMutationWorld(PostgresWorld):
    def __init__(self, settings: DatabaseSettings, scenario_id: str) -> None:
        super().__init__(settings, scenario_id)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, call: Any) -> Any:
        self.entered.set()
        await self.release.wait()
        return await super().invoke(call)


@pytest.mark.parametrize("adapter_name", ["openai-agents", "langgraph"])
async def test_cancel_during_postgres_mutation_cleans_namespace(
    adapter_name: str,
    happy_scenario: Scenario,
    real_adapter_postgres_settings: DatabaseSettings,
) -> None:
    if adapter_name == "openai-agents":
        model = ScriptedModel(
            [[function_call("issue_refund", _arguments(), call_id="call_cancel_postgres")]]
        )
        adapter: OpenAIAgentsAdapter | LangGraphAdapter = OpenAIAgentsAdapter(
            OpenAIAgentsRuntime(Agent(name="cancel-openai", model=model))
        )
    else:
        graph_model = ToolAwareFakeChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "issue_refund",
                            "args": _arguments(),
                            "id": "call_cancel_langgraph",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
        adapter = LangGraphAdapter(
            LangGraphRuntime(lambda tools, _context: create_agent(graph_model, cast(Any, tools)))
        )
    world = PausedMutationWorld(real_adapter_postgres_settings, f"e2e.cancel.{adapter_name}")
    task = asyncio.create_task(Runner(DeterministicGrader()).run(happy_scenario, world, adapter))
    await asyncio.wait_for(world.entered.wait(), timeout=2)
    task.cancel()
    record = await task
    assert record.terminal_reason is TerminalReason.CANCELLED
    assert record.cleanup_succeeded
    assert world.cleanup_succeeded
    assert adapter.pending_native_calls == 0
    assert adapter.queued_native_calls == 0
    connection = await connect(real_adapter_postgres_settings)
    try:
        lease = await connection.fetchrow(
            "SELECT namespace FROM worker_worlds.run_leases WHERE run_id=$1", str(record.id)
        )
        namespace_exists = await connection.fetchval(
            "SELECT to_regnamespace($1)::text",
            f"ww_run_{str(record.id).removeprefix('run_').lower()}",
        )
        assert lease is None
        assert namespace_exists is None
    finally:
        await connection.close()
