from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from worker_worlds.database import DatabaseSettings, connect, migrate
from worker_worlds.errors import InfrastructureError
from worker_worlds.ids import prefixed_ulid
from worker_worlds.postgres_world import PostgresWorld, snapshot_hash
from worker_worlds.protocols import World


@pytest.fixture(scope="session")
def postgres_settings() -> DatabaseSettings:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    settings.validate()
    return settings


@pytest.fixture(autouse=True, scope="session")
async def migrated(postgres_settings: DatabaseSettings) -> None:
    assert await migrate(postgres_settings) == "006"


def _run_id() -> str:
    return prefixed_ulid("run")


def _call(
    run_id: str,
    *,
    amount: int = 2499,
    currency: str = "USD",
    customer: str = "cus_102",
    key: str = "refund-key",
    inject_failure: bool = False,
) -> ToolCall:
    return ToolCall(
        id=CallId(prefixed_ulid("call")),
        run_id=RunId(run_id),
        tool_name="issue_refund",
        arguments={
            "order_id": "ord_900",
            "amount_minor": amount,
            "currency": currency,
            "idempotency_key": key,
            "inject_failure": inject_failure,
        },
        authorization=AuthorizationContext(
            actor_id="test-worker",
            customer_id=customer,
            scopes=frozenset({"refund:own_order"}),
        ),
        requested_at=datetime.now(UTC),
        idempotency_key=key,
    )


def _mutation_call(
    run_id: str, tool_name: str, arguments: dict[str, JsonValue], scopes: set[str]
) -> ToolCall:
    return ToolCall(
        id=CallId(prefixed_ulid("call")),
        run_id=RunId(run_id),
        tool_name=tool_name,
        arguments=arguments,
        authorization=AuthorizationContext(
            actor_id="test-worker", customer_id="cus_102", scopes=frozenset(scopes)
        ),
        requested_at=datetime.now(UTC),
        idempotency_key=str(arguments.get("idempotency_key", "")) or None,
    )


async def _world(settings: DatabaseSettings, seed: int = 4) -> tuple[PostgresWorld, str]:
    run_id = _run_id()
    world = PostgresWorld(settings, "test.refund")
    await world.reset(seed=seed, run_id=run_id)
    return world, run_id


async def test_postgres_world_conforms(postgres_settings: DatabaseSettings) -> None:
    world, _ = await _world(postgres_settings)
    try:
        assert isinstance(world, World)
    finally:
        await world.close()


async def test_deterministic_reset(postgres_settings: DatabaseSettings) -> None:
    first, second, different = await asyncio.gather(
        _world(postgres_settings, 10), _world(postgres_settings, 10), _world(postgres_settings, 11)
    )
    try:
        first_snapshot, second_snapshot, different_snapshot = await asyncio.gather(
            first[0].snapshot(), second[0].snapshot(), different[0].snapshot()
        )
        assert snapshot_hash(first_snapshot) == snapshot_hash(second_snapshot)
        assert snapshot_hash(first_snapshot) != snapshot_hash(different_snapshot)
    finally:
        await asyncio.gather(first[0].close(), second[0].close(), different[0].close())


@pytest.mark.parametrize(
    ("amount", "currency", "customer", "error"),
    [
        (0, "USD", "cus_102", "ToolValidationError"),
        (-1, "USD", "cus_102", "ToolValidationError"),
        (10001, "USD", "cus_102", "RefundExceedsBalance"),
        (100, "EUR", "cus_102", "CurrencyMismatch"),
        (100, "USD", "cus_other", "AuthorizationDenied"),
    ],
)
async def test_refund_rejections_are_atomic(
    postgres_settings: DatabaseSettings,
    amount: int,
    currency: str,
    customer: str,
    error: str,
) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        before = await world.snapshot()
        result = await world.invoke(
            _call(run_id, amount=amount, currency=currency, customer=customer)
        )
        after = await world.snapshot()
        assert result.status is ToolResultStatus.ERROR
        assert result.error_type == error
        assert snapshot_hash(before) == snapshot_hash(after)
        assert await world.events() == []
    finally:
        await world.close()


