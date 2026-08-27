"""Deterministic commerce supply-chain domain and PostgreSQL world."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from worker_worlds.contracts import JsonValue, ToolCall
from worker_worlds.database import DatabaseSettings
from worker_worlds.ids import deterministic_ulid
from worker_worlds.json_world import JsonPostgresWorld, JsonWorldRejection


class SupplyChainModel(BaseModel):
    """Strict immutable supply-chain value."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_timestamps(cls, value: object) -> object:
        """Require controlled UTC timestamps."""
        offset = value.utcoffset() if isinstance(value, datetime) else None
        if isinstance(value, datetime) and (
            value.tzinfo is None or offset is None or offset.total_seconds() != 0
        ):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value


class SupplierStatus(StrEnum):
    """Supplier lifecycle."""

    ACTIVE = "active"
    DELAYED = "delayed"
    SUSPENDED = "suspended"


class PurchaseOrderStatus(StrEnum):
    """Purchase-order lifecycle."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class Supplier(SupplyChainModel):
    """External inventory supplier."""

    id: str
    name: str
    status: SupplierStatus
    on_time_rate_basis_points: Annotated[int, Field(ge=0, le=10_000)]


class Warehouse(SupplyChainModel):
    """Physical stock location."""

    id: str
    name: str
    region: str


class StockPosition(SupplyChainModel):
    """Non-negative SKU balance at a warehouse."""

    sku: str
    warehouse_id: str
    available: Annotated[int, Field(ge=0)]
    reserved: Annotated[int, Field(ge=0)]


class ReorderPolicy(SupplyChainModel):
    """Deterministic stock threshold policy."""

    sku: str
    warehouse_id: str
    reorder_point: Annotated[int, Field(ge=0)]
    safety_stock: Annotated[int, Field(ge=0)]
    reorder_quantity: Annotated[int, Field(gt=0)]


class PurchaseOrder(SupplyChainModel):
    """Authorized replenishment order."""

    id: str
    supplier_id: str
    warehouse_id: str
    sku: str
    quantity: Annotated[int, Field(gt=0)]
    status: PurchaseOrderStatus
    expected_at: datetime


class Observation(SupplyChainModel):
    """Controlled demand, forecast, or lead-time observation."""

    id: str
    kind: str = Field(pattern="^(demand|forecast|lead_time)$")
    sku: str
    value: Annotated[int, Field(ge=0)]
    observed_at: datetime


class SupplyToolInput(BaseModel):
    """Strict supply-chain tool input."""

    model_config = ConfigDict(extra="forbid", strict=True)


class GetStockoutRiskInput(SupplyToolInput):
    """Stockout analysis request."""

    sku: str
    warehouse_id: str
    inject_delay_ms: Annotated[int, Field(ge=0, le=60_000)] = 0


class RecommendTransferInput(SupplyToolInput):
    """Read-only transfer recommendation request."""

    sku: str
    destination_warehouse_id: str
    quantity: Annotated[int, Field(gt=0)]


class CreatePurchaseOrderInput(SupplyToolInput):
    """Replenishment mutation request."""

    supplier_id: str
    warehouse_id: str
    sku: str
    quantity: Annotated[int, Field(gt=0, le=1_000_000)]
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class ExecuteTransferInput(SupplyToolInput):
    """Authorized stock-transfer mutation."""

    sku: str
    source_warehouse_id: str
    destination_warehouse_id: str
    quantity: Annotated[int, Field(gt=0, le=1_000_000)]
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


class EscalateSupplierDelayInput(SupplyToolInput):
    """Authorized supplier-delay escalation."""

    supplier_id: str
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=128)
    inject_failure: bool = False


def validate_transfer_policy(
    *, available: int, reserved: int, safety_stock: int, quantity: int
) -> int:
    """Return remaining free stock or reject an unsafe transfer as pure domain logic."""
    if quantity <= 0:
        raise ValueError("transfer quantity must be positive")
    free_stock = available - reserved
    if free_stock < quantity:
        raise ValueError("transfer exceeds free stock")
    remaining_free = free_stock - quantity
    if remaining_free < safety_stock:
        raise ValueError("transfer would breach source safety stock")
    return remaining_free


def build_supply_chain_state(seed: int) -> dict[str, JsonValue]:
    """Construct deterministic commerce supply-chain state for world version 1.1."""
    base = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=seed % 365)

    def identifier(prefix: str) -> str:
        identity = deterministic_ulid(int(base.timestamp() * 1000), f"supply:1.1:{seed}:{prefix}")
        return f"{prefix}_{identity}"

    suppliers = (
        Supplier(
            id="sup_primary",
            name="Northwind Supply",
            status=SupplierStatus.DELAYED if seed % 3 == 0 else SupplierStatus.ACTIVE,
            on_time_rate_basis_points=8_200 + seed % 700,
        ),
    )
    warehouses = (
        Warehouse(id="wh_west", name="West DC", region="US-WEST"),
        Warehouse(id="wh_east", name="East DC", region="US-EAST"),
    )
    stock = (
        StockPosition(sku="SKU-2", warehouse_id="wh_west", available=2 + seed % 3, reserved=1),
        StockPosition(sku="SKU-2", warehouse_id="wh_east", available=18 + seed % 5, reserved=2),
    )
    policies = (
        ReorderPolicy(
            sku="SKU-2",
            warehouse_id="wh_west",
            reorder_point=6,
            safety_stock=3,
            reorder_quantity=20,
        ),
        ReorderPolicy(
            sku="SKU-2",
            warehouse_id="wh_east",
            reorder_point=8,
            safety_stock=5,
            reorder_quantity=24,
        ),
    )
    observations = (
        Observation(
            id=identifier("obsd"), kind="demand", sku="SKU-2", value=7 + seed % 4, observed_at=base
        ),
        Observation(
            id=identifier("obsf"),
            kind="forecast",
            sku="SKU-2",
            value=9 + seed % 4,
            observed_at=base,
        ),
        Observation(
            id=identifier("obsl"),
            kind="lead_time",
            sku="SKU-2",
            value=5 + seed % 3,
            observed_at=base,
        ),
    )
    return cast(
        dict[str, JsonValue],
        {
            "suppliers": [item.model_dump(mode="json") for item in suppliers],
            "warehouses": [item.model_dump(mode="json") for item in warehouses],
            "stock_positions": [item.model_dump(mode="json") for item in stock],
            "reorder_policies": [item.model_dump(mode="json") for item in policies],
            "purchase_orders": [],
            "observations": [item.model_dump(mode="json") for item in observations],
            "transfer_recommendations": [],
            "escalations": [],
        },
    )


class SupplyChainWorld(JsonPostgresWorld):
    """PostgreSQL-isolated deterministic commerce supply-chain world."""

    name = "postgres-commerce-supply-chain"
    version = "1.1"
    inputs = {
        "get_stockout_risk": GetStockoutRiskInput,
        "recommend_transfer": RecommendTransferInput,
        "create_purchase_order": CreatePurchaseOrderInput,
        "execute_transfer": ExecuteTransferInput,
        "escalate_supplier_delay": EscalateSupplierDelayInput,
    }
    mutations = frozenset({"create_purchase_order", "execute_transfer", "escalate_supplier_delay"})

    def __init__(self, settings: DatabaseSettings, scenario_id: str) -> None:
        """Create a supply-chain world bound to explicit Postgres settings."""
        super().__init__(settings, scenario_id)
        self._seed = 0

    def build_state(self, seed: int) -> dict[str, JsonValue]:
        """Build versioned deterministic supply-chain state."""
        self._seed = seed
        return build_supply_chain_state(seed)

    def apply_tool(
        self,
        call: ToolCall,
        name: str,
        data: BaseModel,
        state: dict[str, JsonValue],
    ) -> tuple[
        dict[str, JsonValue],
        tuple[
            str, str, str, dict[str, JsonValue] | None, dict[str, JsonValue], dict[str, JsonValue]
        ]
        | None,
    ]:
        """Enforce supply-chain authorization and invariants independently of translation."""
        if name == "get_stockout_risk":
            risk_request = cast(GetStockoutRiskInput, data)
            stock = self._stock(state, risk_request.sku, risk_request.warehouse_id)
            policy = self._policy(state, risk_request.sku, risk_request.warehouse_id)
            available = int(stock["available"]) - int(stock["reserved"])
            return {
                "sku": risk_request.sku,
                "warehouse_id": risk_request.warehouse_id,
                "net_available": available,
                "reorder_point": int(policy["reorder_point"]),
                "at_risk": available <= int(policy["reorder_point"]),
            }, None
        if name == "recommend_transfer":
            recommendation = cast(RecommendTransferInput, data)
            destination = self._stock(
                state, recommendation.sku, recommendation.destination_warehouse_id
            )
            candidates = [
                item
                for item in cast(list[dict[str, Any]], state["stock_positions"])
                if item["sku"] == recommendation.sku
                and item["warehouse_id"] != recommendation.destination_warehouse_id
                and int(item["available"]) - int(item["reserved"]) - recommendation.quantity
                >= int(
                    self._policy(state, recommendation.sku, str(item["warehouse_id"]))[
                        "safety_stock"
                    ]
                )
            ]
            source = (
                sorted(candidates, key=lambda item: str(item["warehouse_id"]))[0]
                if candidates
                else None
            )
            return {
                "recommended": source is not None,
                "source_warehouse_id": source["warehouse_id"] if source else None,
                "destination_warehouse_id": destination["warehouse_id"],
                "quantity": recommendation.quantity,
                "analytical_only": True,
            }, None
        if name == "create_purchase_order":
            self._require_scope(call, "purchase_order:write")
            purchase = cast(CreatePurchaseOrderInput, data)
            supplier = self._entity(state, "suppliers", purchase.supplier_id)
            self._entity(state, "warehouses", purchase.warehouse_id)
            if supplier["status"] == SupplierStatus.SUSPENDED.value:
                raise JsonWorldRejection("SupplierIneligible", "supplier is suspended")
            entity_id = self._entity_id(
                "po", len(cast(list[JsonValue], state["purchase_orders"])) + 1
            )
            after: dict[str, JsonValue] = {
                "id": entity_id,
                "supplier_id": purchase.supplier_id,
                "warehouse_id": purchase.warehouse_id,
                "sku": purchase.sku,
                "quantity": purchase.quantity,
                "status": "submitted",
                "expected_at": (self._clock + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
            }
            cast(list[JsonValue], state["purchase_orders"]).append(after)
            return after, (
                "purchase_order.created",
                "purchase_order",
                entity_id,
                None,
                after,
                {"rule": "purchase_order_requires_authorized_scope"},
            )
        if name == "execute_transfer":
            self._require_scope(call, "inventory:transfer")
            transfer = cast(ExecuteTransferInput, data)
            if transfer.source_warehouse_id == transfer.destination_warehouse_id:
                raise JsonWorldRejection("InvalidTransfer", "source and destination must differ")
            source = self._stock(state, transfer.sku, transfer.source_warehouse_id)
            destination = self._stock(state, transfer.sku, transfer.destination_warehouse_id)
            transfer_before: dict[str, JsonValue] = {
                "source_available": int(source["available"]),
                "destination_available": int(destination["available"]),
            }
            source_policy = self._policy(state, transfer.sku, transfer.source_warehouse_id)
            try:
                validate_transfer_policy(
                    available=int(source["available"]),
                    reserved=int(source["reserved"]),
                    safety_stock=int(source_policy["safety_stock"]),
                    quantity=transfer.quantity,
                )
            except ValueError as exc:
                code = "InsufficientStock" if "free stock" in str(exc) else "SafetyStockViolation"
                raise JsonWorldRejection(code, str(exc)) from exc
            source["available"] = int(source["available"]) - transfer.quantity
            destination["available"] = int(destination["available"]) + transfer.quantity
            transfer_after: dict[str, JsonValue] = {
                "sku": transfer.sku,
                "source_warehouse_id": transfer.source_warehouse_id,
                "destination_warehouse_id": transfer.destination_warehouse_id,
                "quantity": transfer.quantity,
                "source_available": int(source["available"]),
                "destination_available": int(destination["available"]),
            }
            return transfer_after, (
                "inventory.transferred",
                "stock_position",
                f"{transfer.sku}:{transfer.source_warehouse_id}",
                transfer_before,
                transfer_after,
                {"rule": "transfer_requires_free_stock"},
            )
        self._require_scope(call, "supplier:escalate")
        escalation = cast(EscalateSupplierDelayInput, data)
        self._entity(state, "suppliers", escalation.supplier_id)
        entity_id = self._entity_id("esc", len(cast(list[JsonValue], state["escalations"])) + 1)
        escalation_after: dict[str, JsonValue] = {
            "id": entity_id,
            "supplier_id": escalation.supplier_id,
            "reason": escalation.reason,
        }
        cast(list[JsonValue], state["escalations"]).append(escalation_after)
        return escalation_after, (
            "supplier.delay_escalated",
            "supplier",
            escalation.supplier_id,
            None,
            escalation_after,
            {"rule": "supplier_escalation_requires_scope"},
        )

    @staticmethod
    def _require_scope(call: ToolCall, scope: str) -> None:
        if scope not in call.authorization.scopes:
            raise JsonWorldRejection("AuthorizationDenied", f"missing required scope: {scope}")

    @staticmethod
    def _entity(state: dict[str, JsonValue], collection: str, entity_id: str) -> dict[str, Any]:
        entity = next(
            (
                item
                for item in cast(list[dict[str, Any]], state[collection])
                if item["id"] == entity_id
            ),
            None,
        )
        if entity is None:
            raise JsonWorldRejection("EntityNotFound", f"{collection} entity not found")
        return entity

    @staticmethod
    def _stock(state: dict[str, JsonValue], sku: str, warehouse_id: str) -> dict[str, Any]:
        item = next(
            (
                row
                for row in cast(list[dict[str, Any]], state["stock_positions"])
                if row["sku"] == sku and row["warehouse_id"] == warehouse_id
            ),
            None,
        )
        if item is None:
            raise JsonWorldRejection("StockNotFound", "stock position not found")
        return item

    @staticmethod
    def _policy(state: dict[str, JsonValue], sku: str, warehouse_id: str) -> dict[str, Any]:
        item = next(
            (
                row
                for row in cast(list[dict[str, Any]], state["reorder_policies"])
                if row["sku"] == sku and row["warehouse_id"] == warehouse_id
            ),
            None,
        )
        if item is None:
            raise JsonWorldRejection("PolicyNotFound", "reorder policy not found")
        return item
