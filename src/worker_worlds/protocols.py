"""Stable async behavioral interfaces."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol, runtime_checkable

from worker_worlds.contracts import (
    AuthorizationContext,
    JsonValue,
    RunRecord,
    Scenario,
    ToolCall,
    ToolResult,
    ToolSpec,
    Verdict,
    WorkerTurn,
    WorldEvent,
    WorldSnapshot,
)


@runtime_checkable
class World(Protocol):
    """A deterministic stateful environment exposed only through tools."""

    version: str

    async def reset(self, *, seed: int, run_id: str) -> WorldSnapshot:
        """Reset to deterministic state and return its snapshot."""
        ...

    async def tools(self, context: AuthorizationContext) -> list[ToolSpec]:
        """List normalized tools authorized for a context."""
        ...

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Invoke one normalized tool call."""
        ...

    async def snapshot(self) -> WorldSnapshot:
        """Capture the current canonical state."""
        ...

    async def events(self, after_sequence: int = 0) -> list[WorldEvent]:
        """Return events strictly after a sequence."""
        ...

    async def advance_time(self, delta: timedelta) -> list[WorldEvent]:
        """Advance controlled time and return resulting events."""
        ...

    async def inject(self, event_type: str, payload: dict[str, JsonValue]) -> WorldEvent:
        """Atomically apply one trusted scheduled world event."""
        ...

    async def close(self) -> None:
        """Release world resources."""
        ...


@runtime_checkable
class WorkerAdapter(Protocol):
    """Translate a worker framework into normalized turns."""

    name: str
    worker_version: str

    async def start(self, scenario: Scenario, tools: list[ToolSpec]) -> None:
        """Start a worker run with scenario input and tools."""
        ...

    async def expose_tools(self, tools: list[ToolSpec]) -> None:
        """Expose normalized world tools to the worker."""
        ...

    async def next_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        """Return the next normalized worker turn."""
        ...

    def is_terminal(self, turn: WorkerTurn) -> bool:
        """Return whether a turn explicitly ends execution."""
        ...

    async def cancel(self) -> None:
        """Propagate cancellation to the worker framework."""
        ...


@runtime_checkable
class Reporter(Protocol):
    """Persist or present a finished run without changing its meaning."""

    async def report(self, record: RunRecord) -> None:
        """Emit a derived representation of a run record."""
        ...


@runtime_checkable
class Grader(Protocol):
    """Evaluate immutable run evidence without mutating the world."""

    async def grade(self, scenario: Scenario, record: RunRecord) -> list[Verdict]:
        """Evaluate deterministic assertions over immutable evidence."""
        ...
