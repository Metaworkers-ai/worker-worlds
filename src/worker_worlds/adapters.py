"""Optional native framework adapters with deterministic test seams."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Protocol, cast

from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
    JsonValue,
    RunId,
    Scenario,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnId,
    WorkerTurn,
)
from worker_worlds.errors import AdapterError, ProviderError
from worker_worlds.ids import prefixed_ulid
from worker_worlds.native_bridge import (
    NativeBridgePendingError,
    NativeToolBridge,
    NativeToolHandler,
)


@dataclass(frozen=True)
class NativeDecision:
    """Small framework seam normalized by every native adapter."""

    message: str | None = None
    tool_name: str | None = None
    arguments: dict[str, JsonValue] | None = None
    terminal: bool = False
    model_tokens: int | None = None
    model_input_tokens: int | None = None
    model_output_tokens: int | None = None
    cost_minor: int | None = None
    request_id: str | None = None
    provider_response_ids: tuple[str, ...] = ()
    provider_request_ids: tuple[str, ...] = ()
    provider_retry_count: int = 0
    model_provider: str | None = None
    model_name: str | None = None
    model_version: str | None = None


class NativeRuntime(Protocol):
    """Deterministic seam used by SDK-specific invocation glue."""

    async def decide(
        self,
        scenario: Scenario,
        tools: list[dict[str, JsonValue]],
        tool_result: ToolResult | None,
        turn_index: int,
    ) -> NativeDecision:
        """Return one native worker decision."""
        ...

    async def cancel(self) -> None:
        """Cancel native framework work."""
        ...


RuntimeFactory = Callable[[], NativeRuntime]


class NativeBridgeRuntime(Protocol):
    """Opt-in seam for SDKs that own their model/tool loop."""

    async def run_with_tools(
        self, scenario: Scenario, tools: list[NativeToolHandler]
    ) -> NativeDecision:
        """Run the provider loop using callbacks backed by the runner."""
        ...

    async def cancel(self) -> None:
        """Cancel provider work."""
        ...


class NativeAdapter:
    """Shared translation boundary for supported native frameworks."""

    name = "native"
    worker_version = "1.0"
    required_module = ""
    install_extra = ""

    def __init__(self, runtime: NativeRuntime | NativeBridgeRuntime | None = None) -> None:
        """Use an injected test seam or load an installed native runtime."""
        if runtime is None:
            if not self.required_module or importlib.util.find_spec(self.required_module) is None:
                raise AdapterError(
                    f"{self.name} adapter requires 'worker-worlds[{self.install_extra}]'"
                )
            raise AdapterError(
                f"{self.name} native runtime must be configured with a worker callable"
            )
        self._runtime = runtime
        self._scenario: Scenario | None = None
        self._tools: list[ToolSpec] = []
        self._turn_index = 0
        self._bridge: NativeToolBridge | None = None
        self._provider_task: asyncio.Task[NativeDecision] | None = None
        self._provider_ended_with_pending = False
        self._cancel_lock = asyncio.Lock()
        self._cancelled = False
        self._cancellation_failed = False

    async def start(self, scenario: Scenario, tools: list[ToolSpec]) -> None:
        """Start a normalized native run."""
        self._scenario = scenario
        self._turn_index = 0
        self._cancelled = False
        self._cancellation_failed = False
        await self.expose_tools(tools)
        run_with_tools = getattr(self._runtime, "run_with_tools", None)
        if callable(run_with_tools):
            authorization = AuthorizationContext(
                actor_id=self.name,
                customer_id=str(scenario.trigger.actor.get("customer_id", "")) or None,
                scopes=frozenset({"refund:own_order"}),
            )
            self._bridge = NativeToolBridge(tools, authorization)
            self._provider_ended_with_pending = False
            runtime = cast(NativeBridgeRuntime, self._runtime)
            self._provider_task = asyncio.create_task(
                runtime.run_with_tools(scenario, self._bridge.handlers())
            )

    async def expose_tools(self, tools: list[ToolSpec]) -> None:
        """Translate and retain framework-neutral tool schemas."""
        self._tools = list(tools)

    def native_tools(self) -> list[dict[str, JsonValue]]:
        """Return SDK-ready function declarations without database access."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in self._tools
        ]

    async def next_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        """Normalize a native decision into a WorkerTurn."""
        if self._scenario is None:
            raise AdapterError("adapter has not been started")
        if self._bridge is not None:
            return await self._next_bridge_turn(tool_result)
        failure_type: str | None = None
        try:
            runtime = cast(NativeRuntime, self._runtime)
            decision = await runtime.decide(
                self._scenario, self.native_tools(), tool_result, self._turn_index
            )
        except Exception as exc:
            failure_type = type(exc).__name__
        if failure_type is not None:
            raise AdapterError(f"{self.name} worker invocation failed ({failure_type})")
        now = datetime.now(UTC)
        tool_call = None
        if decision.tool_name is not None:
            if decision.arguments is None:
                raise AdapterError("native tool call omitted arguments")
            tool_call = ToolCall(
                id=CallId(decision.request_id or prefixed_ulid("call")),
                run_id=RunId("pending"),
                tool_name=decision.tool_name,
                arguments=decision.arguments,
                authorization=AuthorizationContext(
                    actor_id=self.name,
                    customer_id=str(self._scenario.trigger.actor.get("customer_id", "")) or None,
                    scopes=frozenset({"refund:own_order"}),
                ),
                requested_at=now,
                idempotency_key=(
                    str(decision.arguments.get("idempotency_key"))
                    if decision.arguments.get("idempotency_key") is not None
                    else None
                ),
            )
        turn = WorkerTurn(
            id=TurnId(prefixed_ulid("turn")),
            index=self._turn_index,
            occurred_at=now,
            message=decision.message,
            tool_call=tool_call,
            tool_result=tool_result,
            terminal=decision.terminal,
            model_tokens=decision.model_tokens,
            model_input_tokens=decision.model_input_tokens,
            model_output_tokens=decision.model_output_tokens,
            cost_minor=decision.cost_minor,
            provider_response_ids=decision.provider_response_ids,
            provider_request_ids=decision.provider_request_ids,
            provider_retry_count=decision.provider_retry_count,
            model_provider=decision.model_provider,
            model_name=decision.model_name,
            model_version=decision.model_version,
        )
        self._turn_index += 1
        return turn

    async def _next_bridge_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        """Race provider completion against FIFO native tool submissions."""
        assert self._bridge is not None
        assert self._provider_task is not None
        if self._provider_task.done() and self._bridge.pending_count:
            self._provider_ended_with_pending = True
        if tool_result is not None:
            self._bridge.resolve(tool_result)
        if self._provider_ended_with_pending:
            return self._provider_terminal_turn(tool_result)
        if self._bridge.queued_count:
            call = await self._bridge.next_call()
            self._provider_ended_with_pending = self._provider_task.done()
            return self._call_turn(call, tool_result)
        call_task = asyncio.create_task(self._bridge.next_call())
        done, _ = await asyncio.wait(
            {call_task, self._provider_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if call_task in done:
            self._provider_ended_with_pending = self._provider_task.done()
            return self._call_turn(call_task.result(), tool_result)
        call_task.cancel()
        await asyncio.gather(call_task, return_exceptions=True)
        if self._bridge.pending_count or self._bridge.queued_count:
            self._provider_ended_with_pending = True
            return self._provider_terminal_turn(tool_result)
        return self._provider_terminal_turn(tool_result)

    def _provider_terminal_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        assert self._provider_task is not None
        if self._provider_ended_with_pending:
            raise NativeBridgePendingError(
                "native provider terminated with pending tool requests"
            ) from None
        failure_type: str | None = None
        provider_failed = False
        try:
            decision = self._provider_task.result()
        except Exception as exc:
            failure_type = type(exc).__name__
            provider_failed = isinstance(exc, ProviderError)
        if failure_type is not None:
            if provider_failed:
                raise ProviderError(f"{self.name} provider invocation failed") from None
            raise AdapterError(f"{self.name} worker invocation failed ({failure_type})")
        now = datetime.now(UTC)
        turn = WorkerTurn(
            id=TurnId(prefixed_ulid("turn")),
            index=self._turn_index,
            occurred_at=now,
            message=decision.message,
            tool_result=tool_result,
            terminal=True,
            model_tokens=decision.model_tokens,
            model_input_tokens=decision.model_input_tokens,
            model_output_tokens=decision.model_output_tokens,
            cost_minor=decision.cost_minor,
            provider_response_ids=decision.provider_response_ids,
            provider_request_ids=decision.provider_request_ids,
            provider_retry_count=decision.provider_retry_count,
            model_provider=decision.model_provider,
            model_name=decision.model_name,
            model_version=decision.model_version,
        )
        self._turn_index += 1
        return turn

    def _call_turn(self, call: ToolCall, tool_result: ToolResult | None) -> WorkerTurn:
        now = datetime.now(UTC)
        turn = WorkerTurn(
            id=TurnId(prefixed_ulid("turn")),
            index=self._turn_index,
            occurred_at=now,
            tool_call=call,
            tool_result=tool_result,
        )
        self._turn_index += 1
        return turn

    @property
    def continues_after_tool_error(self) -> bool:
        """Only SDK-managed bridge loops receive structured tool failures."""
        return self._bridge is not None

    @property
    def incomplete_native_evidence(self) -> bool:
        """Report provider termination while callbacks were unresolved."""
        return self._provider_ended_with_pending

    @property
    def cancellation_failed(self) -> bool:
        """Report a sanitized native-runtime cancellation failure."""
        return self._cancellation_failed

    @property
    def pending_native_calls(self) -> int:
        """Return unresolved bridge callbacks for lifecycle verification."""
        return self._bridge.pending_count if self._bridge is not None else 0

    @property
    def queued_native_calls(self) -> int:
        """Return queued bridge callbacks for lifecycle verification."""
        return self._bridge.queued_count if self._bridge is not None else 0

    def is_terminal(self, turn: WorkerTurn) -> bool:
        """Recognize explicit native completion."""
        return turn.terminal

    async def cancel(self) -> None:
        """Propagate cancellation to the native runtime."""
        async with self._cancel_lock:
            if self._cancelled:
                return
            self._cancelled = True
            if self._bridge is not None:
                await self._bridge.close()
            try:
                await self._runtime.cancel()
            except BaseException:
                self._cancellation_failed = True
            finally:
                if self._provider_task is not None:
                    if not self._provider_task.done():
                        self._provider_task.cancel()
                    await asyncio.gather(self._provider_task, return_exceptions=True)


class LangGraphAdapter(NativeAdapter):
    """LangGraph translation adapter."""

    name = "langgraph"
    required_module = "langgraph"
    install_extra = "langgraph"

    def sdk_tools(self) -> list[object]:
        """Construct real LangChain tools when the LangGraph extra is installed."""
        if importlib.util.find_spec("langchain_core.tools") is None:
            raise AdapterError("langgraph SDK tool construction requires worker-worlds[langgraph]")
        structured_tool = import_module("langchain_core.tools").StructuredTool

        async def unavailable(**arguments: JsonValue) -> dict[str, JsonValue]:
            return {"normalized_by_worker_worlds": True, "arguments": arguments}

        return [
            structured_tool.from_function(
                coroutine=unavailable,
                name=tool.name,
                description=tool.description,
            )
            for tool in self._tools
        ]


class OpenAIAgentsAdapter(NativeAdapter):
    """OpenAI Agents SDK translation adapter."""

    name = "openai-agents"
    required_module = "agents"
    install_extra = "openai-agents"

    def sdk_tools(self) -> list[object]:
        """Construct real OpenAI Agents SDK FunctionTool declarations when installed."""
        if importlib.util.find_spec("agents") is None:
            raise AdapterError(
                "OpenAI Agents SDK tool construction requires worker-worlds[openai-agents]"
            )
        function_tool = import_module("agents").FunctionTool

        async def normalize(_context: object, arguments: str) -> JsonValue:
            value = json.loads(arguments)
            return {"normalized_by_worker_worlds": True, "arguments": value}

        return [
            function_tool(
                name=tool.name,
                description=tool.description,
                params_json_schema=tool.input_schema,
                on_invoke_tool=normalize,
                strict_json_schema=True,
            )
            for tool in self._tools
        ]


class DeterministicFakeRuntime:
    """Network-free native runtime used by examples and conformance tests."""

    def __init__(self, decisions: list[NativeDecision]) -> None:
        """Configure a finite deterministic decision script."""
        self._decisions = decisions
        self.cancelled = False

    async def decide(
        self,
        scenario: Scenario,
        tools: list[dict[str, JsonValue]],
        tool_result: ToolResult | None,
        turn_index: int,
    ) -> NativeDecision:
        """Return the indexed decision while validating tool discovery."""
        del scenario, tool_result
        if not tools:
            raise AdapterError("no tools were exposed")
        if turn_index >= len(self._decisions):
            return NativeDecision(message="complete", terminal=True)
        return self._decisions[turn_index]

    async def cancel(self) -> None:
        """Record deterministic cancellation."""
        self.cancelled = True


def refund_fake_runtime() -> DeterministicFakeRuntime:
    """Return a deterministic partial-refund worker script."""
    return DeterministicFakeRuntime(
        [
            NativeDecision(
                tool_name="issue_refund",
                arguments={
                    "order_id": "ord_900",
                    "amount_minor": 2499,
                    "currency": "USD",
                    "idempotency_key": "native-refund-1",
                },
                model_tokens=20,
                cost_minor=1,
            ),
            NativeDecision(message="Refund completed", terminal=True, model_tokens=5),
        ]
    )
