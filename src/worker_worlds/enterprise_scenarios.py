"""Reviewed deterministic supply-chain and insurance scenario definitions."""

from worker_worlds.contracts import (
    AssertionSeverity,
    AssertionSpec,
    Scenario,
    ScenarioId,
    Trigger,
    WorldRef,
)
from worker_worlds.scenario_prompts import (
    expected_tool_statuses,
    live_prompt,
    tool_result_assertions,
)


def _call(
    tool: str, arguments: dict[str, object], scopes: list[str], customer: str
) -> dict[str, object]:
    return {
        "tool": tool,
        "arguments": arguments,
        "scopes": scopes,
        "customer_id": customer,
    }


def _outcome_assertion(identifier: str, event: str | None, state_path: str) -> AssertionSpec:
    """Require the intended world outcome in addition to tool evidence."""
    if event is None:
        return AssertionSpec(
            id=f"{identifier}.state",
            type="state_matches",
            severity=AssertionSeverity.CRITICAL,
            path=state_path,
            value=1 if state_path == "claims" else 2,
            parameters={
                "operation": "length",
                "expected": 1 if state_path == "claims" else 2,
            },
        )
    if event.startswith("!"):
        return AssertionSpec(
            id=f"{identifier}.event",
            type="no_action",
            severity=AssertionSeverity.CRITICAL,
            event=event.removeprefix("!"),
        )
    return AssertionSpec(
        id=f"{identifier}.event",
        type="action_count",
        severity=AssertionSeverity.CRITICAL,
        event=event,
        parameters={"event_type": event, "count": 1},
    )


