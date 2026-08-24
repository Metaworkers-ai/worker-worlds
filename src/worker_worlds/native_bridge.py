"""Asynchronous bridge between SDK-managed loops and the normalized runner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
    JsonValue,
    RunId,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from worker_worlds.errors import AdapterError
from worker_worlds.ids import prefixed_ulid


class NativeBridgeError(AdapterError):
    """Base error for invalid bridge lifecycle operations."""


class NativeBridgePendingError(NativeBridgeError):
    """The provider stopped while native tool calls were still pending."""


class NativeBridgeClosedError(NativeBridgeError):
    """A native call was interrupted because its bridge closed."""


@dataclass(frozen=True)
class NativeToolHandler:
    """Framework-facing declaration and asynchronous normalized callback."""

    spec: ToolSpec
    invoke: Callable[[dict[str, JsonValue], str | None], Awaitable[ToolResult]]


class NativeToolBridge:
    """Correlate concurrent SDK tool callbacks with runner-owned execution."""

    def __init__(self, tools: list[ToolSpec], authorization: AuthorizationContext) -> None:
        """Create a bridge with world-declared tools and trusted authorization."""
        self._tools = {tool.name: tool for tool in tools}
        self._authorization = authorization
        self._queue: asyncio.Queue[ToolCall] = asyncio.Queue()
        self._pending: dict[CallId, asyncio.Future[ToolResult]] = {}
        self._seen: set[CallId] = set()
        self._closed = False
        self._close_lock = asyncio.Lock()

    def handlers(self) -> list[NativeToolHandler]:
        """Return handlers in the stable order supplied by the world."""
        handlers: list[NativeToolHandler] = []
        for tool in self._tools.values():

            async def invoke(
                arguments: dict[str, JsonValue],
                request_id: str | None = None,
                *,
                name: str = tool.name,
            ) -> ToolResult:
                return await self.submit(name, arguments, request_id=request_id)

            handlers.append(NativeToolHandler(spec=tool, invoke=invoke))
        return handlers

    async def submit(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        request_id: str | None = None,
    ) -> ToolResult:
        """Queue one native request and wait for its normalized result."""
        if self._closed:
            raise NativeBridgeClosedError("native tool bridge is closed")
        if tool_name not in self._tools:
            raise NativeBridgeError(f"native provider requested unknown tool: {tool_name}")
        call_id = CallId(request_id or prefixed_ulid("call"))
        if call_id in self._seen:
            raise NativeBridgeError(f"duplicate native request id: {call_id}")
        self._seen.add(call_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ToolResult] = loop.create_future()
        self._pending[call_id] = future
        call = ToolCall(
            id=call_id,
            run_id=RunId("pending"),
            tool_name=tool_name,
            arguments=arguments,
            authorization=self._authorization,
            requested_at=datetime.now(UTC),
            idempotency_key=(
                str(arguments["idempotency_key"])
                if arguments.get("idempotency_key") is not None
                else None
            ),
        )
        await self._queue.put(call)
        try:
            return await future
        finally:
            self._pending.pop(call_id, None)

    async def next_call(self) -> ToolCall:
        """Return the next request in submission order."""
        if self._closed and self._queue.empty():
            raise NativeBridgeClosedError("native tool bridge is closed")
        return await self._queue.get()

    def resolve(self, result: ToolResult) -> None:
        """Deliver one runner result to exactly its waiting SDK callback."""
        future = self._pending.get(result.call_id)
        if future is None:
            raise NativeBridgeError(f"result has no pending native request: {result.call_id}")
        if future.done():
            raise NativeBridgeError(f"duplicate result for native request: {result.call_id}")
        future.set_result(result)

    @property
    def pending_count(self) -> int:
        """Return unresolved callback count for evidence checks."""
        return sum(not future.done() for future in self._pending.values())

    @property
    def queued_count(self) -> int:
        """Return requests not yet handed to the runner."""
        return self._queue.qsize()

    async def close(self) -> None:
        """Idempotently fail every pending callback and drain queued calls."""
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            error = NativeBridgeClosedError("native tool bridge was cancelled")
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            while not self._queue.empty():
                self._queue.get_nowait()
