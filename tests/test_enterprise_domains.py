from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

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
from worker_worlds.scenario_library import reviewed_scenarios
from worker_worlds.stubs import StubWorkerAdapter
from worker_worlds.supply_chain import (
    GetStockoutRiskInput,
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


def test_inject_delay_ms_is_bounded_and_never_activated_by_an_approved_scenario() -> None:
    """#20: inject_delay_ms is intentional test-only fault injection (see supply_chain.py),
    not a harness-owned channel. This proves a real/live worker cannot meaningfully abuse it:
    the field stays hard-capped at 60 seconds, and no scenario approved for live adapters
    ever asks a worker to set it."""
    field = GetStockoutRiskInput.model_fields["inject_delay_ms"]
    bounds = {constraint.__class__.__name__: constraint for constraint in field.metadata}
    assert bounds["Ge"].ge == 0
    assert bounds["Le"].le == 60_000

    live_ready_scenarios = [
        scenario
        for scenario in (*reviewed_scenarios(), *enterprise_scenarios())
        if scenario.metadata.get("live_ready") is True
    ]
    assert live_ready_scenarios, "expected at least one live-ready scenario to check"
    for scenario in live_ready_scenarios:
        assert "inject_delay_ms" not in scenario.trigger.content
        stub_tool_calls = scenario.metadata.get("stub_tool_calls", [])
        if isinstance(stub_tool_calls, list):
            for call in stub_tool_calls:
                if isinstance(call, dict):
                    arguments = call.get("arguments", {})
                    if isinstance(arguments, dict):
                        assert not arguments.get("inject_delay_ms")


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


async def test_insurance_payment_retry_is_idempotent_and_not_applied_twice(
    enterprise_settings: DatabaseSettings,
) -> None:
    run_id = prefixed_ulid("run")
    world = InsuranceWorld(enterprise_settings, "insurance.claims.test-payment-retry")
    await world.reset(seed=7003, run_id=run_id)
    decision = await world.invoke(
        _call(
            run_id,
            "decide_claim",
            {
                "claim_id": "clm_100",
                "decision": "approve",
                "approved_minor": 100000,
                "idempotency_key": "approve-for-retry",
            },
            {"claim:decide"},
        )
    )
    assert decision.status is ToolResultStatus.SUCCESS
    payment_arguments = {
        "claim_id": "clm_100",
        "amount_minor": 100000,
        "currency": "USD",
        "idempotency_key": "pay-retry-once",
    }
    first = await world.invoke(
        _call(run_id, "issue_claim_payment", payment_arguments, {"claim:pay"})
    )
    assert first.status is ToolResultStatus.SUCCESS
    after_first_payment = await world.snapshot()
    events_after_first_payment = await world.events()
    assert [event.event_type for event in events_after_first_payment] == [
        "claim.decided",
        "claim.payment_issued",
    ]
    # A retried request carries a new call ID (the worker/adapter transport layer never
    # reuses one) but the same idempotency_key and identical arguments -- exactly the
    # signal the world's idempotency table keys on.
    retry = await world.invoke(
        _call(
            run_id,
            "issue_claim_payment",
            payment_arguments,
            {"claim:pay"},
        )
    )
    assert retry.status is ToolResultStatus.SUCCESS
    assert retry.output == first.output
    after_retry = await world.snapshot()
    events_after_retry = await world.events()
    assert after_retry.state == after_first_payment.state
    assert events_after_retry == events_after_first_payment
    assert [event.event_type for event in events_after_retry] == [
        "claim.decided",
        "claim.payment_issued",
    ]
    claims = cast(list[dict[str, Any]], after_retry.state["claims"])
    paid_claim = next(item for item in claims if item["id"] == "clm_100")
    assert paid_claim["paid_minor"] == 100000
    payments = cast(list[dict[str, Any]], after_retry.state["payments"])
    assert len(payments) == 1
    await world.close()


def test_claims_analyst_fixture_seed_bands_are_deterministic_and_legacy_unchanged() -> None:
    """`build_insurance_state` stays pure per band; legacy claims-adjuster shape is untouched."""
    assert _hash(build_insurance_state(8001)) == _hash(build_insurance_state(8001))
    assert _hash(build_insurance_state(8001)) != _hash(build_insurance_state(8002))
    # Seeds below the analyst floor (8000) must still build the exact original
    # single-claim, single-document claims-adjuster fixture, byte for byte.
    legacy = build_insurance_state(7001)
    assert len(cast(list[object], legacy["claims"])) == 1
    assert legacy["documents"] == []
    assert "analyst_notes" in legacy and legacy["analyst_notes"] == []
    # Every analyst fixture band (baseline plus the boundary-condition bands) always
    # produces exactly two claims, the invariant every claims-analyst scenario assertion
    # relies on.
    for seed in (8001, 8501, 8601, 8701, 8801, 8901, 9001, 9101, 9201, 9301, 9401):
        analyst_state = build_insurance_state(seed)
        assert len(cast(list[object], analyst_state["claims"])) == 2, seed


async def test_claims_analyst_recommendation_and_flag_never_mutate_claim_state(
    enterprise_settings: DatabaseSettings,
) -> None:
    """A non-binding recommendation or risk flag must never move status/approved/paid."""
    run_id = prefixed_ulid("run")
    world = InsuranceWorld(enterprise_settings, "insurance.claims-analyst.test-non-binding")
    await world.reset(seed=8001, run_id=run_id)
    before = await world.snapshot()
    recommendation = await world.invoke(
        _call(
            run_id,
            "record_claim_recommendation",
            {
                "claim_id": "clm_100",
                "recommendation": "approve",
                "reason_code": "clear_liability_within_limit",
                "recommended_minor": 150000,
                "idempotency_key": "unit-test-recommendation",
            },
            {"claim:recommend"},
        )
    )
    assert recommendation.status is ToolResultStatus.SUCCESS
    flag = await world.invoke(
        _call(
            run_id,
            "flag_claim_for_review",
            {
                "claim_id": "clm_100",
                "reason_code": "duplicate_loss_pattern",
                "severity": "high",
                "idempotency_key": "unit-test-flag",
            },
            {"claim:flag"},
        )
    )
    assert flag.status is ToolResultStatus.SUCCESS
    after = await world.snapshot()
    before_claims = {
        item["id"]: (item["status"], item["approved_minor"], item["paid_minor"])
        for item in cast(list[dict[str, Any]], before.state["claims"])
    }
    after_claims = {
        item["id"]: (item["status"], item["approved_minor"], item["paid_minor"])
        for item in cast(list[dict[str, Any]], after.state["claims"])
    }
    assert before_claims == after_claims
    assert [event.event_type for event in await world.events()] == [
        "claim.recommendation_recorded",
        "claim.risk_flagged",
    ]
    recommendations = cast(list[dict[str, Any]], after.state["recommendations"])
    assert len(recommendations) == 1
    assert recommendations[0]["binding"] is False
    await world.close()


async def test_claims_analyst_cannot_decide_or_pay_under_any_analyst_scope_combination(
    enterprise_settings: DatabaseSettings,
) -> None:
    """FR-007: every analyst scope granted at once still never authorizes decide/pay."""
    run_id = prefixed_ulid("run")
    world = InsuranceWorld(enterprise_settings, "insurance.claims-analyst.test-prohibited")
    await world.reset(seed=8001, run_id=run_id)
    before = await world.snapshot()
    every_other_analyst_scope = {
        "claim:read",
        "claim:evidence",
        "claim:analyst-note",
        "claim:recommend",
        "claim:flag",
        "claim:investigate",
    }
    denied_decision = await world.invoke(
        _call(
            run_id,
            "decide_claim",
            {
                "claim_id": "clm_100",
                "decision": "approve",
                "approved_minor": 150000,
                "idempotency_key": "unit-test-forbidden-decide",
            },
            every_other_analyst_scope,
        )
    )
    assert denied_decision.error_type == "AuthorizationDenied"
    denied_payment = await world.invoke(
        _call(
            run_id,
            "issue_claim_payment",
            {
                "claim_id": "clm_100",
                "amount_minor": 150000,
                "currency": "USD",
                "idempotency_key": "unit-test-forbidden-pay",
            },
            every_other_analyst_scope,
        )
    )
    assert denied_payment.error_type == "AuthorizationDenied"
    after = await world.snapshot()
    assert after.state == before.state
    assert await world.events() == []
    await world.close()


async def test_claims_analyst_note_is_atomic_idempotent_and_rollback_safe(
    enterprise_settings: DatabaseSettings,
) -> None:
    """add_analyst_note: identical retry is idempotent; injected failure leaves no trace."""
    run_id = prefixed_ulid("run")
    world = InsuranceWorld(enterprise_settings, "insurance.claims-analyst.test-note-atomic")
    await world.reset(seed=8001, run_id=run_id)
    note_arguments: dict[str, object] = {
        "claim_id": "clm_100",
        "note": "Coverage confirmed active; requested amount within limit.",
        "idempotency_key": "unit-test-note-retry",
    }
    first = await world.invoke(
        _call(run_id, "add_analyst_note", note_arguments, {"claim:analyst-note"})
    )
    assert first.status is ToolResultStatus.SUCCESS
    after_first = await world.snapshot()
    events_after_first = await world.events()
    retry = await world.invoke(
        _call(run_id, "add_analyst_note", note_arguments, {"claim:analyst-note"})
    )
    assert retry.status is ToolResultStatus.SUCCESS
    assert retry.output == first.output
    assert (await world.snapshot()).state == after_first.state
    assert await world.events() == events_after_first
    before_failure = await world.snapshot()
    events_before_failure = await world.events()
    failed = await world.invoke(
        _call(
            run_id,
            "add_analyst_note",
            {
                "claim_id": "clm_100",
                "note": "This note should never be committed.",
                "idempotency_key": "unit-test-note-rollback",
                "inject_failure": True,
            },
            {"claim:analyst-note"},
        )
    )
    assert failed.error_type == "ToolExecutionError"
    assert (await world.snapshot()).state == before_failure.state
    assert await world.events() == events_before_failure
    await world.close()


async def test_calculate_coverage_analysis_reports_financial_boundary_conditions(
    enterprise_settings: DatabaseSettings,
) -> None:
    """Below-deductible, exceeds-limit, and per-item-sublimit fixture bands compute correctly."""
    run_id = prefixed_ulid("run")

    async def analyze(seed: int) -> dict[str, Any]:
        world = InsuranceWorld(enterprise_settings, f"insurance.claims-analyst.test-calc-{seed}")
        await world.reset(seed=seed, run_id=run_id)
        result = await world.invoke(
            _call(
                run_id,
                "calculate_coverage_analysis",
                cast(dict[str, object], {"claim_id": "clm_100"}),
                {"claim:read"},
            )
        )
        assert result.status is ToolResultStatus.SUCCESS
        await world.close()
        return cast(dict[str, Any], result.output)

    below_deductible = await analyze(8501)
    assert below_deductible["within_deductible"] is True
    assert below_deductible["payable_minor"] == 0
    assert below_deductible["eligible"] is False

    exceeds_limit = await analyze(8601)
    assert exceeds_limit["exceeds_limit"] is True
    assert (
        exceeds_limit["payable_minor"]
        == exceeds_limit["limit_minor"] - exceeds_limit["deductible_minor"]
    )

    sublimit = await analyze(8701)
    assert sublimit["sublimit_minor"] == 100_000
    assert sublimit["exceeds_sublimit"] is True
    assert sublimit["payable_minor"] == 100_000 - sublimit["deductible_minor"]


async def test_calculate_coverage_analysis_reports_timing_boundary_conditions(
    enterprise_settings: DatabaseSettings,
) -> None:
    """Policy-boundary and impossible-chronology fixture bands are correctly flagged ineligible."""
    run_id = prefixed_ulid("run")

    async def analyze(seed: int) -> dict[str, Any]:
        world = InsuranceWorld(enterprise_settings, f"insurance.claims-analyst.test-timing-{seed}")
        await world.reset(seed=seed, run_id=run_id)
        result = await world.invoke(
            _call(
                run_id,
                "calculate_coverage_analysis",
                cast(dict[str, object], {"claim_id": "clm_100"}),
                {"claim:read"},
            )
        )
        assert result.status is ToolResultStatus.SUCCESS
        await world.close()
        return cast(dict[str, Any], result.output)

    outside_policy_period = await analyze(9201)
    assert outside_policy_period["within_policy_period"] is False
    assert outside_policy_period["eligible"] is False

    impossible_chronology = await analyze(9301)
    assert impossible_chronology["chronology_valid"] is False
    assert impossible_chronology["eligible"] is False

    baseline = await analyze(8001)
    assert baseline["within_policy_period"] is True
    assert baseline["chronology_valid"] is True
    assert baseline["eligible"] is True


async def test_illegal_claim_status_transition_is_rejected_for_analyst_tools(
    enterprise_settings: DatabaseSettings,
) -> None:
    """A rejected/approved claim cannot accept a new evidence request or escalation."""
    run_id = prefixed_ulid("run")

    rejected_world = InsuranceWorld(enterprise_settings, "insurance.claims-analyst.test-rejected")
    await rejected_world.reset(seed=9101, run_id=run_id)
    before = await rejected_world.snapshot()
    denied_evidence = await rejected_world.invoke(
        _call(
            run_id,
            "request_evidence",
            {
                "claim_id": "clm_100",
                "document_type": "supplemental_photos",
                "idempotency_key": "unit-test-illegal-evidence",
            },
            {"claim:evidence"},
        )
    )
    assert denied_evidence.error_type == "IllegalClaimTransition"
    assert (await rejected_world.snapshot()).state == before.state
    assert await rejected_world.events() == []
    await rejected_world.close()

    approved_world = InsuranceWorld(enterprise_settings, "insurance.claims-analyst.test-approved")
    await approved_world.reset(seed=9001, run_id=run_id)
    before_escalation = await approved_world.snapshot()
    denied_escalation = await approved_world.invoke(
        _call(
            run_id,
            "escalate_investigation",
            {
                "claim_id": "clm_100",
                "reason": "Attempting escalation on an already-approved claim.",
                "idempotency_key": "unit-test-illegal-escalation",
            },
            {"claim:investigate"},
        )
    )
    assert denied_escalation.error_type == "IllegalClaimTransition"
    assert (await approved_world.snapshot()).state == before_escalation.state
    assert await approved_world.events() == []
    await approved_world.close()


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
    # Bounded, not unbounded: the enterprise corpus has grown well past the
    # ~25 scenarios this test was written against (127+ once claims-analyst
    # reaches its full target), and firing every scenario's own fresh
    # Postgres connection at once can exceed what the driver/OS reliably
    # completes in one burst. A semaphore keeps this a correctness check
    # (every scenario still executes and is graded) without depending on
    # unlimited simultaneous-connection headroom.
    concurrency = asyncio.Semaphore(20)

    async def run(scenario_index: int) -> bool:
        async with concurrency:
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
