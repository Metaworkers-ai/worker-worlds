"""Importable demonstration factories for the supported native agent SDKs."""

from __future__ import annotations

from typing import Any, cast

from worker_worlds.adapters import LangGraphAdapter, OpenAIAgentsAdapter
from worker_worlds.agent_registry import AgentFactoryContext
from worker_worlds.langgraph_runtime import LangGraphRunContext, LangGraphRuntime
from worker_worlds.openai_agents_runtime import OpenAIAgentsRuntime


def _model_name(context: AgentFactoryContext) -> str:
    metadata = context.definition.model
    return metadata.name if metadata is not None else "gpt-5-mini"


def create_openai_agent(context: AgentFactoryContext) -> OpenAIAgentsAdapter:
    """Construct an OpenAI Agents adapter without contacting the provider."""
    from agents import Agent

    agent = Agent(
        name="worker-worlds-openai",
        instructions="Use the supplied world tools and finish the requested scenario.",
        model=_model_name(context),
    )
    return OpenAIAgentsAdapter(OpenAIAgentsRuntime(agent))


def create_langgraph_agent(context: AgentFactoryContext) -> LangGraphAdapter:
    """Construct a LangGraph adapter without contacting the provider."""
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    model = ChatOpenAI(model=_model_name(context), temperature=0)

    def graph_factory(tools: list[object], _context: LangGraphRunContext) -> object:
        return create_react_agent(model, cast(Any, tools))

    metadata = context.definition.model
    return LangGraphAdapter(
        LangGraphRuntime(
            graph_factory,
            model_provider=metadata.provider if metadata else None,
            model_name=metadata.name if metadata else None,
            model_version=metadata.version if metadata else None,
        )
    )
