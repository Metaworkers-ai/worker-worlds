"""Worker Worlds public package."""

from worker_worlds.agent_registry import (
    AgentAdapterType,
    AgentDefinition,
    AgentFactoryContext,
    AgentModelMetadata,
    AgentReadiness,
    AgentRegistry,
)
from worker_worlds.contracts import RunRecord, Scenario
from worker_worlds.langgraph_runtime import LangGraphRunContext
from worker_worlds.native_bridge import NativeToolBridge, NativeToolHandler

__all__ = [
    "AgentAdapterType",
    "AgentDefinition",
    "AgentFactoryContext",
    "AgentModelMetadata",
    "AgentReadiness",
    "AgentRegistry",
    "LangGraphRunContext",
    "NativeToolBridge",
    "NativeToolHandler",
    "RunRecord",
    "Scenario",
]
__version__ = "1.0.0rc1"
USER_AGENT = f"worker-worlds/{__version__}"
