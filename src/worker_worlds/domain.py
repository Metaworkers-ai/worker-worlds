"""Pure typed commerce entities and invariants."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from worker_worlds.contracts import JsonValue, Money


class DomainModel(BaseModel):
    """Strict immutable base for commerce domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: object) -> object:
        """Require direct datetime fields to be timezone-aware UTC."""
        if isinstance(value, datetime):
            offset = value.utcoffset()
            if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
                raise ValueError("timestamps must be timezone-aware UTC")
        return value


class OrderStatus(StrEnum):
    """Legal order lifecycle states."""

    PENDING = "pending"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    """Refund processing states."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ShipmentStatus(StrEnum):
    """Shipment lifecycle states."""

    PENDING = "pending"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    LOST = "lost"


class TicketStatus(StrEnum):
    """Support ticket lifecycle states."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    ESCALATED = "escalated"
    CLOSED = "closed"


class Customer(DomainModel):
    """Commerce customer."""

    id: str
    email: str
    name: str
    created_at: datetime


class Product(DomainModel):
    """Sellable catalog product."""

    id: str
    sku: str
    title: str
    price: Money
    active: bool = True


class Inventory(DomainModel):
    """Inventory balance at a location."""

    id: str
    product_id: str
    location: str
    available: Annotated[int, Field(ge=0)]
    reserved: Annotated[int, Field(ge=0)] = 0
    backorder_allowed: bool = False


class LineItem(DomainModel):
    """Purchased product quantity and unit price."""

    id: str
    order_id: str
    product_id: str
    quantity: Annotated[int, Field(gt=0)]
    unit_price: Money


class Order(DomainModel):
    """Captured commerce order with refund balance."""

    id: str
    customer_id: str
    status: OrderStatus
    captured: Money
    refunded: Money
    created_at: datetime

    @model_validator(mode="after")
    def valid_balance(self) -> Self:
        """Enforce currency and captured/refunded reconciliation."""
        if self.captured.currency != self.refunded.currency:
            raise ValueError("order captured and refunded currencies must match")
        if self.refunded.amount_minor > self.captured.amount_minor:
            raise ValueError("refunded total cannot exceed captured total")
        return self


class Refund(DomainModel):
    """One idempotent order refund."""

    id: str
    order_id: str
    amount: Money
    status: RefundStatus
    idempotency_key: str
    created_at: datetime


class Shipment(DomainModel):
    """Order shipment."""

    id: str
    order_id: str
    status: ShipmentStatus
    quantity: Annotated[int, Field(gt=0)]
    created_at: datetime


class Ticket(DomainModel):
    """Customer support ticket."""

    id: str
    customer_id: str
    order_id: str | None
    subject: str
    status: TicketStatus
    created_at: datetime


class CommerceFact(DomainModel):
    """Explicit deterministic ambiguity that does not weaken entity invariants."""

    key: str
    value: JsonValue
    trust: str = Field(pattern="^(trusted_fixture|untrusted_content)$")


def validate_ticket_transition(current: TicketStatus, target: TicketStatus) -> None:
    """Reject illegal ticket state transitions."""
    legal = {
        TicketStatus.OPEN: {TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED, TicketStatus.CLOSED},
        TicketStatus.IN_PROGRESS: {TicketStatus.ESCALATED, TicketStatus.CLOSED},
        TicketStatus.ESCALATED: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
        TicketStatus.CLOSED: set(),
    }
    if target not in legal[current]:
        raise ValueError(f"illegal ticket transition: {current.value} -> {target.value}")
