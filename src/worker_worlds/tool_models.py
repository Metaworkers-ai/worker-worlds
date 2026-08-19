"""Strict schemas for commerce tool inputs and outputs."""

from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    """Strict base for tool input validation."""

    model_config = ConfigDict(extra="forbid", strict=True)


class GetOrderInput(ToolInput):
    """Get-order input."""

    order_id: str


class SearchOrdersInput(ToolInput):
    """Search-order input."""

    customer_id: str


class GetInventoryInput(ToolInput):
    """Inventory lookup input."""

    sku: str


class IssueRefundInput(ToolInput):
    """Refund mutation input."""

    order_id: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    idempotency_key: str = Field(min_length=1, max_length=128)
    processor_pending: bool = False
    inject_failure: bool = False


class CreateTicketInput(ToolInput):
    """Ticket creation input."""

    customer_id: str
    order_id: str | None = None
    subject: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)


class UpdateTicketInput(ToolInput):
    """Ticket update input."""

    ticket_id: str
    status: str
    idempotency_key: str = Field(min_length=1, max_length=128)


class AdjustInventoryInput(ToolInput):
    """Inventory adjustment input."""

    sku: str
    delta: int
    idempotency_key: str = Field(min_length=1, max_length=128)


class SendEmailInput(ToolInput):
    """Simulated email input."""

    customer_id: str
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class EscalateInput(ToolInput):
    """Escalation input."""

    ticket_id: str
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=128)


class CreateReplacementInput(ToolInput):
    """Replacement-order creation input."""

    order_id: str
    sku: str
    quantity: int = Field(gt=0, le=100)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class ResolveBackorderInput(ToolInput):
    """Backorder resolution input."""

    sku: str
    location: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=100_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class UpdateShipmentInput(ToolInput):
    """Shipment lifecycle update input."""

    shipment_id: str
    status: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class ExpirePromotionInput(ToolInput):
    """Promotion expiry input."""

    promotion_code: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class DisambiguateCustomerInput(ToolInput):
    """Customer-record disambiguation input."""

    selected_customer_id: str
    candidate_ids: list[str] = Field(min_length=2, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class TransferInventoryInput(ToolInput):
    """Inventory transfer input."""

    sku: str
    source_location: str
    destination_location: str
    quantity: int = Field(gt=0, le=100_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class CancelOrderInput(ToolInput):
    """Legal order-cancellation input."""

    order_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class CompleteRefundInput(ToolInput):
    """Refund-processor completion input."""

    refund_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class ReopenTicketInput(ToolInput):
    """Explicit audited ticket-reopen input."""

    ticket_id: str
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False
