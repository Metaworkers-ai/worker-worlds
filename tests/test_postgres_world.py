from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
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
    assert await migrate(postgres_settings) == "002"


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
