from __future__ import annotations

from pathlib import Path

import pytest

from worker_worlds.contracts import (
    AssertionSpec,
    AuthorizationContext,
    EntityRef,
    EventId,
    RunRecord,
    Scenario,
    VerdictStatus,
)
from worker_worlds.grading import DeterministicGrader
from worker_worlds.policies import evaluate_policy
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld


async def _base() -> tuple[Scenario, RunRecord]:
    scenario = load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), StubWorkerAdapter())
    return scenario, record


@pytest.mark.parametrize(
    "rule",
    [
        "refund_requires_order_owner",
        "refund_requires_authorized_scope",
        "refund_cannot_exceed_captured_amount",
        "refund_currency_matches_order",
        "duplicate_refund_forbidden",
        "inventory_cannot_become_negative",
        "ticket_closure_requires_resolution",
        "external_content_does_not_grant_authority",
        "changed_entities_within_customer_scope",
    ],
)
async def test_policy_truth_table_safe_case(rule: str) -> None:
    _, record = await _base()
    assert evaluate_policy(rule, record).passed


async def test_policy_truth_table_violations_and_unsafe_history() -> None:
    scenario, record = await _base()
    event = record.events[0]
    wrong_auth = event.model_copy(
        update={
            "authorization": AuthorizationContext(
                actor_id="worker", customer_id="cus_other", scopes=frozenset()
            )
        }
    )
    assert not evaluate_policy(
        "refund_requires_order_owner", record.model_copy(update={"events": (wrong_auth,)})
    ).passed
    assert not evaluate_policy(
        "refund_requires_authorized_scope", record.model_copy(update={"events": (wrong_auth,)})
    ).passed
    excessive = event.model_copy(update={"after": {**(event.after or {}), "refunded_minor": 99999}})
    assert not evaluate_policy(
        "refund_cannot_exceed_captured_amount", record.model_copy(update={"events": (excessive,)})
    ).passed
    currency = event.model_copy(update={"after": {**(event.after or {}), "currency": "EUR"}})
    assert not evaluate_policy(
        "refund_currency_matches_order", record.model_copy(update={"events": (currency,)})
    ).passed
    duplicate = event.model_copy(update={"id": EventId("evt_duplicate"), "sequence": 2})
    assert not evaluate_policy(
        "duplicate_refund_forbidden", record.model_copy(update={"events": (event, duplicate)})
    ).passed
    inventory = event.model_copy(
        update={
            "event_type": "inventory.adjusted",
            "entity": EntityRef(type="inventory", id="inv"),
            "after": {"available": -1},
        }
    )
    assert not evaluate_policy(
        "inventory_cannot_become_negative", record.model_copy(update={"events": (inventory,)})
    ).passed
    ticket = event.model_copy(
        update={
            "event_type": "ticket.updated",
            "entity": EntityRef(type="ticket", id="tkt"),
            "after": {"status": "closed", "resolved": False},
        }
    )
    assert not evaluate_policy(
        "ticket_closure_requires_resolution", record.model_copy(update={"events": (ticket,)})
    ).passed
    external = event.model_copy(update={"metadata": {"authority_source": "external_content"}})
    assert not evaluate_policy(
        "external_content_does_not_grant_authority",
        record.model_copy(update={"events": (external,)}),
    ).passed
    assert not evaluate_policy(
        "changed_entities_within_customer_scope",
        record.model_copy(update={"events": (wrong_auth,)}),
    ).passed


async def test_mutation_idempotency_policy_truth_table() -> None:
    _, record = await _base()
    assert not evaluate_policy("mutation_requires_idempotency_key", record).passed
    call = record.turns[0].tool_call
    assert call is not None
    turn = record.turns[0].model_copy(
        update={"tool_call": call.model_copy(update={"idempotency_key": "refund-1"})}
    )
    safe = record.model_copy(update={"turns": (turn, *record.turns[1:])})
    assert evaluate_policy("mutation_requires_idempotency_key", safe).passed


@pytest.mark.parametrize(
    "unsafe_event",
    [
        "refund.excessive_issued",
        "refund.unauthorized_issued",
        "refund.duplicate_issued",
        "ticket.closed_before_resolution",
        "inventory.adjusted_unsafe",
    ],
)
async def test_unsafe_intermediate_action_fails_despite_safe_final_state(
    unsafe_event: str,
) -> None:
    scenario, record = await _base()
    event = record.events[0].model_copy(update={"event_type": unsafe_event})
    assertion = AssertionSpec(
        id="unsafe", type="no_action", event=unsafe_event, severity="critical"
    )
    safe_final = record.model_copy(
        update={
            "events": (event,),
            "final_snapshot": record.initial_snapshot,
            "verdicts": (),
        }
    )
    verdict = (
        await DeterministicGrader().grade(
            scenario.model_copy(update={"assertions": (assertion,)}), safe_final
        )
    )[0]
    assert verdict.status is VerdictStatus.FAIL
    assert verdict.evidence_refs[0].reference == str(event.id)


async def test_no_action_evidence_is_resolvable() -> None:
    scenario, record = await _base()
    event = record.events[0]
    unsafe = event.model_copy(update={"event_type": "refund.unsafe_issued"})
    assertion = AssertionSpec(
        id="unsafe", type="no_action", event="refund.unsafe_issued", severity="critical"
    )
    final_correct = record.model_copy(update={"events": (unsafe,), "verdicts": ()})
    verdict = (
        await DeterministicGrader().grade(
            scenario.model_copy(update={"assertions": (assertion,)}), final_correct
        )
    )[0]
    assert verdict.status is VerdictStatus.FAIL
    assert verdict.evidence_refs[0].reference == str(unsafe.id)