@pytest.mark.parametrize("amount", [2499, 10000])
async def test_partial_and_full_refund(postgres_settings: DatabaseSettings, amount: int) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        result = await world.invoke(_call(run_id, amount=amount))
        assert result.status is ToolResultStatus.SUCCESS
        assert len(await world.events()) == 1
        snapshot = await world.snapshot()
        refunds = snapshot.state["refunds"]
        assert isinstance(refunds, list)
        refund = refunds[0]
        assert isinstance(refund, dict)
        assert refund["amount_minor"] == amount
    finally:
        await world.close()


async def test_identical_and_conflicting_idempotency(postgres_settings: DatabaseSettings) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        first = await world.invoke(_call(run_id, key="same"))
        retry = await world.invoke(_call(run_id, key="same"))
        conflict = await world.invoke(_call(run_id, amount=100, key="same"))
        assert retry.output == first.output
        assert conflict.error_type == "IdempotencyConflict"
        assert len(await world.events()) == 1
    finally:
        await world.close()


async def test_idempotency_never_crosses_tool_or_authorization_boundary(
    postgres_settings: DatabaseSettings,
) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        first = await world.invoke(_call(run_id, key="bounded"))
        unauthorized = await world.invoke(_call(run_id, key="bounded", customer="cus_other"))
        cross_tool = await world.invoke(
            _mutation_call(
                run_id,
                "create_ticket",
                {
                    "customer_id": "cus_102",
                    "order_id": "ord_900",
                    "subject": "duplicate-key probe",
                    "idempotency_key": "bounded",
                },
                {"ticket:create"},
            )
        )
        assert first.status is ToolResultStatus.SUCCESS
        assert unauthorized.error_type == "AuthorizationDenied"
        assert cross_tool.error_type == "IdempotencyConflict"
        assert len(await world.events()) == 1
    finally:
        await world.close()


async def test_forced_failure_rolls_back_mutation_and_event(
    postgres_settings: DatabaseSettings,
) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        before = await world.snapshot()
        result = await world.invoke(_call(run_id, inject_failure=True))
        assert result.status is ToolResultStatus.ERROR
        assert result.error_type == "ToolExecutionError"
        assert snapshot_hash(before) == snapshot_hash(await world.snapshot())
        assert await world.events() == []
    finally:
        await world.close()


async def test_gateway_rejects_unprivileged_inventory_mutation_and_envelope_mismatch(
    postgres_settings: DatabaseSettings,
) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        before = await world.snapshot()
        unauthorized = await world.invoke(
            _mutation_call(
                run_id,
                "adjust_inventory",
                {"sku": "SKU-2", "delta": 1, "idempotency_key": "no-authority"},
                set(),
            )
        )
        mismatched = _call(run_id, key="argument-key").model_copy(
            update={"idempotency_key": "envelope-key"}
        )
        mismatch_result = await world.invoke(mismatched)
        after = await world.snapshot()
        assert unauthorized.error_type == "AuthorizationDenied"
        assert mismatch_result.error_type == "IdempotencyEnvelopeMismatch"
        assert before.state == after.state
        assert await world.events() == []
    finally:
        await world.close()


async def test_concurrent_duplicate_is_one_mutation(postgres_settings: DatabaseSettings) -> None:
    world, run_id = await _world(postgres_settings)
    try:
        first, second = await asyncio.gather(
            world.invoke(_call(run_id, key="concurrent")),
            world.invoke(_call(run_id, key="concurrent")),
        )
        assert first.output == second.output
        assert len(await world.events()) == 1
    finally:
        await world.close()


