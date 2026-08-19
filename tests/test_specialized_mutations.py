from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import cast

import pytest

from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
    JsonValue,
    RunId,
    ToolCall,
    ToolResultStatus,
)
from worker_worlds.database import DatabaseSettings, migrate
from worker_worlds.ids import prefixed_ulid
from worker_worlds.postgres_world import PostgresWorld, snapshot_hash

FLOWS = (
    "create_replacement",
    "resolve_backorder",
    "update_shipment",
    "expire_promotion",
    "disambiguate_customer",
    "transfer_inventory",
    "cancel_order",
    "complete_refund",
    "reopen_ticket",
)


@pytest.fixture(scope="session")
def specialized_settings() -> DatabaseSettings:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    settings.validate()
    return settings


@pytest.fixture(autouse=True, scope="session")
async def specialized_migrations(specialized_settings: DatabaseSettings) -> None:
    assert await migrate(specialized_settings) == "003"


async def _new_world(settings: DatabaseSettings, flow: str) -> tuple[PostgresWorld, str]:
    run_id = prefixed_ulid("run")
    world = PostgresWorld(settings, f"matrix.{flow}")
    await world.reset(seed=8100 + FLOWS.index(flow), run_id=run_id)
    return world, run_id


def _call(
    run_id: str,
    tool: str,
    arguments: dict[str, JsonValue],
    scopes: set[str],
) -> ToolCall:
    return ToolCall(
        id=CallId(prefixed_ulid("call")),
        run_id=RunId(run_id),
        tool_name=tool,
        arguments=arguments,
        authorization=AuthorizationContext(
            actor_id="matrix-worker", customer_id="cus_102", scopes=frozenset(scopes)
        ),
        requested_at=datetime.now(UTC),
        idempotency_key=str(arguments.get("idempotency_key", "")) or None,
    )


async def _prepared_call(
    world: PostgresWorld,
    run_id: str,
    flow: str,
    key: str,
    *,
    inject_failure: bool = False,
) -> ToolCall:
    snapshot = await world.snapshot()
    arguments: dict[str, JsonValue]
    scopes: set[str]
    if flow == "create_replacement":
        arguments, scopes = (
            {"order_id": "ord_900", "sku": "SKU-2", "quantity": 1},
            {"replacement:create"},
        )
    elif flow == "resolve_backorder":
        arguments, scopes = (
            {"sku": "SKU-2", "location": "secondary", "quantity": 1},
            {"inventory:write"},
        )
    elif flow == "update_shipment":
        rows = cast(list[JsonValue], snapshot.state["shipments"])
        pending = next(row for row in rows if isinstance(row, dict) and row["status"] == "pending")
        arguments, scopes = {"shipment_id": pending["id"], "status": "shipped"}, {"shipment:write"}
    elif flow == "expire_promotion":
        arguments, scopes = {"promotion_code": "MATRIX"}, {"promotion:write"}
    elif flow == "disambiguate_customer":
        rows = cast(list[JsonValue], snapshot.state["customers"])
        identifiers = [row["id"] for row in rows if isinstance(row, dict)]
        arguments, scopes = (
            {"selected_customer_id": "cus_102", "candidate_ids": identifiers},
            {"customer:disambiguate"},
        )
    elif flow == "transfer_inventory":
        arguments, scopes = (
            {
                "sku": "SKU-2",
                "source_location": "default",
                "destination_location": "secondary",
                "quantity": 1,
            },
            {"inventory:write"},
        )
    elif flow == "cancel_order":
        arguments, scopes = {"order_id": "ord_cancel"}, {"order:cancel"}
    elif flow == "complete_refund":
        pending_call = _call(
            run_id,
            "issue_refund",
            {
                "order_id": "ord_900",
                "amount_minor": 100,
                "currency": "USD",
                "idempotency_key": key + "-pending",
                "processor_pending": True,
            },
            {"refund:own_order"},
        )
        result = await world.invoke(pending_call)
        assert isinstance(result.output, dict)
        arguments, scopes = {"refund_id": result.output["refund_id"]}, {"refund:process"}
    else:
        rows = cast(list[JsonValue], snapshot.state["tickets"])
        ticket = next(row for row in rows if isinstance(row, dict))
        if ticket["status"] != "closed":
            close = _call(
                run_id,
                "update_ticket",
                {
                    "ticket_id": ticket["id"],
                    "status": "closed",
                    "idempotency_key": key + "-close",
                },
                set(),
            )
            assert (await world.invoke(close)).status is ToolResultStatus.SUCCESS
        arguments, scopes = (
            {"ticket_id": ticket["id"], "reason": "matrix reopen"},
            {"ticket:reopen"},
        )
    arguments["idempotency_key"] = key
    if inject_failure:
        arguments["inject_failure"] = True
    return _call(run_id, flow, arguments, scopes)


