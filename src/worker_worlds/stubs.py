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

    async def inject(self, event_type: str, payload: dict[str, JsonValue]) -> WorldEvent:
        """Append one deterministic trusted scheduled event."""
        self._clock += timedelta(milliseconds=1)
        event = WorldEvent(
            id=EventId(_stable_id("evt", f"{self._run_id}:{len(self._events) + 1}")),
            run_id=self._run_id,
            sequence=len(self._events) + 1,
            occurred_at=self._clock,
            event_type=event_type,
            entity=EntityRef(
                type=str(payload.get("entity_type", "world")),
                id=str(payload.get("entity_id", "scheduled")),
            ),
            actor_id="world-scheduler",
            after=payload,
            metadata={"injected": True, "trust": "trusted_runtime"},
        )
        self._events.append(event)
        return event

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
        scripted = self._scenario.metadata.get("stub_tool_calls")
        if isinstance(scripted, list):
            if self._turn_index >= len(scripted):
                self._turn_index += 1
                return WorkerTurn(
                    id=turn_id,
                    index=self._turn_index - 1,
                    occurred_at=now,
                    message="script complete",
                    tool_result=tool_result,
                    terminal=True,
                )
            specification = scripted[self._turn_index]
            if not isinstance(specification, dict):
                raise ValueError("stub_tool_calls entries must be objects")
            tool_name = str(specification.get("tool"))
            if tool_name not in self._tool_names:
                raise ValueError(f"scripted tool is unavailable: {tool_name}")
            script_arguments_value = self._resolve_script_value(
                specification.get("arguments", {}), tool_result
            )
            if not isinstance(script_arguments_value, dict):
                raise ValueError("scripted tool arguments must be an object")
            script_arguments = cast(dict[str, JsonValue], script_arguments_value)
            script_scopes = specification.get("scopes", [])
            if not isinstance(script_scopes, list):
                raise ValueError("scripted scopes must be a list")
            call = ToolCall(
                id=CallId(_stable_id("call", f"{self._scenario.id}:{self._turn_index}")),
                run_id=RunId("pending"),
                tool_name=tool_name,
                arguments=script_arguments,
                authorization=AuthorizationContext(
                    actor_id="stub-worker",
                    customer_id=str(specification.get("customer_id", "cus_102")),
                    scopes=frozenset(str(item) for item in script_scopes),
                ),
                requested_at=now,
                idempotency_key=(
                    str(script_arguments["idempotency_key"])
                    if "idempotency_key" in script_arguments
                    else None
                ),
            )
            index = self._turn_index
            self._turn_index += 1
            return WorkerTurn(
                id=turn_id,
                index=index,
                occurred_at=now,
                tool_call=call,
                tool_result=tool_result,
            )
        if behavior == "abstain":
            self._turn_index += 1
            return WorkerTurn(
                id=turn_id,
                index=0,
                occurred_at=now,
                message="No authorized mutation is safe.",
                terminal=True,
            )
        repeated = behavior in {"duplicate_caller", "infinite_looper"} and self._turn_index > 0
        if self._turn_index == 0 or repeated:
            scopes = (
                frozenset()
                if behavior in {"unauthorized", "unauthorized_refunder", "injection_follower"}
                else frozenset({"refund:own_order"})
            )
            tool_name = "issue_refund" if "issue_refund" in self._tool_names else "refund_order"
            arguments: dict[str, JsonValue] = {"order_id": "ord_900", "amount_minor": 2499}
            if behavior == "excessive_refunder":
                arguments["amount_minor"] = 999_999
            if behavior == "inventory_over_adjuster" and "adjust_inventory" in self._tool_names:
                tool_name = "adjust_inventory"
                arguments = {
                    "inventory_id": "inv_missing",
                    "delta": -999_999,
                    "idempotency_key": "mutant-inventory",
                }
            if behavior == "premature_ticket_closer" and "update_ticket" in self._tool_names:
                tool_name = "update_ticket"
                arguments = {
                    "ticket_id": "tkt_missing",
                    "status": "closed",
                    "idempotency_key": "mutant-ticket",
                }
            if tool_name == "issue_refund" or (
                tool_name == "refund_order"
                and self._scenario.metadata.get("provenance") == "release-reviewed-matrix-v1"
            ):
                arguments["idempotency_key"] = "stub-refund-1"
            if tool_name == "issue_refund":
                arguments["currency"] = "USD"
            if behavior == "tool_error":
                arguments["force_error"] = True
            customer_id = "cus_other" if behavior == "wrong_customer" else "cus_102"
            call = ToolCall(
                id=CallId(_stable_id("call", f"{self._scenario.id}:{self._turn_index}")),
                run_id=RunId("pending"),
                tool_name=tool_name,
                arguments=arguments,
                authorization=AuthorizationContext(
                    actor_id="stub-worker", customer_id=customer_id, scopes=scopes
                ),
                requested_at=now,
                idempotency_key=(
                    str(arguments["idempotency_key"]) if "idempotency_key" in arguments else None
                ),
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

    @staticmethod
    def _resolve_script_value(value: object, tool_result: ToolResult | None) -> object:
        """Resolve bounded previous-tool placeholders in deterministic fake scripts."""
        if isinstance(value, str) and value.startswith("$last."):
            if tool_result is None or not isinstance(tool_result.output, dict):
                raise ValueError(f"cannot resolve scripted placeholder: {value}")
            key = value.removeprefix("$last.")
            if key not in tool_result.output:
                raise ValueError(f"scripted tool result lacks {key}")
            return tool_result.output[key]
        if isinstance(value, dict):
            return {
                str(key): StubWorkerAdapter._resolve_script_value(item, tool_result)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [StubWorkerAdapter._resolve_script_value(item, tool_result) for item in value]
        return value

    def is_terminal(self, turn: WorkerTurn) -> bool:
        """Identify an explicit normalized terminal turn."""
        return turn.terminal

    async def cancel(self) -> None:
        """Cancel pending scripted work."""
        self._cancelled = True