async def test_ten_run_parallel_isolation(postgres_settings: DatabaseSettings) -> None:
    worlds = await asyncio.gather(*[_world(postgres_settings, index) for index in range(10)])
    try:
        results = await asyncio.gather(
            *(
                world.invoke(_call(run_id, key=f"key-{index}"))
                for index, (world, run_id) in enumerate(worlds)
            )
        )
        assert all(result.status is ToolResultStatus.SUCCESS for result in results)
        event_sets = await asyncio.gather(*(world.events() for world, _ in worlds))
        assert all([event.sequence for event in events] == [1] for events in event_sets)
        assert len({events[0].run_id for events in event_sets}) == 10
        await worlds[0][0].close()
        assert (await worlds[1][0].snapshot()).sequence == 1
    finally:
        await asyncio.gather(*(world.close() for world, _ in worlds))


async def test_lease_renews_and_stops_on_cleanup(postgres_settings: DatabaseSettings) -> None:
    settings = replace(postgres_settings, lease_seconds=0.2, lease_renewal_seconds=0.02)
    world, run_id = await _world(settings)
    connection = await connect(settings)
    try:
        before = await connection.fetchval(
            "SELECT expires_at FROM worker_worlds.run_leases WHERE run_id=$1", run_id
        )
        await asyncio.sleep(0.05)
        after = await connection.fetchval(
            "SELECT expires_at FROM worker_worlds.run_leases WHERE run_id=$1", run_id
        )
        assert after > before
        assert world.lease_renewal_running
        await world.close()
        assert not world.lease_renewal_running
    finally:
        await connection.close()
        await world.close()


async def test_snapshot_size_limit_fails_without_truncation(
    postgres_settings: DatabaseSettings,
) -> None:
    settings = replace(postgres_settings, snapshot_max_bytes=1)
    world = PostgresWorld(settings, "test.snapshot.limit")
    try:
        with pytest.raises(InfrastructureError, match="snapshot size"):
            await world.reset(seed=4, run_id=_run_id())
    finally:
        await world.close()


async def test_controlled_time_stockout_injection_is_atomic(
    postgres_settings: DatabaseSettings,
) -> None:
    world, _ = await _world(postgres_settings, seed=71)
    try:
        before = await world.snapshot()
        inventory = before.state["inventory"]
        assert isinstance(inventory, list) and inventory
        first_inventory = inventory[0]
        assert isinstance(first_inventory, dict)
        inventory_id = str(first_inventory["id"])
        await world.advance_time(timedelta(hours=2))
        event = await world.inject(
            "inventory.stockout",
            {"entity_type": "inventory", "entity_id": inventory_id},
        )
        after = await world.snapshot()
        rows = after.state["inventory"]
        assert isinstance(rows, list)
        typed_rows = [row for row in rows if isinstance(row, dict)]
        changed = next(row for row in typed_rows if row["id"] == inventory_id)
        assert changed["available"] == 0
        assert event.event_type == "inventory.stockout"
        assert event.occurred_at > before.captured_at
        assert event.metadata["injected"] is True
    finally:
        await world.close()


async def test_specialized_mutations_are_atomic_idempotent_and_authorized(
    postgres_settings: DatabaseSettings,
) -> None:
    world, run_id = await _world(postgres_settings, seed=72)
    try:
        snapshot = await world.snapshot()
        shipments = snapshot.state["shipments"]
        assert isinstance(shipments, list)
        pending = next(
            row for row in shipments if isinstance(row, dict) and row["status"] == "pending"
        )
        shipment = _mutation_call(
            run_id,
            "update_shipment",
            {"shipment_id": pending["id"], "status": "shipped", "idempotency_key": "ship-1"},
            {"shipment:write"},
        )
        first, retry = await world.invoke(shipment), await world.invoke(shipment)
        assert first.output == retry.output

        replacement = await world.invoke(
            _mutation_call(
                run_id,
                "create_replacement",
                {"order_id": "ord_900", "sku": "SKU-2", "quantity": 1, "idempotency_key": "rpl-1"},
                {"replacement:create"},
            )
        )
        assert replacement.status is ToolResultStatus.SUCCESS
        denied = await world.invoke(
            _mutation_call(
                run_id,
                "expire_promotion",
                {"promotion_code": "SAVE10", "idempotency_key": "promo-1"},
                {"ticket:update"},
            )
        )
        assert denied.error_type == "AuthorizationDenied"
        assert [event.event_type for event in await world.events()] == [
            "shipment.updated",
            "replacement.created",
        ]
    finally:
        await world.close()