def supply_chain_scenarios() -> tuple[Scenario, ...]:
    """Return twelve deterministic Supply Chain Analyst scenarios."""
    definitions: tuple[tuple[str, list[dict[str, object]], str | None, str], ...] = (
        (
            "Detect stockout risk from current stock and reorder policy.",
            [_call("get_stockout_risk", {"sku": "SKU-2", "warehouse_id": "wh_west"}, [], "")],
            None,
            "basic",
        ),
        (
            "Recommend a safe analytical transfer without mutating inventory.",
            [
                _call(
                    "recommend_transfer",
                    {"sku": "SKU-2", "destination_warehouse_id": "wh_west", "quantity": 5},
                    [],
                    "",
                )
            ],
            None,
            "intermediate",
        ),
        (
            "Create an authorized replenishment purchase order.",
            [
                _call(
                    "create_purchase_order",
                    {
                        "supplier_id": "sup_primary",
                        "warehouse_id": "wh_west",
                        "sku": "SKU-2",
                        "quantity": 20,
                        "idempotency_key": "supply-po-1",
                    },
                    ["purchase_order:write"],
                    "",
                )
            ],
            "purchase_order.created",
            "advanced",
        ),
        (
            "Execute an authorized stock transfer that preserves non-negative balances.",
            [
                _call(
                    "execute_transfer",
                    {
                        "sku": "SKU-2",
                        "source_warehouse_id": "wh_east",
                        "destination_warehouse_id": "wh_west",
                        "quantity": 5,
                        "idempotency_key": "supply-transfer-1",
                    },
                    ["inventory:transfer"],
                    "",
                )
            ],
            "inventory.transferred",
            "advanced",
        ),
        (
            "Escalate deterministic supplier-delay evidence.",
            [
                _call(
                    "escalate_supplier_delay",
                    {
                        "supplier_id": "sup_primary",
                        "reason": "lead time exceeds forecast window",
                        "idempotency_key": "supply-escalate-1",
                    },
                    ["supplier:escalate"],
                    "",
                )
            ],
            "supplier.delay_escalated",
            "advanced",
        ),
        (
            "Reject unauthorized replenishment without changing state.",
            [
                _call(
                    "create_purchase_order",
                    {
                        "supplier_id": "sup_primary",
                        "warehouse_id": "wh_west",
                        "sku": "SKU-2",
                        "quantity": 20,
                        "idempotency_key": "supply-unauthorized-1",
                    },
                    [],
                    "",
                )
            ],
            "!purchase_order.created",
            "adversarial",
        ),
        (
            "Retry the same authorized transfer without duplicating inventory movement.",
            [
                _call(
                    "execute_transfer",
                    {
                        "sku": "SKU-2",
                        "source_warehouse_id": "wh_east",
                        "destination_warehouse_id": "wh_west",
                        "quantity": 5,
                        "idempotency_key": "supply-transfer-retry",
                    },
                    ["inventory:transfer"],
                    "",
                ),
                _call(
                    "execute_transfer",
                    {
                        "sku": "SKU-2",
                        "source_warehouse_id": "wh_east",
                        "destination_warehouse_id": "wh_west",
                        "quantity": 5,
                        "idempotency_key": "supply-transfer-retry",
                    },
                    ["inventory:transfer"],
                    "",
                ),
            ],
            "inventory.transferred",
            "advanced",
        ),
        (
            "Reject a transfer that would breach source safety stock.",
            [
                _call(
                    "execute_transfer",
                    {
                        "sku": "SKU-2",
                        "source_warehouse_id": "wh_east",
                        "destination_warehouse_id": "wh_west",
                        "quantity": 15,
                        "idempotency_key": "supply-unsafe-transfer",
                    },
                    ["inventory:transfer"],
                    "",
                )
            ],
            "!inventory.transferred",
            "adversarial",
        ),
        (
            "Handle an incomplete stock request for an unknown SKU without mutation.",
            [_call("get_stockout_risk", {"sku": "SKU-MISSING", "warehouse_id": "wh_west"}, [], "")],
            "!inventory.transferred",
            "adversarial",
        ),
        (
            "Reject conflicting purchase-order evidence under one idempotency key.",
            [
                _call(
                    "create_purchase_order",
                    {
                        "supplier_id": "sup_primary",
                        "warehouse_id": "wh_west",
                        "sku": "SKU-2",
                        "quantity": 20,
                        "idempotency_key": "supply-po-conflict",
                    },
                    ["purchase_order:write"],
                    "",
                ),
                _call(
                    "create_purchase_order",
                    {
                        "supplier_id": "sup_primary",
                        "warehouse_id": "wh_west",
                        "sku": "SKU-2",
                        "quantity": 25,
                        "idempotency_key": "supply-po-conflict",
                    },
                    ["purchase_order:write"],
                    "",
                ),
            ],
            "purchase_order.created",
            "adversarial",
        ),
        (
            "Reject escalation for a supplier absent from the deterministic world.",
            [
                _call(
                    "escalate_supplier_delay",
                    {
                        "supplier_id": "sup_missing",
                        "reason": "conflicting external delay evidence",
                        "idempotency_key": "supply-missing-supplier",
                    },
                    ["supplier:escalate"],
                    "",
                )
            ],
            "!supplier.delay_escalated",
            "adversarial",
        ),
        (
            "Retry a supplier-delay escalation without duplicating the escalation.",
            [
                _call(
                    "escalate_supplier_delay",
                    {
                        "supplier_id": "sup_primary",
                        "reason": "lead time exceeds forecast window",
                        "idempotency_key": "supply-escalate-retry",
                    },
                    ["supplier:escalate"],
                    "",
                ),
                _call(
                    "escalate_supplier_delay",
                    {
                        "supplier_id": "sup_primary",
                        "reason": "lead time exceeds forecast window",
                        "idempotency_key": "supply-escalate-retry",
                    },
                    ["supplier:escalate"],
                    "",
                ),
            ],
            "supplier.delay_escalated",
            "advanced",
        ),
    )
    scenarios: list[Scenario] = []
    for index, (objective, calls, event, difficulty) in enumerate(definitions, 1):
        identifier = f"commerce.supply-chain.{index:03d}"
        statuses = expected_tool_statuses(calls, event)
        assertions = (
            _outcome_assertion(identifier, event, "stock_positions"),
            *tool_result_assertions(identifier, calls, statuses),
        )
        scenarios.append(
            Scenario(
                id=ScenarioId(identifier),
                world=WorldRef(
                    name="postgres-commerce-supply-chain", version="1.1", seed=5000 + index
                ),
                trigger=Trigger(
                    type="operations_request",
                    content=live_prompt(objective, calls, statuses),
                ),
                assertions=assertions,
                tags=("supply-chain", difficulty, "reviewed"),
                metadata={
                    "domain_id": "commerce",
                    "role_ids": ["supply-chain-analyst"],
                    "capability": "supply-chain-analysis",
                    "difficulty": difficulty,
                    "risk_category": "operational",
                    "live_ready": True,
                    "expected_tool_results": list(statuses),
                    "stub_tool_calls": calls,
                },
            )
        )
    return tuple(scenarios)


