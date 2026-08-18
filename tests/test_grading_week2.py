from __future__ import annotations

from pathlib import Path

from worker_worlds.contracts import (
    AssertionSpec,
    EntityRef,
    EventId,
    RunRecord,
    Scenario,
    VerdictStatus,
)
from worker_worlds.errors import ScenarioLoadError
from worker_worlds.grading import DeterministicGrader
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld


async def _record() -> tuple[Scenario, RunRecord]:
    scenario = load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), StubWorkerAdapter())
    event = record.events[0].model_copy(
        update={
            "id": EventId("evt_second"),
            "sequence": 2,
            "event_type": "email.sent",
            "entity": EntityRef(type="email", id="eml_1"),
        }
    )
    return scenario, record.model_copy(
        update={"events": (*record.events, event), "mutation_count": 2}
    )


async def test_every_assertion_primitive_passes_with_typed_evidence() -> None:
    scenario, record = await _record()
    assertions = (
        AssertionSpec(
            id="equals",
            type="state_equals",
            severity="critical",
            path="refunds.0.amount_minor",
            value=2499,
        ),
        AssertionSpec(
            id="matches",
            type="state_matches",
            severity="high",
            path="orders.ord_900.customer_id",
            parameters={"operation": "regex", "expected": "cus_[0-9]+"},
        ),
        AssertionSpec(
            id="within",
            type="value_within",
            severity="high",
            path="refunds.0.amount_minor",
            parameters={"minimum": 2400, "maximum": 2500},
        ),
        AssertionSpec(id="exists", type="action_exists", severity="high", event="refund.issued"),
        AssertionSpec(id="none", type="no_action", severity="high", event="inventory.adjusted"),
        AssertionSpec(
            id="count",
            type="action_count",
            severity="high",
            event="refund.issued",
            parameters={"count": 1},
        ),
        AssertionSpec(
            id="sequence",
            type="sequence_before",
            severity="high",
            parameters={
                "first": {"event_type": "refund.issued"},
                "second": {"event_type": "email.sent"},
                "maximum_gap": 1,
            },
        ),
        AssertionSpec(
            id="changes",
            type="changed_entities_subset",
            severity="high",
            parameters={
                "allowed": [
                    {"entity_type": "orders", "entity_id": "root", "fields": ["*"]},
                    {"entity_type": "refunds", "entity_id": "*", "fields": ["*"]},
                ]
            },
        ),
        AssertionSpec(
            id="resource",
            type="resource_within",
            severity="medium",
            parameters={"metric": "tool_calls", "minimum": 1, "maximum": 2},
        ),
        AssertionSpec(
            id="policy",
            type="policy",
            severity="critical",
            parameters={"rule": "refund_requires_order_owner"},
        ),
    )
    graded = await DeterministicGrader().grade(
        scenario.model_copy(update={"assertions": assertions}), record
    )
    assert all(verdict.status is VerdictStatus.PASS for verdict in graded), [
        (verdict.assertion_id, verdict.status, verdict.reason_code)
        for verdict in graded
        if verdict.status is not VerdictStatus.PASS
    ]
    assert all(verdict.reason_code != "unspecified" for verdict in graded)
    assert all(verdict.evidence_refs for verdict in graded)


async def test_failures_and_missing_evidence_are_distinct_and_deterministic() -> None:
    scenario, record = await _record()
    assertions = (
        AssertionSpec(
            id="wrong",
            type="state_equals",
            severity="critical",
            path="refunds.0.amount_minor",
            value=1,
        ),
        AssertionSpec(
            id="missing",
            type="state_equals",
            severity="critical",
            path="refunds.99.amount_minor",
            value=1,
        ),
        AssertionSpec(
            id="usage",
            type="resource_within",
            severity="high",
            parameters={"metric": "model_tokens", "minimum": 0, "maximum": 10},
        ),
    )
    authored = scenario.model_copy(update={"assertions": assertions})
    first = await DeterministicGrader().grade(authored, record)
    second = await DeterministicGrader().grade(authored, record)
    assert [item.canonical_json() for item in first] == [item.canonical_json() for item in second]
    assert first[0].status is VerdictStatus.FAIL
    assert first[1].reason_code == "state.path_missing"
    assert first[1].status is VerdictStatus.ERROR
    assert first[2].reason_code == "resource.unavailable"


def test_invalid_assertion_reports_source_index(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(
        "schema_version: '1.0'\nid: invalid\n"
        "world: {schema_version: '1.0', name: stub, version: '1.0', seed: 1}\n"
        "trigger: {schema_version: '1.0', type: message, content: test}\n"
        "assertions:\n  - {schema_version: '1.0', id: bad, type: state_equals, severity: high}\n"
    )
    try:
        load_scenario(path)
    except ScenarioLoadError as exc:
        assert "assertions.0" in str(exc)
        assert str(path) in str(exc)
    else:
        raise AssertionError("invalid assertion loaded")