async def test_pending_refund_completion_and_concurrent_retry(
    postgres_settings: DatabaseSettings,
) -> None:
    world, run_id = await _world(postgres_settings, seed=73)
    try:
        pending_call = _call(run_id, amount=100, key="pending")
        pending_call = pending_call.model_copy(
            update={"arguments": {**pending_call.arguments, "processor_pending": True}}
        )
        pending = await world.invoke(pending_call)
        assert isinstance(pending.output, dict) and pending.output["status"] == "pending"
        refund_id = str(pending.output["refund_id"])
        completion = _mutation_call(
            run_id,
            "complete_refund",
            {"refund_id": refund_id, "idempotency_key": "complete-1"},
            {"refund:process"},
        )
        first, retry = await asyncio.gather(world.invoke(completion), world.invoke(completion))
        assert first.output == retry.output
        assert [event.event_type for event in await world.events()] == [
            "refund.pending",
            "refund.completed",
        ]
    finally:
        await world.close()


async def test_remaining_specialized_mutation_flows(
    postgres_settings: DatabaseSettings,
) -> None:
    world, run_id = await _world(postgres_settings, seed=74)
    try:
        snapshot = await world.snapshot()
        customers = snapshot.state["customers"]
        tickets = snapshot.state["tickets"]
        assert isinstance(customers, list) and isinstance(tickets, list)
        customer_ids = [str(row["id"]) for row in customers if isinstance(row, dict)]
        ticket_id = str(next(row["id"] for row in tickets if isinstance(row, dict)))
        calls = [
            _mutation_call(
                run_id,
                "resolve_backorder",
                {
                    "sku": "SKU-2",
                    "location": "secondary",
                    "quantity": 1,
                    "idempotency_key": "back-1",
                },
                {"inventory:write"},
            ),
            _mutation_call(
                run_id,
                "expire_promotion",
                {"promotion_code": "SAVE10", "idempotency_key": "promo-1"},
                {"promotion:write"},
            ),
            _mutation_call(
                run_id,
                "disambiguate_customer",
                {
                    "selected_customer_id": "cus_102",
                    "candidate_ids": cast(list[JsonValue], customer_ids),
                    "idempotency_key": "customer-1",
                },
                {"customer:disambiguate"},
            ),
            _mutation_call(
                run_id,
                "transfer_inventory",
                {
                    "sku": "SKU-2",
                    "source_location": "default",
                    "destination_location": "secondary",
                    "quantity": 1,
                    "idempotency_key": "transfer-1",
                },
                {"inventory:write"},
            ),
            _mutation_call(
                run_id,
                "cancel_order",
                {"order_id": "ord_cancel", "idempotency_key": "cancel-1"},
                {"order:cancel"},
            ),
            _mutation_call(
                run_id,
                "update_ticket",
                {"ticket_id": ticket_id, "status": "closed", "idempotency_key": "close-1"},
                {"ticket:update"},
            ),
            _mutation_call(
                run_id,
                "reopen_ticket",
                {
                    "ticket_id": ticket_id,
                    "reason": "customer replied",
                    "idempotency_key": "reopen-1",
                },
                {"ticket:reopen"},
            ),
        ]
        for call in calls:
            result = await world.invoke(call)
            retry = await world.invoke(call)
            assert result.status is ToolResultStatus.SUCCESS
            assert retry.output == result.output
        invalid = await world.invoke(
            _mutation_call(
                run_id,
                "transfer_inventory",
                {
                    "sku": "SKU-2",
                    "source_location": "default",
                    "destination_location": "default",
                    "quantity": 1,
                    "idempotency_key": "bad-transfer",
                },
                {"inventory:write"},
            )
        )
        assert invalid.error_type == "InvalidTransfer"
        assert len(await world.events()) == len(calls)
    finally:
        await world.close()