@pytest.mark.parametrize("flow", FLOWS)
async def test_specialized_success_retry_conflict_and_atomicity(
    specialized_settings: DatabaseSettings, flow: str
) -> None:
    world, run_id = await _new_world(specialized_settings, flow)
    try:
        call = await _prepared_call(world, run_id, flow, f"{flow}-success")
        before_events = len(await world.events())
        success = await world.invoke(call)
        retry = await world.invoke(call)
        assert success.status is ToolResultStatus.SUCCESS and retry.output == success.output
        assert len(await world.events()) == before_events + 1
        conflict_arguments = dict(call.arguments)
        conflict_arguments["inject_failure"] = True
        conflict = await world.invoke(call.model_copy(update={"arguments": conflict_arguments}))
        assert conflict.error_type == "IdempotencyConflict"
    finally:
        await world.close()
        assert world.cleanup_succeeded


@pytest.mark.parametrize("flow", FLOWS)
async def test_specialized_concurrent_identical_and_conflicting_calls(
    specialized_settings: DatabaseSettings, flow: str
) -> None:
    world, run_id = await _new_world(specialized_settings, flow)
    try:
        call = await _prepared_call(world, run_id, flow, f"{flow}-concurrent")
        before_events = len(await world.events())
        first, second = await asyncio.gather(world.invoke(call), world.invoke(call))
        assert first.status is ToolResultStatus.SUCCESS and first.output == second.output
        assert len(await world.events()) == before_events + 1
        changed = dict(call.arguments)
        changed["inject_failure"] = True
        left, right = await asyncio.gather(
            world.invoke(call), world.invoke(call.model_copy(update={"arguments": changed}))
        )
        assert {left.status, right.status} == {ToolResultStatus.SUCCESS, ToolResultStatus.ERROR}
    finally:
        await world.close()


@pytest.mark.parametrize("flow", FLOWS)
async def test_specialized_validation_authorization_and_injected_rollback(
    specialized_settings: DatabaseSettings, flow: str
) -> None:
    world, run_id = await _new_world(specialized_settings, flow)
    try:
        call = await _prepared_call(world, run_id, flow, f"{flow}-negative")
        invalid = await world.invoke(
            call.model_copy(update={"arguments": {**call.arguments, "unexpected": True}})
        )
        assert invalid.error_type == "ToolValidationError"
        denied = await world.invoke(
            call.model_copy(
                update={
                    "id": CallId(prefixed_ulid("call")),
                    "authorization": call.authorization.model_copy(update={"scopes": frozenset()}),
                }
            )
        )
        assert denied.error_type == "AuthorizationDenied"
        failing = await _prepared_call(world, run_id, flow, f"{flow}-rollback", inject_failure=True)
        before, event_count = await world.snapshot(), len(await world.events())
        failure = await world.invoke(failing)
        assert failure.error_type == "ToolExecutionError"
        assert snapshot_hash(await world.snapshot()) == snapshot_hash(before)
        assert len(await world.events()) == event_count
    finally:
        await world.close()


async def test_illegal_terminal_transitions_are_rejected(
    specialized_settings: DatabaseSettings,
) -> None:
    world, run_id = await _new_world(specialized_settings, "update_shipment")
    try:
        snapshot = await world.snapshot()
        rows = cast(list[JsonValue], snapshot.state["shipments"])
        delivered = next(
            row for row in rows if isinstance(row, dict) and row["status"] == "delivered"
        )
        result = await world.invoke(
            _call(
                run_id,
                "update_shipment",
                {
                    "shipment_id": delivered["id"],
                    "status": "shipped",
                    "idempotency_key": "terminal",
                },
                {"shipment:write"},
            )
        )
        assert result.error_type == "IllegalStatusTransition"
    finally:
        await world.close()
