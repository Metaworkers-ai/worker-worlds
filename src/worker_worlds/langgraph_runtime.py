"""Real LangGraph runtime backed by the shared native tool bridge."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast
from uuid import uuid4

from pydantic import ConfigDict, create_model

from worker_worlds.adapters import NativeDecision
from worker_worlds.contracts import JsonValue, Scenario
from worker_worlds.errors import AdapterError, ProviderError
from worker_worlds.native_bridge import NativeToolHandler


@dataclass(frozen=True)
class LangGraphRunContext:
    """Run-scoped graph construction context safe for checkpoint namespaces."""

    scenario: Scenario
    thread_id: str


GraphFactory = Callable[[list[Any], LangGraphRunContext], Any]


def _python_type(schema: object) -> type[object]:
    if not isinstance(schema, dict):
        return object
    schema_type = schema.get("type")
    if not isinstance(schema_type, str):
        return object
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }.get(schema_type, object)


def _structured_tool(handler: NativeToolHandler) -> object:
    from langchain_core.tools import InjectedToolCallId, StructuredTool

    schema = handler.spec.input_schema
    properties = schema.get("properties", {})
    raw_required = schema.get("required", [])
    required = (
        {item for item in raw_required if isinstance(item, str)}
        if isinstance(raw_required, list)
        else set()
    )
    fields: dict[str, Any] = {}
    if isinstance(properties, dict):
        for name, definition in properties.items():
            if not isinstance(name, str):
                continue
            value_type = _python_type(definition)
            fields[name] = (value_type, ... if name in required else None)
    fields["tool_call_id"] = (Annotated[str, InjectedToolCallId], ...)
    args_model = create_model(
        f"{handler.spec.name.title().replace('_', '')}Arguments",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )

    async def invoke(tool_call_id: str, **arguments: JsonValue) -> dict[str, JsonValue]:
        supplied = cast(
            dict[str, JsonValue],
            {name: value for name, value in arguments.items() if value is not None},
        )
        result = await handler.invoke(supplied, tool_call_id)
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=invoke,
        name=handler.spec.name,
        description=handler.spec.description,
        args_schema=args_model,
        infer_schema=False,
    )


class LangGraphRuntime:
    """Execute a run-scoped compiled graph through Worker Worlds tools."""

    def __init__(
        self,
        graph_factory: GraphFactory,
        *,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> None:
        """Store a factory so every evaluation receives isolated graph state."""
        self._graph_factory = graph_factory
        self._task: asyncio.Task[Any] | None = None
        self.last_thread_id: str | None = None
        self._model_provider = model_provider
        self._model_name = model_name
        self._model_version = model_version

    async def run_with_tools(
        self, scenario: Scenario, tools: list[NativeToolHandler]
    ) -> NativeDecision:
        """Build and execute one graph with a unique checkpoint identity."""
        self.last_thread_id = f"ww-{uuid4()}"
        context = LangGraphRunContext(scenario=scenario, thread_id=self.last_thread_id)
        try:
            sdk_tools = [_structured_tool(handler) for handler in tools]
            graph = self._graph_factory(sdk_tools, context)
        except ImportError:
            raise AdapterError("LangGraph runtime requires 'worker-worlds[langgraph]'") from None
        except Exception as exc:
            raise AdapterError(f"LangGraph construction failed ({type(exc).__name__})") from None
        ainvoke = getattr(graph, "ainvoke", None)
        if not callable(ainvoke):
            raise AdapterError("LangGraph factory must return an async runnable")

        try:
            self._task = asyncio.create_task(
                ainvoke(
                    {"messages": [("user", scenario.trigger.content)]},
                    config={"configurable": {"thread_id": self.last_thread_id}},
                )
            )
            state = await self._task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_name = type(exc).__name__
            provider_messages = {
                "AuthenticationError": "LangGraph provider authentication failed",
                "RateLimitError": "LangGraph provider rate limit exceeded",
                "APIConnectionError": "LangGraph provider connection failed",
                "APITimeoutError": "LangGraph provider request timed out",
            }
            if error_name in provider_messages:
                raise ProviderError(provider_messages[error_name]) from None
            if error_name in {"ModelBehaviorError", "OutputParserException"}:
                raise AdapterError("LangGraph model returned invalid behavior") from None
            if error_name == "TimeoutError":
                raise AdapterError("LangGraph execution timed out") from None
            raise AdapterError(f"LangGraph execution failed ({error_name})") from None
        finally:
            self._task = None

        messages = state.get("messages", []) if isinstance(state, dict) else []
        final_message = messages[-1] if messages else None
        content = getattr(final_message, "content", state)
        tokens = 0
        input_tokens = 0
        output_tokens = 0
        provider_response_ids: list[str] = []
        provider_request_ids: list[str] = []
        provider_retry_count = 0
        for message in messages:
            usage = getattr(message, "usage_metadata", None)
            if isinstance(usage, dict):
                value = usage.get("total_tokens")
                if isinstance(value, int) and value >= 0:
                    tokens += value
                input_value = usage.get("input_tokens")
                output_value = usage.get("output_tokens")
                if isinstance(input_value, int) and input_value >= 0:
                    input_tokens += input_value
                if isinstance(output_value, int) and output_value >= 0:
                    output_tokens += output_value
            if getattr(message, "type", None) != "ai":
                continue
            response_metadata = getattr(message, "response_metadata", None)
            metadata = response_metadata if isinstance(response_metadata, dict) else {}
            response_id = metadata.get("response_id") or getattr(message, "id", None)
            request_id = metadata.get("request_id")
            retry_count = metadata.get("retry_count", 0)
            if isinstance(response_id, str) and response_id:
                provider_response_ids.append(response_id)
            if isinstance(request_id, str) and request_id:
                provider_request_ids.append(request_id)
            if isinstance(retry_count, int) and retry_count >= 0:
                provider_retry_count += retry_count
        return NativeDecision(
            message=str(content),
            terminal=True,
            model_tokens=tokens,
            model_input_tokens=input_tokens,
            model_output_tokens=output_tokens,
            provider_response_ids=tuple(provider_response_ids),
            provider_request_ids=tuple(provider_request_ids),
            provider_retry_count=provider_retry_count,
            model_provider=self._model_provider,
            model_name=self._model_name,
            model_version=self._model_version,
        )

    async def cancel(self) -> None:
        """Cancel and join an active graph task."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
