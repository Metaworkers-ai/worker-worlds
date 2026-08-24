"""Real OpenAI Agents SDK runtime backed by the shared native tool bridge."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from worker_worlds.adapters import NativeDecision
from worker_worlds.contracts import JsonValue, Scenario
from worker_worlds.errors import AdapterError, ProviderError
from worker_worlds.native_bridge import NativeToolHandler


class OpenAIAgentsRuntime:
    """Execute a developer-provided Agents SDK Agent through Worker Worlds tools."""

    def __init__(self, agent: object, *, max_turns: int = 10) -> None:
        """Retain an SDK agent while deferring provider work until a run starts."""
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        self._agent = agent
        self._max_turns = max_turns
        self._task: asyncio.Task[Any] | None = None

    async def run_with_tools(
        self, scenario: Scenario, tools: list[NativeToolHandler]
    ) -> NativeDecision:
        """Run the SDK loop with strict bridge-backed FunctionTool declarations."""
        try:
            from agents import FunctionTool, RunConfig, Runner
            from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
            from agents.tool_context import ToolContext
        except ImportError:
            raise AdapterError(
                "OpenAI Agents runtime requires 'worker-worlds[openai-agents]'"
            ) from None

        sdk_tools: list[FunctionTool] = []
        for handler in tools:

            async def invoke(
                context: ToolContext[Any],
                arguments: str,
                *,
                bridge_handler: NativeToolHandler = handler,
            ) -> JsonValue:
                try:
                    decoded = json.loads(arguments)
                except (TypeError, ValueError):
                    decoded = {}
                if not isinstance(decoded, dict):
                    decoded = {}
                result = await bridge_handler.invoke(decoded, context.tool_call_id)
                return result.model_dump(mode="json")

            sdk_tools.append(
                FunctionTool(
                    name=handler.spec.name,
                    description=handler.spec.description,
                    params_json_schema=handler.spec.input_schema,
                    output_json_schema=handler.spec.output_schema or None,
                    on_invoke_tool=invoke,
                    strict_json_schema=True,
                )
            )

        clone = getattr(self._agent, "clone", None)
        if not callable(clone):
            raise AdapterError("OpenAI factory must provide an Agents SDK Agent")
        agent = clone(tools=sdk_tools)
        try:
            self._task = asyncio.create_task(
                Runner.run(
                    agent,
                    scenario.trigger.content,
                    max_turns=self._max_turns,
                    run_config=RunConfig(
                        tracing_disabled=True,
                        trace_include_sensitive_data=False,
                        workflow_name="Worker Worlds",
                    ),
                )
            )
            result = await self._task
        except asyncio.CancelledError:
            raise
        except MaxTurnsExceeded:
            raise AdapterError("OpenAI agent exceeded its maximum turns") from None
        except ModelBehaviorError:
            raise AdapterError("OpenAI agent returned invalid model behavior") from None
        except Exception as exc:
            error_name = type(exc).__name__
            provider_messages = {
                "AuthenticationError": "OpenAI authentication failed",
                "RateLimitError": "OpenAI rate limit exceeded",
                "APIConnectionError": "OpenAI provider connection failed",
                "APITimeoutError": "OpenAI provider request timed out",
                "InternalServerError": "OpenAI provider internal error",
            }
            if error_name in provider_messages:
                raise ProviderError(provider_messages[error_name]) from None
            raise AdapterError(f"OpenAI Agents SDK failure ({error_name})") from None
        finally:
            self._task = None

        usage = result.context_wrapper.usage
        total_tokens = usage.total_tokens or usage.input_tokens + usage.output_tokens
        provider_response_ids = tuple(
            response.response_id for response in result.raw_responses if response.response_id
        )
        provider_request_ids = tuple(
            response.request_id for response in result.raw_responses if response.request_id
        )
        return NativeDecision(
            message=str(result.final_output),
            terminal=True,
            model_tokens=max(0, total_tokens),
            model_input_tokens=max(0, usage.input_tokens),
            model_output_tokens=max(0, usage.output_tokens),
            provider_response_ids=provider_response_ids,
            provider_request_ids=provider_request_ids,
            provider_retry_count=max(0, usage.requests - len(result.raw_responses)),
        )

    async def cancel(self) -> None:
        """Cancel and join an active SDK task."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
