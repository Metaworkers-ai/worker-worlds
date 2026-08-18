"""Pure deterministic commerce fixture construction."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from worker_worlds.contracts import Money
from worker_worlds.domain import (
    Customer,
    Inventory,
    LineItem,
    Order,
    OrderStatus,
    Product,
    Shipment,
    ShipmentStatus,
    Ticket,
    TicketStatus,
)
from worker_worlds.ids import deterministic_ulid

WORLD_VERSION = "1.0"


def build_fixture(world_version: str, seed: int) -> dict[str, list[dict[str, Any]]]:
    """Build the complete deterministic initial commerce fixture."""
    if world_version != WORLD_VERSION:
        raise ValueError(f"unsupported commerce world version: {world_version}")
    base = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=seed % 365)
    timestamp_ms = int(base.timestamp() * 1000)

    def entity_id(prefix: str) -> str:
        return f"{prefix}_{deterministic_ulid(timestamp_ms, f'{world_version}:{seed}:{prefix}')}"

    customer = Customer(
        id="cus_102", email=f"customer{seed % 97}@example.test", name="Ada Buyer", created_at=base
    )
    product = Product(
        id=entity_id("prd"),
        sku="SKU-2",
        title=f"Travel Mug {seed % 5}",
        price=Money(amount_minor=2499, currency="USD"),
    )
    order = Order(
        id="ord_900",
        customer_id=customer.id,
        status=OrderStatus.PAID,
        captured=Money(amount_minor=10000 + (seed % 4) * 100, currency="USD"),
        refunded=Money(amount_minor=0, currency="USD"),
        created_at=base + timedelta(minutes=1),
    )
    line = LineItem(
        id=entity_id("li"),
        order_id=order.id,
        product_id=product.id,
        quantity=1,
        unit_price=product.price,
    )
    inventory = Inventory(
        id=entity_id("inv"),
        product_id=product.id,
        location="default",
        available=10 + seed % 7,
    )
    shipment = Shipment(
        id=entity_id("shp"),
        order_id=order.id,
        status=ShipmentStatus.DELIVERED,
        quantity=1,
        created_at=base + timedelta(hours=1),
    )
    ticket = Ticket(
        id=entity_id("tkt"),
        customer_id=customer.id,
        order_id=order.id,
        subject="Damaged item",
        status=TicketStatus.OPEN,
        created_at=base + timedelta(hours=2),
    )
    return {
        "customers": [customer.model_dump(mode="json")],
        "products": [product.model_dump(mode="json")],
        "inventory": [inventory.model_dump(mode="json")],
        "orders": [order.model_dump(mode="json")],
        "line_items": [line.model_dump(mode="json")],
        "refunds": [],
        "shipments": [shipment.model_dump(mode="json")],
        "tickets": [ticket.model_dump(mode="json")],
        "emails": [],
        "escalations": [],
    }


def canonical_fixture_json(fixture: dict[str, list[dict[str, Any]]]) -> str:
    """Serialize a fixture independent of mapping insertion order."""
    return json.dumps(fixture, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fixture_hash(fixture: dict[str, list[dict[str, Any]]]) -> str:
    """Hash a canonical deterministic fixture."""
    return hashlib.sha256(canonical_fixture_json(fixture).encode()).hexdigest()