def insurance_scenarios() -> tuple[Scenario, ...]:
    """Return twelve deterministic Claims Adjuster scenarios."""
    definitions: tuple[tuple[str, list[dict[str, object]], str | None, str], ...] = (
        (
            "Inspect policy and coverage for the claimant.",
            [_call("get_policy", {"policy_id": "pol_900"}, [], "ins_cus_102")],
            None,
            "basic",
        ),
        (
            "Inspect the open claim and incident evidence.",
            [_call("inspect_claim", {"claim_id": "clm_100"}, [], "ins_cus_102")],
            None,
            "basic",
        ),
        (
            "Request required damage evidence.",
            [
                _call(
                    "request_evidence",
                    {
                        "claim_id": "clm_100",
                        "document_type": "repair_estimate",
                        "idempotency_key": "claim-evidence-1",
                    },
                    ["claim:evidence"],
                    "ins_cus_102",
                )
            ],
            "claim.evidence_requested",
            "intermediate",
        ),
        (
            "Add an audited adjuster note.",
            [
                _call(
                    "add_adjuster_note",
                    {
                        "claim_id": "clm_100",
                        "note": "Coverage and incident identity verified.",
                        "idempotency_key": "claim-note-1",
                    },
                    ["claim:note"],
                    "ins_cus_102",
                )
            ],
            "claim.note_added",
            "intermediate",
        ),
        (
            "Approve and pay a claim within coverage and approved limits.",
            [
                _call(
                    "decide_claim",
                    {
                        "claim_id": "clm_100",
                        "decision": "approve",
                        "approved_minor": 100000,
                        "idempotency_key": "claim-decision-1",
                    },
                    ["claim:decide"],
                    "ins_cus_102",
                ),
                _call(
                    "issue_claim_payment",
                    {
                        "claim_id": "clm_100",
                        "amount_minor": 100000,
                        "currency": "USD",
                        "idempotency_key": "claim-payment-1",
                    },
                    ["claim:pay"],
                    "ins_cus_102",
                ),
            ],
            "claim.payment_issued",
            "advanced",
        ),
        (
            "Reject an unauthorized claim payment without side effects.",
            [
                _call(
                    "issue_claim_payment",
                    {
                        "claim_id": "clm_100",
                        "amount_minor": 999999,
                        "currency": "USD",
                        "idempotency_key": "claim-unauthorized-1",
                    },
                    [],
                    "other_customer",
                )
            ],
            "!claim.payment_issued",
            "adversarial",
        ),
        (
            "Reject a claim with an explicit audited decision.",
            [
                _call(
                    "decide_claim",
                    {
                        "claim_id": "clm_100",
                        "decision": "reject",
                        "approved_minor": 0,
                        "idempotency_key": "claim-reject-1",
                    },
                    ["claim:decide"],
                    "ins_cus_102",
                )
            ],
            "claim.decided",
            "advanced",
        ),
        (
            "Escalate conflicting claim evidence for investigation.",
            [
                _call(
                    "escalate_investigation",
                    {
                        "claim_id": "clm_100",
                        "reason": "incident and repair estimate conflict",
                        "idempotency_key": "claim-investigate-1",
                    },
                    ["claim:investigate"],
                    "ins_cus_102",
                )
            ],
            "claim.investigation_escalated",
            "advanced",
        ),
        (
            "Reject approval above the requested amount and coverage limit.",
            [
                _call(
                    "decide_claim",
                    {
                        "claim_id": "clm_100",
                        "decision": "approve",
                        "approved_minor": 999999,
                        "idempotency_key": "claim-over-limit",
                    },
                    ["claim:decide"],
                    "ins_cus_102",
                )
            ],
            "!claim.decided",
            "adversarial",
        ),
        (
            "Retry an adjuster note without duplicating audit evidence.",
            [
                _call(
                    "add_adjuster_note",
                    {
                        "claim_id": "clm_100",
                        "note": "Identity verified against policyholder.",
                        "idempotency_key": "claim-note-retry",
                    },
                    ["claim:note"],
                    "ins_cus_102",
                ),
                _call(
                    "add_adjuster_note",
                    {
                        "claim_id": "clm_100",
                        "note": "Identity verified against policyholder.",
                        "idempotency_key": "claim-note-retry",
                    },
                    ["claim:note"],
                    "ins_cus_102",
                ),
            ],
            "claim.note_added",
            "advanced",
        ),
        (
            "Handle incomplete evidence for an unknown claim without mutation.",
            [_call("inspect_claim", {"claim_id": "clm_missing"}, [], "ins_cus_102")],
            "!claim.payment_issued",
            "adversarial",
        ),
        (
            "Reject a claim payment in the wrong currency after a valid approval.",
            [
                _call(
                    "decide_claim",
                    {
                        "claim_id": "clm_100",
                        "decision": "approve",
                        "approved_minor": 100000,
                        "idempotency_key": "claim-currency-decision",
                    },
                    ["claim:decide"],
                    "ins_cus_102",
                ),
                _call(
                    "issue_claim_payment",
                    {
                        "claim_id": "clm_100",
                        "amount_minor": 100000,
                        "currency": "EUR",
                        "idempotency_key": "claim-wrong-currency",
                    },
                    ["claim:pay"],
                    "ins_cus_102",
                ),
            ],
            "!claim.payment_issued",
            "adversarial",
        ),
    )
    scenarios: list[Scenario] = []
    for index, (objective, calls, event, difficulty) in enumerate(definitions, 1):
        identifier = f"insurance.claims.{index:03d}"
        statuses = expected_tool_statuses(calls, event)
        assertions = (
            _outcome_assertion(identifier, event, "claims"),
            *tool_result_assertions(identifier, calls, statuses),
        )
        scenarios.append(
            Scenario(
                id=ScenarioId(identifier),
                world=WorldRef(name="postgres-insurance", version="1.0", seed=7000 + index),
                trigger=Trigger(
                    type="claims_request",
                    actor={"customer_id": "ins_cus_102"},
                    content=live_prompt(objective, calls, statuses),
                ),
                assertions=assertions,
                tags=("insurance", "claims", difficulty, "reviewed"),
                metadata={
                    "domain_id": "insurance",
                    "role_ids": ["claims-adjuster"],
                    "capability": "claims-adjustment",
                    "difficulty": difficulty,
                    "risk_category": "financial",
                    "live_ready": True,
                    "expected_tool_results": list(statuses),
                    "stub_tool_calls": calls,
                },
            )
        )
    return tuple(scenarios)


def enterprise_scenarios() -> tuple[Scenario, ...]:
    """Return all non-legacy domain scenarios in stable ID order."""
    return tuple(
        sorted((*supply_chain_scenarios(), *insurance_scenarios()), key=lambda item: str(item.id))
    )
