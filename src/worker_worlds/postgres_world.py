"""Async Postgres-backed deterministic commerce world."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import asyncpg
from pydantic import BaseModel, ValidationError

from worker_worlds.contracts import (
    AuthorizationContext,
    CallId,
    EntityRef,
    EventId,
    JsonValue,
    RunId,
    ScenarioId,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ToolSpec,
    WorldEvent,
    WorldSnapshot,
)
from worker_worlds.database import DatabaseSettings, connect, migrate, validate_namespace
from worker_worlds.domain import TicketStatus, validate_ticket_transition
from worker_worlds.errors import InfrastructureError
from worker_worlds.ids import deterministic_ulid
from worker_worlds.seeding import WORLD_VERSION, build_fixture
from worker_worlds.tool_models import (
    AdjustInventoryInput,
    CreateTicketInput,
    EscalateInput,
    GetInventoryInput,
    GetOrderInput,
    IssueRefundInput,
    SearchOrdersInput,
    SendEmailInput,
    ToolInput,
    UpdateTicketInput,
)

_RUN_SCHEMA_SQL = """
CREATE TABLE {schema}.customers (
 id text PRIMARY KEY, email text NOT NULL UNIQUE, name text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE {schema}.products (
 id text PRIMARY KEY, sku text NOT NULL UNIQUE, title text NOT NULL,
 price_minor bigint NOT NULL CHECK(price_minor >= 0), currency char(3) NOT NULL, active boolean NOT NULL);
CREATE TABLE {schema}.inventory (
 id text PRIMARY KEY, product_id text NOT NULL REFERENCES {schema}.products(id), location text NOT NULL,
 available integer NOT NULL CHECK(available >= 0), reserved integer NOT NULL CHECK(reserved >= 0),
 backorder_allowed boolean NOT NULL, UNIQUE(product_id, location));
CREATE TABLE {schema}.orders (
 id text PRIMARY KEY, customer_id text NOT NULL REFERENCES {schema}.customers(id), status text NOT NULL,
 captured_minor bigint NOT NULL CHECK(captured_minor >= 0), refunded_minor bigint NOT NULL DEFAULT 0,
 currency char(3) NOT NULL, created_at timestamptz NOT NULL,
 CHECK(refunded_minor >= 0 AND refunded_minor <= captured_minor));
CREATE TABLE {schema}.line_items (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES {schema}.orders(id),
 product_id text NOT NULL REFERENCES {schema}.products(id), quantity integer NOT NULL CHECK(quantity > 0),
 unit_price_minor bigint NOT NULL CHECK(unit_price_minor >= 0), currency char(3) NOT NULL);
CREATE TABLE {schema}.refunds (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES {schema}.orders(id), amount_minor bigint NOT NULL CHECK(amount_minor > 0),
 currency char(3) NOT NULL, status text NOT NULL, idempotency_key text NOT NULL UNIQUE,
 input_hash text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE {schema}.shipments (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES {schema}.orders(id), status text NOT NULL,
 quantity integer NOT NULL CHECK(quantity > 0), created_at timestamptz NOT NULL);
CREATE TABLE {schema}.tickets (
 id text PRIMARY KEY, customer_id text NOT NULL REFERENCES {schema}.customers(id),
 order_id text REFERENCES {schema}.orders(id), subject text NOT NULL, status text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE {schema}.emails (
 id text PRIMARY KEY, customer_id text NOT NULL REFERENCES {schema}.customers(id),
 subject text NOT NULL, body text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE {schema}.escalations (
 id text PRIMARY KEY, ticket_id text NOT NULL REFERENCES {schema}.tickets(id), reason text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE {schema}.commerce_facts (
 key text PRIMARY KEY, value jsonb NOT NULL, trust text NOT NULL);
CREATE TABLE {schema}.idempotency (
 key text PRIMARY KEY, tool_name text NOT NULL, input_hash text NOT NULL, result jsonb NOT NULL);
CREATE TABLE {schema}.world_events (
 event_id text PRIMARY KEY, run_id text NOT NULL, scenario_id text NOT NULL, sequence bigint NOT NULL UNIQUE,
 occurred_at timestamptz NOT NULL, event_type text NOT NULL, entity_type text NOT NULL, entity_id text NOT NULL,
 actor_id text NOT NULL, authorization_context jsonb NOT NULL, request_id text, before_state jsonb,
 after_state jsonb, policy_context jsonb NOT NULL, metadata jsonb NOT NULL);
"""

_INPUTS: dict[str, type[ToolInput]] = {
    "get_order": GetOrderInput,
    "search_orders": SearchOrdersInput,
    "get_inventory": GetInventoryInput,
    "issue_refund": IssueRefundInput,
    "create_ticket": CreateTicketInput,
    "update_ticket": UpdateTicketInput,
    "adjust_inventory": AdjustInventoryInput,
    "send_email": SendEmailInput,
    "escalate": EscalateInput,
}
_MUTATIONS = frozenset(
    {"issue_refund", "create_ticket", "update_ticket", "adjust_inventory", "send_email", "escalate"}
)


def snapshot_hash(snapshot: WorldSnapshot) -> str:
    """Hash deterministic state while excluding run-local envelope fields."""
    text = json.dumps(snapshot.state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


class PostgresWorld:
    """Per-run isolated commerce world backed by Postgres transactions."""

    version = WORLD_VERSION
    migration_version = "003"

    def __init__(self, settings: DatabaseSettings, scenario_id: str) -> None:
        """Configure an uninitialized world without opening a connection."""
        self._settings = settings
        self._scenario_id = ScenarioId(scenario_id)
        self._connection: asyncpg.Connection[asyncpg.Record] | None = None
        self._run_id = RunId("")
        self._namespace: str | None = None
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)
        self._closed = False
        self.cleanup_succeeded = False
        self._seed = 0
        self._invoke_lock = asyncio.Lock()
        self._renewal_task: asyncio.Task[None] | None = None

    async def reset(self, *, seed: int, run_id: str) -> WorldSnapshot:
        """Create, migrate, and seed a fresh isolated run namespace."""
        await migrate(self._settings)
        self._connection = await connect(self._settings)
        self._run_id = RunId(run_id)
        ulid = run_id.rsplit("_", maxsplit=1)[-1].lower()
        self._namespace = validate_namespace(f"ww_run_{ulid}")
        self._seed = seed
        fixture = build_fixture(self.version, seed)
        created = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=seed % 365)
        self._clock = created
        expires = datetime.now(UTC) + timedelta(seconds=self._settings.lease_seconds)
        try:
            async with self._connection.transaction():
                await self._connection.execute(
                    "INSERT INTO worker_worlds.run_leases"
                    "(run_id,namespace,world_version,acquired_at,expires_at,active) "
                    "VALUES($1,$2,$3,$4,$5,true)",
                    run_id,
                    self._namespace,
                    self.version,
                    datetime.now(UTC),
                    expires,
                )
                await self._connection.execute(f'CREATE SCHEMA "{self._namespace}"')
                await self._connection.execute(
                    _RUN_SCHEMA_SQL.format(schema=f'"{self._namespace}"')
                )
                await self._insert_fixture(fixture)
        except asyncpg.PostgresError as exc:
            await self._connection.close()
            self._connection = None
            raise InfrastructureError(f"failed to reset isolated world: {exc}") from exc
        self._renewal_task = asyncio.create_task(self._renew_lease())
        return await self.snapshot()

    async def _renew_lease(self) -> None:
        """Keep an active run lease alive until cleanup cancels this task."""
        try:
            while True:
                await asyncio.sleep(self._settings.lease_renewal_seconds)
                if self._connection is None:
                    return
                renewal = await connect(self._settings)
                try:
                    await renewal.execute(
                        "UPDATE worker_worlds.run_leases SET expires_at=$1 "
                        "WHERE run_id=$2 AND active",
                        datetime.now(UTC) + timedelta(seconds=self._settings.lease_seconds),
                        self._run_id,
                    )
                finally:
                    await renewal.close()
        except asyncio.CancelledError:
            return

    async def _insert_fixture(self, fixture: dict[str, list[dict[str, Any]]]) -> None:
        connection, schema = self._ready()
        for customer in fixture["customers"]:
            await connection.execute(
                f"INSERT INTO {schema}.customers VALUES($1,$2,$3,$4)",
                customer["id"],
                customer["email"],
                customer["name"],
                self._as_datetime(customer["created_at"]),
            )
        for product in fixture["products"]:
            await connection.execute(
                f"INSERT INTO {schema}.products VALUES($1,$2,$3,$4,$5,$6)",
                product["id"],
                product["sku"],
                product["title"],
                product["price"]["amount_minor"],
                product["price"]["currency"],
                product["active"],
            )
        for inventory in fixture["inventory"]:
            await connection.execute(
                f"INSERT INTO {schema}.inventory VALUES($1,$2,$3,$4,$5,$6)",
                inventory["id"],
                inventory["product_id"],
                inventory["location"],
                inventory["available"],
                inventory["reserved"],
                inventory["backorder_allowed"],
            )
        for order in fixture["orders"]:
            await connection.execute(
                f"INSERT INTO {schema}.orders VALUES($1,$2,$3,$4,$5,$6,$7)",
                order["id"],
                order["customer_id"],
                order["status"],
                order["captured"]["amount_minor"],
                order["refunded"]["amount_minor"],
                order["captured"]["currency"],
                self._as_datetime(order["created_at"]),
            )
        for line in fixture["line_items"]:
            await connection.execute(
                f"INSERT INTO {schema}.line_items VALUES($1,$2,$3,$4,$5,$6)",
                line["id"],
                line["order_id"],
                line["product_id"],
                line["quantity"],
                line["unit_price"]["amount_minor"],
                line["unit_price"]["currency"],
            )
        for shipment in fixture["shipments"]:
            await connection.execute(
                f"INSERT INTO {schema}.shipments VALUES($1,$2,$3,$4,$5)",
                shipment["id"],
                shipment["order_id"],
                shipment["status"],
                shipment["quantity"],
                self._as_datetime(shipment["created_at"]),
            )
        for ticket in fixture["tickets"]:
            await connection.execute(
                f"INSERT INTO {schema}.tickets VALUES($1,$2,$3,$4,$5,$6)",
                ticket["id"],
                ticket["customer_id"],
                ticket["order_id"],
                ticket["subject"],
                ticket["status"],
                self._as_datetime(ticket["created_at"]),
            )
        for fact in fixture["facts"]:
            await connection.execute(
                f"INSERT INTO {schema}.commerce_facts VALUES($1,$2::jsonb,$3)",
                fact["key"],
                json.dumps(fact["value"]),
                fact["trust"],
            )

    @staticmethod
    def _as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise InfrastructureError("deterministic fixture timestamp is invalid")

    def _ready(self) -> tuple[asyncpg.Connection[asyncpg.Record], str]:
        if self._connection is None or self._namespace is None:
            raise InfrastructureError("world must be reset before use")
        return self._connection, f'"{self._namespace}"'

    async def tools(self, context: AuthorizationContext) -> list[ToolSpec]:
        """Expose strict JSON schemas without database internals."""
        del context
        return [
            ToolSpec(
                name=name,
                description=name.replace("_", " ").capitalize(),
                input_schema=cast(dict[str, JsonValue], model.model_json_schema()),
                output_schema={"type": "object"},
                mutation=name in _MUTATIONS,
            )
            for name, model in _INPUTS.items()
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Validate and dispatch one tool call with typed error provenance."""
        async with self._invoke_lock:
            return await self._invoke(call)

    async def _invoke(self, call: ToolCall) -> ToolResult:
        """Dispatch while holding the per-world mutation ordering lock."""
        started_wall = time.perf_counter()
        started = self._clock
        model_type = _INPUTS.get(call.tool_name)
        if model_type is None:
            return self._error(
                call, "ToolNotFound", f"unknown tool: {call.tool_name}", started, started_wall
            )
        try:
            validated = model_type.model_validate(call.arguments)
            handler = getattr(self, f"_tool_{call.tool_name}")
            output = await handler(call, validated)
            self._clock += timedelta(milliseconds=1)
            return ToolResult(
                call_id=call.id,
                status=ToolResultStatus.SUCCESS,
                output=cast(JsonValue, output),
                started_at=started,
                ended_at=self._clock,
                duration_ms=max(0, int((time.perf_counter() - started_wall) * 1000)),
                audit_metadata={"tool": call.tool_name, "scenario_id": self._scenario_id},
            )
        except ValidationError as exc:
            return self._error(call, "ToolValidationError", str(exc), started, started_wall)
        except ToolRejection as exc:
            return self._error(call, exc.code, str(exc), started, started_wall)
        except asyncpg.PostgresError as exc:
            return self._error(call, "ToolExecutionError", str(exc), started, started_wall)

    def _error(
        self, call: ToolCall, error_type: str, message: str, started: datetime, started_wall: float
    ) -> ToolResult:
        self._clock += timedelta(milliseconds=1)
        return ToolResult(
            call_id=call.id,
            status=ToolResultStatus.ERROR,
            error_type=error_type,
            error_message=message,
            started_at=started,
            ended_at=self._clock,
            duration_ms=max(0, int((time.perf_counter() - started_wall) * 1000)),
            audit_metadata={"tool": call.tool_name, "scenario_id": self._scenario_id},
        )

    async def _tool_get_order(self, call: ToolCall, data: GetOrderInput) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        row = await connection.fetchrow(f"SELECT * FROM {schema}.orders WHERE id=$1", data.order_id)
        if row is None:
            raise ToolRejection("OrderNotFound", f"order {data.order_id} not found")
        if call.authorization.customer_id not in {None, row["customer_id"]}:
            raise ToolRejection("AuthorizationDenied", "customer cannot access this order")
        return self._order_json(row)

    async def _tool_search_orders(
        self, call: ToolCall, data: SearchOrdersInput
    ) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        if call.authorization.customer_id not in {None, data.customer_id}:
            raise ToolRejection("AuthorizationDenied", "customer cannot search these orders")
        rows = await connection.fetch(
            f"SELECT * FROM {schema}.orders WHERE customer_id=$1 ORDER BY id", data.customer_id
        )
        return {"orders": [self._order_json(row) for row in rows]}

    async def _tool_get_inventory(
        self, call: ToolCall, data: GetInventoryInput
    ) -> dict[str, JsonValue]:
        del call
        connection, schema = self._ready()
        row = await connection.fetchrow(
            f"SELECT i.*, p.sku FROM {schema}.inventory i JOIN {schema}.products p ON p.id=i.product_id WHERE p.sku=$1",
            data.sku,
        )
        if row is None:
            raise ToolRejection("InventoryNotFound", f"inventory for {data.sku} not found")
        return {
            "sku": row["sku"],
            "location": row["location"],
            "available": row["available"],
            "reserved": row["reserved"],
        }

    async def _tool_issue_refund(
        self, call: ToolCall, raw: IssueRefundInput
    ) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        input_json = raw.model_dump(mode="json")
        input_hash = self._input_hash(input_json)
        async with connection.transaction():
            prior = await self._existing_idempotency(connection, schema, raw)
            if prior is not None:
                return prior
            order = await connection.fetchrow(
                f"SELECT * FROM {schema}.orders WHERE id=$1 FOR UPDATE", raw.order_id
            )
            if order is None:
                raise ToolRejection("OrderNotFound", f"order {raw.order_id} not found")
            if (
                "refund:own_order" not in call.authorization.scopes
                or call.authorization.customer_id != order["customer_id"]
            ):
                raise ToolRejection(
                    "AuthorizationDenied", "refund is not authorized for this customer"
                )
            if raw.currency != order["currency"]:
                raise ToolRejection("CurrencyMismatch", "refund currency must match order currency")
            if order["status"] not in {"paid", "partially_refunded"}:
                raise ToolRejection(
                    "OrderNotRefundable", f"order status {order['status']} is not refundable"
                )
            refundable = int(order["captured_minor"]) - int(order["refunded_minor"])
            if raw.amount_minor > refundable:
                raise ToolRejection("RefundExceedsBalance", "refund exceeds refundable balance")
            sequence = await self._next_sequence(connection, schema)
            refund_id = self._entity_id("ref", sequence)
            new_refunded = int(order["refunded_minor"]) + raw.amount_minor
            new_status = (
                "refunded" if new_refunded == int(order["captured_minor"]) else "partially_refunded"
            )
            await connection.execute(
                f"INSERT INTO {schema}.refunds VALUES($1,$2,$3,$4,'succeeded',$5,$6,$7)",
                refund_id,
                raw.order_id,
                raw.amount_minor,
                raw.currency,
                raw.idempotency_key,
                input_hash,
                self._clock + timedelta(milliseconds=1),
            )
            await connection.execute(
                f"UPDATE {schema}.orders SET refunded_minor=$1,status=$2 WHERE id=$3",
                new_refunded,
                new_status,
                raw.order_id,
            )
            result: dict[str, JsonValue] = {
                "refund_id": refund_id,
                "order_id": raw.order_id,
                "amount_minor": raw.amount_minor,
                "currency": raw.currency,
                "refunded_minor": new_refunded,
                "idempotent_replay": False,
            }
            await self._append_event(
                connection,
                schema,
                call,
                sequence,
                "refund.issued",
                "refund",
                refund_id,
                {"refunded_minor": int(order["refunded_minor"]), "status": order["status"]},
                result,
                {"rule": "refund_requires_order_owner"},
            )
            await connection.execute(
                f"INSERT INTO {schema}.idempotency VALUES($1,$2,$3,$4::jsonb)",
                raw.idempotency_key,
                call.tool_name,
                input_hash,
                json.dumps(result),
            )
            if raw.inject_failure:
                raise asyncpg.PostgresError("injected transaction failure")
            return result

    async def _tool_create_ticket(
        self, call: ToolCall, raw: CreateTicketInput
    ) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        async with connection.transaction():
            prior = await self._existing_idempotency(connection, schema, raw)
            if prior is not None:
                return prior
            customer = await connection.fetchval(
                f"SELECT id FROM {schema}.customers WHERE id=$1", raw.customer_id
            )
            if customer is None:
                raise ToolRejection("CustomerNotFound", "customer not found")
            if (
                raw.order_id
                and await connection.fetchval(
                    f"SELECT id FROM {schema}.orders WHERE id=$1", raw.order_id
                )
                is None
            ):
                raise ToolRejection("OrderNotFound", "order not found")
            sequence = await self._next_sequence(connection, schema)
            entity_id = self._entity_id("tkt", sequence)
            after: dict[str, JsonValue] = {
                "id": entity_id,
                "customer_id": raw.customer_id,
                "order_id": raw.order_id,
                "subject": raw.subject,
                "status": "open",
            }
            await connection.execute(
                f"INSERT INTO {schema}.tickets VALUES($1,$2,$3,$4,'open',$5)",
                entity_id,
                raw.customer_id,
                raw.order_id,
                raw.subject,
                self._clock,
            )
            await self._append_event(
                connection,
                schema,
                call,
                sequence,
                "ticket.created",
                "ticket",
                entity_id,
                None,
                after,
                {},
            )
            return await self._store_simple_idempotency(connection, schema, call, raw, after)

    async def _tool_update_ticket(
        self, call: ToolCall, raw: UpdateTicketInput
    ) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        try:
            target = TicketStatus(raw.status)
        except ValueError as exc:
            raise ToolRejection(
                "InvalidTicketStatus", f"unknown ticket status: {raw.status}"
            ) from exc
        async with connection.transaction():
            prior = await self._existing_idempotency(connection, schema, raw)
            if prior is not None:
                return prior
            row = await connection.fetchrow(
                f"SELECT * FROM {schema}.tickets WHERE id=$1 FOR UPDATE", raw.ticket_id
            )
            if row is None:
                raise ToolRejection("TicketNotFound", "ticket not found")
            try:
                validate_ticket_transition(TicketStatus(row["status"]), target)
            except ValueError as exc:
                raise ToolRejection("IllegalStatusTransition", str(exc)) from exc
            sequence = await self._next_sequence(connection, schema)
            await connection.execute(
                f"UPDATE {schema}.tickets SET status=$1 WHERE id=$2", target.value, raw.ticket_id
            )
            after: dict[str, JsonValue] = {"id": raw.ticket_id, "status": target.value}
            await self._append_event(
                connection,
                schema,
                call,
                sequence,
                "ticket.updated",
                "ticket",
                raw.ticket_id,
                {"status": row["status"]},
                after,
                {},
            )
            return await self._store_simple_idempotency(connection, schema, call, raw, after)

    async def _tool_adjust_inventory(
        self, call: ToolCall, raw: AdjustInventoryInput
    ) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        async with connection.transaction():
            prior = await self._existing_idempotency(connection, schema, raw)
            if prior is not None:
                return prior
            row = await connection.fetchrow(
                f"SELECT i.* FROM {schema}.inventory i JOIN {schema}.products p ON p.id=i.product_id WHERE p.sku=$1 FOR UPDATE OF i",
                raw.sku,
            )
            if row is None:
                raise ToolRejection("InventoryNotFound", "inventory not found")
            available = int(row["available"]) + raw.delta
            if available < 0 and not row["backorder_allowed"]:
                raise ToolRejection("NegativeInventory", "adjustment would make inventory negative")
            sequence = await self._next_sequence(connection, schema)
            await connection.execute(
                f"UPDATE {schema}.inventory SET available=$1 WHERE id=$2", available, row["id"]
            )
            after: dict[str, JsonValue] = {
                "id": row["id"],
                "sku": raw.sku,
                "available": available,
            }
            await self._append_event(
                connection,
                schema,
                call,
                sequence,
                "inventory.adjusted",
                "inventory",
                row["id"],
                {"available": row["available"]},
                after,
                {},
            )
            return await self._store_simple_idempotency(connection, schema, call, raw, after)

    async def _tool_send_email(self, call: ToolCall, raw: SendEmailInput) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        async with connection.transaction():
            prior = await self._existing_idempotency(connection, schema, raw)
            if prior is not None:
                return prior
            if (
                await connection.fetchval(
                    f"SELECT id FROM {schema}.customers WHERE id=$1", raw.customer_id
                )
                is None
            ):
                raise ToolRejection("CustomerNotFound", "customer not found")
            sequence = await self._next_sequence(connection, schema)
            entity_id = self._entity_id("eml", sequence)
            after: dict[str, JsonValue] = {
                "id": entity_id,
                "customer_id": raw.customer_id,
                "subject": raw.subject,
                "body": raw.body,
            }
            await connection.execute(
                f"INSERT INTO {schema}.emails VALUES($1,$2,$3,$4,$5)",
                entity_id,
                raw.customer_id,
                raw.subject,
                raw.body,
                self._clock,
            )
            await self._append_event(
                connection,
                schema,
                call,
                sequence,
                "email.sent",
                "email",
                entity_id,
                None,
                after,
                {},
            )
            return await self._store_simple_idempotency(connection, schema, call, raw, after)

    async def _tool_escalate(self, call: ToolCall, raw: EscalateInput) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        async with connection.transaction():
            prior = await self._existing_idempotency(connection, schema, raw)
            if prior is not None:
                return prior
            ticket = await connection.fetchrow(
                f"SELECT * FROM {schema}.tickets WHERE id=$1 FOR UPDATE", raw.ticket_id
            )
            if ticket is None:
                raise ToolRejection("TicketNotFound", "ticket not found")
            sequence = await self._next_sequence(connection, schema)
            entity_id = self._entity_id("esc", sequence)
            after: dict[str, JsonValue] = {
                "id": entity_id,
                "ticket_id": raw.ticket_id,
                "reason": raw.reason,
            }
            await connection.execute(
                f"INSERT INTO {schema}.escalations VALUES($1,$2,$3,$4)",
                entity_id,
                raw.ticket_id,
                raw.reason,
                self._clock,
            )
            await connection.execute(
                f"UPDATE {schema}.tickets SET status='escalated' WHERE id=$1", raw.ticket_id
            )
            await self._append_event(
                connection,
                schema,
                call,
                sequence,
                "ticket.escalated",
                "ticket",
                raw.ticket_id,
                {"status": ticket["status"]},
                {"status": "escalated", **after},
                {},
            )
            return await self._store_simple_idempotency(connection, schema, call, raw, after)

    async def _store_simple_idempotency(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        schema: str,
        call: ToolCall,
        raw: BaseModel,
        result: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        input_data = raw.model_dump(mode="json")
        key = str(input_data["idempotency_key"])
        fingerprint = self._input_hash(input_data)
        try:
            await connection.execute(
                f"INSERT INTO {schema}.idempotency VALUES($1,$2,$3,$4::jsonb)",
                key,
                call.tool_name,
                fingerprint,
                json.dumps(result),
            )
        except asyncpg.UniqueViolationError as exc:
            raise ToolRejection("IdempotencyConflict", "idempotency key already used") from exc
        return result

    async def _existing_idempotency(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        schema: str,
        raw: BaseModel,
    ) -> dict[str, JsonValue] | None:
        input_data = raw.model_dump(mode="json")
        key = str(input_data["idempotency_key"])
        fingerprint = self._input_hash(input_data)
        await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
        prior = await connection.fetchrow(
            f"SELECT input_hash,result FROM {schema}.idempotency WHERE key=$1", key
        )
        if prior is None:
            return None
        if prior["input_hash"] != fingerprint:
            raise ToolRejection(
                "IdempotencyConflict", "idempotency key was reused with different input"
            )
        result = (
            json.loads(prior["result"]) if isinstance(prior["result"], str) else prior["result"]
        )
        return cast(dict[str, JsonValue], result)

    async def _next_sequence(
        self, connection: asyncpg.Connection[asyncpg.Record], schema: str
    ) -> int:
        value = await connection.fetchval(
            f"SELECT COALESCE(MAX(sequence),0)+1 FROM {schema}.world_events"
        )
        return int(value)

    async def _append_event(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        schema: str,
        call: ToolCall,
        sequence: int,
        event_type: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, JsonValue] | None,
        after: dict[str, JsonValue] | None,
        policy: dict[str, JsonValue],
    ) -> None:
        event_id = self._entity_id("evt", sequence)
        authorization = call.authorization.model_dump(mode="json")
        await connection.execute(
            f"INSERT INTO {schema}.world_events VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb)",
            event_id,
            self._run_id,
            self._scenario_id,
            sequence,
            self._clock + timedelta(milliseconds=1),
            event_type,
            entity_type,
            entity_id,
            call.authorization.actor_id,
            json.dumps(authorization),
            call.id,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
            json.dumps(policy),
            json.dumps({"tool": call.tool_name}),
        )

    def _entity_id(self, prefix: str, sequence: int) -> str:
        timestamp_ms = int(self._clock.timestamp() * 1000) + sequence
        return f"{prefix}_{deterministic_ulid(timestamp_ms, f'{self._run_id}:{prefix}:{sequence}')}"

    @staticmethod
    def _input_hash(data: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _order_json(row: asyncpg.Record) -> dict[str, JsonValue]:
        return {
            "id": row["id"],
            "customer_id": row["customer_id"],
            "status": row["status"],
            "captured_minor": int(row["captured_minor"]),
            "refunded_minor": int(row["refunded_minor"]),
            "currency": row["currency"],
            "created_at": row["created_at"].isoformat().replace("+00:00", "Z"),
        }

    async def snapshot(self) -> WorldSnapshot:
        """Capture all mutable state in stable entity and key order."""
        connection, schema = self._ready()
        state: dict[str, JsonValue] = {}
        queries = {
            "customers": f"SELECT id,email,name,created_at FROM {schema}.customers ORDER BY id",
            "products": f"SELECT id,sku,title,price_minor,currency,active FROM {schema}.products ORDER BY id",
            "inventory": f"SELECT id,product_id,location,available,reserved,backorder_allowed FROM {schema}.inventory ORDER BY id",
            "orders": f"SELECT * FROM {schema}.orders ORDER BY id",
            "line_items": f"SELECT id,order_id,product_id,quantity,unit_price_minor,currency FROM {schema}.line_items ORDER BY id",
            "refunds": f"SELECT id,order_id,amount_minor,currency,status,idempotency_key,created_at FROM {schema}.refunds ORDER BY id",
            "shipments": f"SELECT id,order_id,status,quantity,created_at FROM {schema}.shipments ORDER BY id",
            "tickets": f"SELECT id,customer_id,order_id,subject,status,created_at FROM {schema}.tickets ORDER BY id",
            "emails": f"SELECT id,customer_id,subject,body,created_at FROM {schema}.emails ORDER BY id",
            "escalations": f"SELECT id,ticket_id,reason,created_at FROM {schema}.escalations ORDER BY id",
            "facts": f"SELECT key,value,trust FROM {schema}.commerce_facts ORDER BY key",
        }
        for name, query in queries.items():
            rows = await connection.fetch(query)
            state[name] = [self._record_json(row) for row in rows]
        size = len(
            json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        if size > self._settings.snapshot_max_bytes:
            raise InfrastructureError(
                f"snapshot size {size} exceeds limit {self._settings.snapshot_max_bytes}"
            )
        sequence = int(
            await connection.fetchval(
                f"SELECT COALESCE(MAX(sequence),0) FROM {schema}.world_events"
            )
        )
        return WorldSnapshot(
            world_name="postgres-commerce",
            world_version=self.version,
            run_id=self._run_id,
            captured_at=self._clock,
            sequence=sequence,
            state=state,
        )

    @staticmethod
    def _record_json(row: asyncpg.Record) -> dict[str, JsonValue]:
        output: dict[str, JsonValue] = {}
        for key, value in row.items():
            if key == "value" and isinstance(value, str):
                output[key] = cast(JsonValue, json.loads(value))
                continue
            output[key] = (
                value.isoformat().replace("+00:00", "Z")
                if isinstance(value, datetime)
                else cast(JsonValue, value)
            )
        return output

    async def events(self, after_sequence: int = 0) -> list[WorldEvent]:
        """Read immutable events in run-local sequence order."""
        connection, schema = self._ready()
        rows = await connection.fetch(
            f"SELECT * FROM {schema}.world_events WHERE sequence>$1 ORDER BY sequence",
            after_sequence,
        )
        return [
            WorldEvent(
                id=EventId(row["event_id"]),
                run_id=RunId(row["run_id"]),
                scenario_id=ScenarioId(row["scenario_id"]),
                sequence=row["sequence"],
                occurred_at=row["occurred_at"],
                event_type=row["event_type"],
                entity=EntityRef(type=row["entity_type"], id=row["entity_id"]),
                actor_id=row["actor_id"],
                authorization=AuthorizationContext.model_validate(
                    self._decoded_json(row["authorization_context"])
                ),
                request_id=CallId(row["request_id"]) if row["request_id"] else None,
                before=self._decoded_json(row["before_state"]),
                after=self._decoded_json(row["after_state"]),
                policy_context=self._decoded_json(row["policy_context"]),
                metadata=self._decoded_json(row["metadata"]),
            )
            for row in rows
        ]

    @staticmethod
    def _decoded_json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    async def advance_time(self, delta: timedelta) -> list[WorldEvent]:
        """Advance controlled world time."""
        if delta.total_seconds() < 0:
            raise ValueError("world time cannot move backwards")
        self._clock += delta
        return []

    async def inject(self, event_type: str, payload: dict[str, JsonValue]) -> WorldEvent:
        """Atomically apply and record one trusted scheduled world event."""
        connection, schema = self._ready()
        entity_type = str(payload.get("entity_type", event_type.split(".", 1)[0]))
        entity_id = str(payload.get("entity_id", "scheduled"))
        async with self._invoke_lock, connection.transaction():
            before: dict[str, JsonValue] | None = None
            after = dict(payload)
            if event_type == "inventory.stockout" and entity_id != "scheduled":
                row = await connection.fetchrow(
                    f"SELECT available FROM {schema}.inventory WHERE id=$1 FOR UPDATE", entity_id
                )
                if row is None:
                    raise InfrastructureError(f"injection target inventory {entity_id} not found")
                before = {"available": int(row["available"])}
                await connection.execute(
                    f"UPDATE {schema}.inventory SET available=0 WHERE id=$1", entity_id
                )
                after["available"] = 0
            sequence = await self._next_sequence(connection, schema)
            event_id = self._entity_id("evt", sequence)
            occurred_at = self._clock + timedelta(milliseconds=1)
            authorization = AuthorizationContext(
                actor_id="world-scheduler", scopes=frozenset({"world:inject"})
            ).model_dump(mode="json")
            await connection.execute(
                f"INSERT INTO {schema}.world_events VALUES"
                "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12::jsonb,$13::jsonb,"
                "$14::jsonb,$15::jsonb)",
                event_id,
                self._run_id,
                self._scenario_id,
                sequence,
                occurred_at,
                event_type,
                entity_type,
                entity_id,
                "world-scheduler",
                json.dumps(authorization),
                None,
                json.dumps(before) if before is not None else None,
                json.dumps(after),
                json.dumps({"source": "scenario_schedule"}),
                json.dumps({"injected": True, "trust": "trusted_runtime"}),
            )
        self._clock = occurred_at
        return (await self.events(after_sequence=sequence - 1))[0]

    async def close(self) -> None:
        """Drop only this validated namespace and release its lease."""
        if self._closed:
            return
        self._closed = True
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            await self._renewal_task
            self._renewal_task = None
        if self._connection is None or self._namespace is None:
            self.cleanup_succeeded = True
            return
        namespace = validate_namespace(self._namespace)
        try:
            async with self._connection.transaction():
                await self._connection.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
                await self._connection.execute(
                    "UPDATE worker_worlds.run_leases SET active=false WHERE run_id=$1", self._run_id
                )
            self.cleanup_succeeded = True
        finally:
            await self._connection.close()
            self._connection = None

    @property
    def lease_renewal_running(self) -> bool:
        """Expose renewal state for reliability verification."""
        return self._renewal_task is not None and not self._renewal_task.done()


class ToolRejection(Exception):
    """Expected typed domain or authorization rejection."""

    def __init__(self, code: str, message: str) -> None:
        """Create a rejection with stable machine code."""
        super().__init__(message)
        self.code = code
