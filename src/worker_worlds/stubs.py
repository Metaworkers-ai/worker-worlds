"""Deterministic in-memory stubs for contract-first development."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
    EntityRef,
    EventId,
    JsonValue,
    RunId,
    Scenario,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
    TurnId,
    WorkerTurn,
    WorldEvent,
    WorldSnapshot,
)
from worker_worlds.errors import InfrastructureError


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{uuid5(NAMESPACE_URL, value).hex}"


class StubWorld:
    """Small deterministic refund world implementing the frozen World protocol."""

    version = "1.0"

    def __init__(self, *, fail_snapshot: bool = False) -> None:
        """Create an empty stub, optionally faulting snapshot capture."""
        self._run_id = RunId("")
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)
        self._state: dict[str, JsonValue] = {}
        self._events: list[WorldEvent] = []
        self._closed = False
        self._fail_snapshot = fail_snapshot

    async def reset(self, *, seed: int, run_id: str) -> WorldSnapshot:
        """Reset to canonical state for a seed."""
        self._run_id = RunId(run_id)
        self._clock = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seed % 86_400)
        self._state = {
            "orders": {
                "ord_900": {
                    "customer_id": "cus_102",
                    "captured_minor": 10000,
                    "refunded_minor": 0,
                    "currency": "USD",
                }
            },
            "refunds": [],
        }
        self._events = []
        self._closed = False
        return await self.snapshot()

    async def tools(self, context: AuthorizationContext) -> list[ToolSpec]:
        """Return tools available under trusted authorization."""
        del context
        return [
            ToolSpec(
                name="refund_order",
                description="Issue a partial refund in integer minor units.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string"},
                        "amount_minor": {"type": "integer"},
                    },
                    "required": ["order_id", "amount_minor"],
                },
            )
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Validate authority and atomically append a refund event."""
        started = self._clock
        order_id = call.arguments.get("order_id")
        amount = call.arguments.get("amount_minor")
        if call.arguments.get("force_error") is True:
            return self._error_result(call, "StubToolError", "forced tool failure", started)
        if not isinstance(order_id, str) or not isinstance(amount, int) or isinstance(amount, bool):
            return self._error_result(
                call, "InvalidToolArguments", "invalid refund arguments", started
            )
        orders = cast("dict[str, JsonValue]", self._state["orders"])
        order = orders.get(order_id)
        if not isinstance(order, dict):
            return self._error_result(call, "OrderNotFound", f"order {order_id} not found", started)
        if (
            "refund:own_order" not in call.authorization.scopes
            or call.authorization.customer_id != order["customer_id"]
        ):
            return self._error_result(call, "AuthorizationDenied", "refund not authorized", started)
        captured = order["captured_minor"]
        refunded = order["refunded_minor"]
        if not isinstance(captured, int) or not isinstance(refunded, int) or amount <= 0:
            return self._error_result(
                call, "InvalidRefund", "invalid refund state or amount", started
            )
        if refunded + amount > captured:
            return self._error_result(
                call, "RefundExceedsCaptured", "refund exceeds captured amount", started
            )
        before = deepcopy(order)
        order["refunded_minor"] = refunded + amount
        refund_id = _stable_id("ref", f"{self._run_id}:{call.id}")
        refund: dict[str, JsonValue] = {
            "id": refund_id,
            "order_id": order_id,
            "amount_minor": amount,
            "currency": "USD",
        }
        refunds = cast("list[JsonValue]", self._state["refunds"])
        refunds.append(refund)
        self._clock += timedelta(milliseconds=1)
        event = WorldEvent(
            id=EventId(_stable_id("evt", f"{self._run_id}:{len(self._events) + 1}")),
            run_id=self._run_id,
            sequence=len(self._events) + 1,
            occurred_at=self._clock,
            event_type="refund.issued",
            entity=EntityRef(type="refund", id=refund_id),
            actor_id=call.authorization.actor_id,
            request_id=call.id,
            authorization=call.authorization,
            before=before,
            after=refund,
        )
        self._events.append(event)
        return ToolResult(
            call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={"refund_id": refund_id},
            started_at=started,
            ended_at=self._clock,
        )

    def _error_result(
        self, call: ToolCall, error_type: str, message: str, started: datetime
    ) -> ToolResult:
        self._clock += timedelta(milliseconds=1)
        return ToolResult(
            call_id=call.id,
            status=ToolResultStatus.ERROR,
            error_type=error_type,
            error_message=message,
            started_at=started,
            ended_at=self._clock,
        )

    async def snapshot(self) -> WorldSnapshot:
        """Capture canonical current state."""
        if self._fail_snapshot:
            raise InfrastructureError("stub snapshot failure")
        return WorldSnapshot(
            world_name="stub-commerce",
            world_version=self.version,
            run_id=self._run_id,
            captured_at=self._clock,
            sequence=len(self._events),
            state=deepcopy(self._state),
        )

    async def events(self, after_sequence: int = 0) -> list[WorldEvent]:
        """Return append-only events after the given sequence."""
        return [event for event in self._events if event.sequence > after_sequence]

    async def advance_time(self, delta: timedelta) -> list[WorldEvent]:
        """Advance the controlled clock without wall-clock sleeping."""
        if delta.total_seconds() < 0:
            raise ValueError("world time cannot move backwards")
        self._clock += delta
        return []

    async def close(self) -> None:
        """Release stub resources."""
        self._closed = True

    @property
    def closed(self) -> bool:
        """Expose cleanup state for lifecycle conformance tests."""
        return self._closed


