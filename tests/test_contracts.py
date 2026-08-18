from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from worker_worlds.contracts import Money, Scenario, ToolResult, ToolResultStatus


def test_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Money.model_validate({"amount_minor": 100, "currency": "USD", "unknown": True})


def test_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        Money(amount_minor=12.5, currency="USD")


def test_non_utc_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ToolResult(
            call_id="call_1",
            status=ToolResultStatus.SUCCESS,
            started_at=datetime(2026, 1, 1),
            ended_at=datetime.now(UTC),
        )


def test_unsupported_major_rejected_clearly() -> None:
    with pytest.raises(ValidationError, match="unsupported schema major 2"):
        Money(schema_version="2.0", amount_minor=100, currency="USD")


def test_deterministic_serialization(happy_scenario: Scenario) -> None:
    first = happy_scenario.canonical_json()
    reparsed = type(happy_scenario).model_validate_json(first)
    assert reparsed.canonical_json() == first
    assert reparsed.canonical_hash() == happy_scenario.canonical_hash()


def test_error_result_requires_provenance() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="require error_type"):
        ToolResult(
            call_id="call_1",
            status=ToolResultStatus.ERROR,
            started_at=now,
            ended_at=now,
        )


def test_day_zero_serialized_scenario_remains_readable() -> None:
    payload = {
        "schema_version": "1.0",
        "id": "legacy.v1",
        "world": {"schema_version": "1.0", "name": "stub", "version": "1.0", "seed": 1},
        "trigger": {"schema_version": "1.0", "type": "message", "content": "legacy"},
        "assertions": [
            {
                "schema_version": "1.0",
                "id": "legacy.assertion",
                "type": "state_equals",
                "severity": "critical",
                "path": "value",
                "value": 1,
            }
        ],
    }
    scenario = Scenario.model_validate(payload)
    assert scenario.assertions[0].required
    assert scenario.assertions[0].parameters == {}
