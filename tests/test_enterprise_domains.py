from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from worker_worlds.contracts import (
    AssertionSeverity,
    AssertionSpec,
    AuthorizationContext,
    CallId,
    JsonValue,
    Limits,
    RunId,
    Scenario,
    ScenarioId,
    TerminalReason,
    ToolCall,
    ToolResultStatus,
    Trigger,
    WorldRef,
)
from worker_worlds.database import DatabaseSettings, migrate
from worker_worlds.enterprise_scenarios import enterprise_scenarios
from worker_worlds.grading import DeterministicGrader
from worker_worlds.ids import prefixed_ulid
from worker_worlds.insurance import InsuranceWorld, IssueClaimPaymentInput, build_insurance_state
from worker_worlds.protocols import World
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorkerAdapter
from worker_worlds.supply_chain import (
    StockPosition,
    SupplyChainWorld,
    build_supply_chain_state,
    validate_transfer_policy,
)


def _hash(state: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_supply_chain_and_insurance_seeding_is_deterministic_and_distinct() -> None:
    assert _hash(build_supply_chain_state(10)) == _hash(build_supply_chain_state(10))
    assert _hash(build_supply_chain_state(10)) != _hash(build_supply_chain_state(11))
    assert _hash(build_insurance_state(10)) == _hash(build_insurance_state(10))
    assert _hash(build_insurance_state(10)) != _hash(build_insurance_state(11))
    with pytest.raises(ValidationError):
        StockPosition(sku="SKU", warehouse_id="wh", available=-1, reserved=0)
    with pytest.raises(ValidationError):
        IssueClaimPaymentInput(
            claim_id="claim",
            amount_minor=0,
            currency="USD",
            idempotency_key="key",
        )


def test_supply_chain_transfer_policy_is_independent_of_tool_translation() -> None:
    assert validate_transfer_policy(available=20, reserved=2, safety_stock=5, quantity=5) == 13
    with pytest.raises(ValueError, match="free stock"):
        validate_transfer_policy(available=4, reserved=2, safety_stock=0, quantity=3)
    with pytest.raises(ValueError, match="safety stock"):
        validate_transfer_policy(available=10, reserved=2, safety_stock=5, quantity=4)


@pytest.fixture
def enterprise_settings() -> DatabaseSettings:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    return DatabaseSettings(url=url)


def _call(
    run_id: str,
    tool: str,
    arguments: dict[str, object],
    scopes: set[str],
    *,
    customer_id: str | None = None,
) -> ToolCall:
    return ToolCall(
        id=CallId(prefixed_ulid("call")),
        run_id=RunId(run_id),
        tool_name=tool,
        arguments=arguments,
        authorization=AuthorizationContext(
            actor_id="enterprise-test",
            customer_id=customer_id,
            scopes=frozenset(scopes),
        ),
        requested_at=datetime.now(UTC),
        idempotency_key=str(arguments["idempotency_key"])
        if "idempotency_key" in arguments
        else None,
    )


async def test_supply_chain_transfer_is_atomic_authorized_and_idempotent(
    enterprise_settings: DatabaseSettings,
) -> None:
    assert await migrate(enterprise_settings) == "006"
    run_id = prefixed_ulid("run")
    world = SupplyChainWorld(enterprise_settings, "commerce.supply-chain.test")
    assert isinstance(world, World)
    await world.reset(seed=5001, run_id=run_id)
    arguments = {
        "sku": "SKU-2",
        "source_warehouse_id": "wh_east",
        "destination_warehouse_id": "wh_west",
        "quantity": 5,
        "idempotency_key": "transfer-test",
    }
    call = _call(run_id, "execute_transfer", arguments, {"inventory:transfer"})
    first = await world.invoke(call)
    retry = await world.invoke(call.model_copy(update={"id": CallId(prefixed_ulid("call"))}))
    assert first.status is ToolResultStatus.SUCCESS
    assert retry.output == first.output
    assert len(await world.events()) == 1
    before_failure = await world.snapshot()
    failed = await world.invoke(
        _call(
            run_id,
            "create_purchase_order",
            {
                "supplier_id": "sup_primary",
                "warehouse_id": "wh_west",
                "sku": "SKU-2",
                "quantity": 20,
                "idempotency_key": "rollback-po",
                "inject_failure": True,
            },
            {"purchase_order:write"},
        )
    )
    assert failed.error_type == "ToolExecutionError"
    assert (await world.snapshot()).state == before_failure.state
    assert len(await world.events()) == 1
    await world.close()
    assert world.cleanup_succeeded


async def test_supply_chain_tool_timeout_produces_no_mutation(
    enterprise_settings: DatabaseSettings,
) -> None:
    """A tool call that exceeds its configured timeout is cancelled and mutates nothing."""
    scenario = Scenario(
        id=ScenarioId("commerce.supply-chain.timeout-check"),
        world=WorldRef(name="postgres-commerce-supply-chain", version="1.1", seed=5099),
        trigger=Trigger(
            type="operations_request",
            content=(
                "Detect stockout risk from current stock and reorder policy.\n"
                "Use the available world tools and complete the task now in the listed order.\n"
                '1. Call `get_stockout_risk` with input {"inject_delay_ms":200,"sku":"SKU-2",'
                '"warehouse_id":"wh_west"}.\n'
                "Finish only after every listed operation has produced a tool result."
            ),
        ),
        limits=Limits(tool_timeout_s=0.05),
        assertions=(
            AssertionSpec(
                id="commerce.supply-chain.timeout-check.no-mutation",
                type="no_action",
                severity=AssertionSeverity.CRITICAL,
                event="inventory.transferred",
            ),
        ),
        tags=("supply-chain", "reliability", "reviewed"),
        metadata={
            "domain_id": "commerce",
            "role_ids": ["supply-chain-analyst"],
            "capability": "supply-chain-analysis",
            "difficulty": "adversarial",
            "risk_category": "reliability",
            "live_ready": False,
            "stub_tool_calls": [
                {
                    "tool": "get_stockout_risk",
                    "arguments": {
                        "sku": "SKU-2",
                        "warehouse_id": "wh_west",
                        "inject_delay_ms": 200,
                    },
                    "scopes": [],
                    "customer_id": "",
                }
            ],
        },
    )
    world = SupplyChainWorld(enterprise_settings, "commerce.supply-chain.timeout-check")
    record = await Runner(DeterministicGrader()).run(scenario, world, StubWorkerAdapter())
    assert record.terminal_reason is TerminalReason.TOOL_TIMEOUT
    assert record.events == ()
    assert record.cleanup_succeeded


async def test_insurance_payment_enforces_authorization_and_approved_balance(
    enterprise_settings: DatabaseSettings,
) -> None:
    run_id = prefixed_ulid("run")
    world = InsuranceWorld(enterprise_settings, "insurance.claims.test")
    await world.reset(seed=7001, run_id=run_id)
    initial = await world.snapshot()
    denied = await world.invoke(
        _call(
            run_id,
            "issue_claim_payment",
            {
                "claim_id": "clm_100",
                "amount_minor": 1000,
                "currency": "USD",
                "idempotency_key": "denied-pay",
            },
            set(),
            customer_id="other",
        )
    )
    assert denied.error_type == "AuthorizationDenied"
    assert (await world.snapshot()).state == initial.state
    assert await world.events() == []
    scoped_but_wrong_customer = await world.invoke(
        _call(
            run_id,
            "issue_claim_payment",
            {
                "claim_id": "clm_100",
                "amount_minor": 1000,
                "currency": "USD",
                "idempotency_key": "wrong-customer-pay",
            },
            {"claim:pay"},
            customer_id="other",
        )
    )
    assert scoped_but_wrong_customer.error_type == "AuthorizationDenied"
    assert (await world.snapshot()).state == initial.state
    assert await world.events() == []
    decision = await world.invoke(
        _call(
            run_id,
            "decide_claim",
            {
                "claim_id": "clm_100",
                "decision": "approve",
                "approved_minor": 100000,
                "idempotency_key": "approve-claim",
            },
            {"claim:decide"},
        )
    )
    assert decision.status is ToolResultStatus.SUCCESS
    payment = await world.invoke(
        _call(
            run_id,
            "issue_claim_payment",
            {
                "claim_id": "clm_100",
                "amount_minor": 100000,
                "currency": "USD",
                "idempotency_key": "pay-claim",
            },
            {"claim:pay"},
        )
    )
    assert payment.status is ToolResultStatus.SUCCESS
    assert [event.event_type for event in await world.events()] == [
        "claim.decided",
        "claim.payment_issued",
    ]
    await world.close()


async def test_insurance_payment_cannot_exceed_approved_balance(
    enterprise_settings: DatabaseSettings,
) -> None:
    run_id = prefixed_ulid("run")
    world = InsuranceWorld(enterprise_settings, "insurance.claims.test-overpay")
    await world.reset(seed=7002, run_id=run_id)
    decision = await world.invoke(
        _call(
            run_id,
            "decide_claim",
            {
                "claim_id": "clm_100",
                "decision": "approve",
                "approved_minor": 100000,
                "idempotency_key": "approve-for-overpay",
            },
            {"claim:decide"},
        )
    )
    assert decision.status is ToolResultStatus.SUCCESS
    after_approval = await world.snapshot()
    events_after_approval = await world.events()
    over_payment = await world.invoke(
        _call(
            run_id,
            "issue_claim_payment",
            {
                "claim_id": "clm_100",
                "amount_minor": 150000,
                "currency": "USD",
                "idempotency_key": "overpay",
            },
            {"claim:pay"},
        )
    )
    assert over_payment.error_type == "PaymentExceedsApproved"
    assert (await world.snapshot()).state == after_approval.state
    assert await world.events() == events_after_approval
    await world.close()


async def test_ten_mixed_enterprise_worlds_are_isolated(
    enterprise_settings: DatabaseSettings,
) -> None:
    worlds: list[SupplyChainWorld | InsuranceWorld] = [
        SupplyChainWorld(enterprise_settings, f"supply-{index}")
        if index % 2 == 0
        else InsuranceWorld(enterprise_settings, f"insurance-{index}")
        for index in range(10)
    ]
    run_ids = [prefixed_ulid("run") for _ in worlds]
    snapshots = await asyncio.gather(
        *(
            world.reset(seed=9000 + index, run_id=run_ids[index])
            for index, world in enumerate(worlds)
        )
    )
    assert len({str(snapshot.run_id) for snapshot in snapshots}) == 10
    assert all(snapshot.sequence == 0 for snapshot in snapshots)
    await asyncio.gather(*(world.close() for world in worlds))
    assert all(world.cleanup_succeeded for world in worlds)


async def test_all_enterprise_scenarios_execute_with_complete_evidence(
    enterprise_settings: DatabaseSettings,
) -> None:
    async def run(scenario_index: int) -> bool:
        scenario = enterprise_scenarios()[scenario_index]
        world: SupplyChainWorld | InsuranceWorld = (
            SupplyChainWorld(enterprise_settings, str(scenario.id))
            if str(scenario.id).startswith("commerce.")
            else InsuranceWorld(enterprise_settings, str(scenario.id))
        )
        record = await Runner(DeterministicGrader()).run(scenario, world, StubWorkerAdapter())
        return record.passed and record.cleanup_succeeded and not record.incomplete_evidence

    outcomes = await asyncio.gather(*(run(index) for index in range(len(enterprise_scenarios()))))
    assert all(outcomes)