class StubWorkerAdapter:
    """Scripted adapter supporting success, unauthorized, timeout, and tool error modes."""

    name = "stub"
    worker_version = "1.0"

    def __init__(self) -> None:
        """Create a fresh scripted adapter."""
        self._scenario: Scenario | None = None
        self._turn_index = 0
        self._cancelled = False
        self._tool_names: set[str] = set()

    async def start(self, scenario: Scenario, tools: list[ToolSpec]) -> None:
        """Start a fresh scripted worker run."""
        self._scenario = scenario
        self._turn_index = 0
        self._cancelled = False
        await self.expose_tools(tools)

    async def expose_tools(self, tools: list[ToolSpec]) -> None:
        """Accept normalized world tool declarations."""
        if not tools:
            raise ValueError("stub worker requires at least one tool")
        self._tool_names = {tool.name for tool in tools}

    async def next_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        """Return the next normalized scripted decision."""
        if self._scenario is None:
            raise RuntimeError("worker has not been started")
        behavior = self._scenario.metadata.get("stub_behavior", "success")
        if behavior == "timeout":
            await asyncio.sleep(self._scenario.limits.wall_time_s * 2)
        now = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=self._turn_index)
        turn_id = TurnId(_stable_id("turn", f"{self._scenario.id}:{self._turn_index}"))
        if self._turn_index == 0:
            scopes = frozenset() if behavior == "unauthorized" else frozenset({"refund:own_order"})
            tool_name = "issue_refund" if "issue_refund" in self._tool_names else "refund_order"
            arguments: dict[str, JsonValue] = {"order_id": "ord_900", "amount_minor": 2499}
            if tool_name == "issue_refund":
                arguments.update({"currency": "USD", "idempotency_key": "stub-refund-1"})
            if behavior == "tool_error":
                arguments["force_error"] = True
            call = ToolCall(
                id=CallId(_stable_id("call", f"{self._scenario.id}:0")),
                run_id=RunId("pending"),
                tool_name=tool_name,
                arguments=arguments,
                authorization=AuthorizationContext(
                    actor_id="stub-worker", customer_id="cus_102", scopes=scopes
                ),
                requested_at=now,
            )
            self._turn_index += 1
            return WorkerTurn(id=turn_id, index=0, occurred_at=now, tool_call=call)
        self._turn_index += 1
        return WorkerTurn(
            id=turn_id,
            index=1,
            occurred_at=now,
            message="done",
            tool_result=tool_result,
            terminal=True,
        )

    def is_terminal(self, turn: WorkerTurn) -> bool:
        """Identify an explicit normalized terminal turn."""
        return turn.terminal

    async def cancel(self) -> None:
        """Cancel pending scripted work."""
        self._cancelled = True
