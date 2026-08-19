from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from worker_worlds.contracts import Money
from worker_worlds.domain import Order, OrderStatus, TicketStatus, validate_ticket_transition
from worker_worlds.seeding import build_fixture, canonical_fixture_json, fixture_hash


def test_order_rejects_over_refund() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        Order(
            id="ord",
            customer_id="cus",
            status=OrderStatus.REFUNDED,
            captured=Money(amount_minor=100, currency="USD"),
            refunded=Money(amount_minor=101, currency="USD"),
            created_at=datetime.now(UTC),
        )


def test_illegal_ticket_transition_rejected() -> None:
    with pytest.raises(ValueError, match="illegal ticket transition"):
        validate_ticket_transition(TicketStatus.CLOSED, TicketStatus.OPEN)


def test_seed_is_pure_and_varies_documentedly() -> None:
    first = build_fixture("1.0", 10)
    assert first == build_fixture("1.0", 10)
    assert fixture_hash(first) == fixture_hash(build_fixture("1.0", 10))
    different = build_fixture("1.0", 11)
    assert fixture_hash(first) != fixture_hash(different)
    assert first["inventory"][0]["available"] == 13
    assert different["inventory"][0]["available"] == 14


def test_canonical_fixture_ignores_mapping_insertion_order() -> None:
    fixture = build_fixture("1.0", 10)
    reversed_fixture = dict(reversed(list(fixture.items())))
    assert canonical_fixture_json(fixture) == canonical_fixture_json(reversed_fixture)


def test_messy_fixture_is_explicit_deterministic_and_untrusted() -> None:
    first = build_fixture("1.0", 55)
    assert first == build_fixture("1.0", 55)
    assert len(first["customers"]) == 2
    assert len(first["inventory"]) == 2
    assert {row["location"] for row in first["inventory"]} == {"default", "secondary"}
    assert any(row["backorder_allowed"] for row in first["inventory"])
    assert {row["status"] for row in first["shipments"]} == {"delivered", "pending"}
    untrusted = (first["products"][1]["title"], first["tickets"][0]["subject"])
    assert all("[UNTRUSTED]" in value for value in untrusted)
    facts = {fact["key"]: fact for fact in first["facts"]}
    assert facts["pricing.catalog_minor"]["value"] != facts["pricing.checkout_minor"]["value"]
    assert (
        facts["order.displayed_total_minor"]["value"]
        != facts["order.captured_total_minor"]["value"]
    )
    assert facts["promotion.expired"]["value"] is True
    assert facts["ticket.lifecycle"]["value"] == ["cancelled", "reopened"]
    assert sum(fact["trust"] == "untrusted_content" for fact in first["facts"]) == 7
