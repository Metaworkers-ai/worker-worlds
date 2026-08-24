"""Reusable transactional PostgreSQL spine for deterministic enterprise worlds."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import cast

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
from worker_worlds.errors import InfrastructureError
from worker_worlds.ids import deterministic_ulid

_SCHEMA_SQL = """
CREATE TABLE {schema}.world_state (
 id integer PRIMARY KEY CHECK(id=1), state jsonb NOT NULL);
CREATE TABLE {schema}.idempotency (
 key text PRIMARY KEY, tool_name text NOT NULL, input_hash text NOT NULL,
 actor_id text NOT NULL, result jsonb NOT NULL);
CREATE TABLE {schema}.world_events (
 event_id text PRIMARY KEY, run_id text NOT NULL, scenario_id text NOT NULL,
 sequence bigint NOT NULL UNIQUE, occurred_at timestamptz NOT NULL,
 event_type text NOT NULL, entity_type text NOT NULL, entity_id text NOT NULL,
 actor_id text NOT NULL, authorization_context jsonb NOT NULL, request_id text,
 before_state jsonb, after_state jsonb, policy_context jsonb NOT NULL,
 metadata jsonb NOT NULL);
"""


class JsonWorldRejection(Exception):
    """Typed expected rejection from domain behavior."""

    def __init__(self, code: str, message: str) -> None:
        """Create a stable machine-readable rejection."""
        super().__init__(message)
        self.code = code


class JsonPostgresWorld(ABC):
    """Atomic state/event/idempotency implementation for one isolated run."""

    migration_version = "006"
    name: str
    version: str
    inputs: dict[str, type[BaseModel]]
    mutations: frozenset[str]

    def __init__(self, settings: DatabaseSettings, scenario_id: str) -> None:
        """Configure an unopened deterministic world."""
        settings.validate()
        self._settings = settings
        self._scenario_id = ScenarioId(scenario_id)
        self._connection: asyncpg.Connection[asyncpg.Record] | None = None
        self._namespace: str | None = None
        self._run_id = RunId("")
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)
        self._invoke_lock = asyncio.Lock()
        self._renewal_task: asyncio.Task[None] | None = None
        self._closed = False
        self.cleanup_succeeded = False

    @abstractmethod
    def build_state(self, seed: int) -> dict[str, JsonValue]:
        """Build pure deterministic initial state."""

    @abstractmethod
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
        """Apply domain behavior to a copied state and optionally describe its event."""

    async def reset(self, *, seed: int, run_id: str) -> WorldSnapshot:
        """Create a leased isolated namespace and seed deterministic state."""
        await migrate(self._settings)
        self._connection = await connect(self._settings)
        self._run_id = RunId(run_id)
        ulid = run_id.rsplit("_", maxsplit=1)[-1].lower()
        self._namespace = validate_namespace(f"ww_run_{ulid}")
        self._clock = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=seed % 365)
        state = self.build_state(seed)
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
                schema = f'"{self._namespace}"'
                await self._connection.execute(f"CREATE SCHEMA {schema}")
                await self._connection.execute(_SCHEMA_SQL.format(schema=schema))
                await self._connection.execute(
                    f"INSERT INTO {schema}.world_state VALUES(1,$1::jsonb)",
                    json.dumps(state, sort_keys=True, separators=(",", ":")),
                )
        except asyncpg.PostgresError as exc:
            await self._connection.close()
            self._connection = None
            raise InfrastructureError(f"failed to reset isolated {self.name} world") from exc
        self._renewal_task = asyncio.create_task(self._renew_lease())
        return await self.snapshot()

    async def _renew_lease(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._settings.lease_renewal_seconds)
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

    def _ready(self) -> tuple[asyncpg.Connection[asyncpg.Record], str]:
        if self._connection is None or self._namespace is None:
            raise InfrastructureError("world must be reset before use")
        return self._connection, f'"{self._namespace}"'

    async def tools(self, context: AuthorizationContext) -> list[ToolSpec]:
        """Expose only strict public schemas, never persistence internals."""
        del context
        return [
            ToolSpec(
                name=name,
                description=name.replace("_", " ").capitalize(),
                input_schema=cast(dict[str, JsonValue], model.model_json_schema()),
                output_schema={"type": "object"},
                mutation=name in self.mutations,
            )
            for name, model in sorted(self.inputs.items())
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Validate and execute a tool with complete typed provenance."""
        async with self._invoke_lock:
            started_wall = time.perf_counter()
            started = self._clock
            if call.run_id != self._run_id:
                return self._error(call, "RunMismatch", "tool call run ID does not match world")
            model = self.inputs.get(call.tool_name)
            if model is None:
                return self._error(call, "ToolNotFound", f"unknown tool: {call.tool_name}")
            try:
                data = model.model_validate(call.arguments)
                output = await self._execute(call, data)
                self._clock += timedelta(milliseconds=1)
                return ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.SUCCESS,
                    output=output,
                    started_at=started,
                    ended_at=self._clock,
                    duration_ms=max(0, int((time.perf_counter() - started_wall) * 1000)),
                    audit_metadata={"tool": call.tool_name, "scenario_id": self._scenario_id},
                )
            except ValidationError as exc:
                return self._error(call, "ToolValidationError", str(exc))
            except JsonWorldRejection as exc:
                return self._error(call, exc.code, str(exc))
            except asyncpg.PostgresError:
                return self._error(call, "ToolExecutionError", "database tool execution failed")

    async def _execute(self, call: ToolCall, data: BaseModel) -> dict[str, JsonValue]:
        connection, schema = self._ready()
        mutation = call.tool_name in self.mutations
        arguments = data.model_dump(mode="json")
        key = arguments.get("idempotency_key")
        if mutation and (not isinstance(key, str) or call.idempotency_key not in {None, key}):
            raise JsonWorldRejection(
                "IdempotencyKeyMismatch", "mutation envelope and arguments must share a key"
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "tool": call.tool_name,
                    "arguments": arguments,
                    "authorization": call.authorization.model_dump(mode="json"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        async with connection.transaction():
            if mutation:
                await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
                prior = await connection.fetchrow(
                    f"SELECT * FROM {schema}.idempotency WHERE key=$1", key
                )
                if prior is not None:
                    if prior["tool_name"] != call.tool_name or prior["input_hash"] != fingerprint:
                        raise JsonWorldRejection(
                            "IdempotencyConflict", "idempotency key was reused with different input"
                        )
                    if prior["actor_id"] != call.authorization.actor_id:
                        raise JsonWorldRejection(
                            "AuthorizationDenied", "idempotent result belongs to another actor"
                        )
                    result = prior["result"]
                    return cast(
                        dict[str, JsonValue],
                        json.loads(result) if isinstance(result, str) else result,
                    )
            raw_state = await connection.fetchval(
                f"SELECT state FROM {schema}.world_state WHERE id=1 FOR UPDATE"
            )
            state = cast(
                dict[str, JsonValue],
                json.loads(raw_state) if isinstance(raw_state, str) else copy.deepcopy(raw_state),
            )
            output, event = self.apply_tool(call, call.tool_name, data, state)
            if not mutation:
                return output
            sequence = int(
                await connection.fetchval(
                    f"SELECT COALESCE(MAX(sequence),0)+1 FROM {schema}.world_events"
                )
            )
            if event is None:
                raise InfrastructureError("mutation did not produce event evidence")
            event_type, entity_type, entity_id, before, after, policy = event
            await connection.execute(
                f"UPDATE {schema}.world_state SET state=$1::jsonb WHERE id=1",
                json.dumps(state, sort_keys=True, separators=(",", ":")),
            )
            event_id = self._entity_id("evt", sequence)
            await connection.execute(
                f"INSERT INTO {schema}.world_events VALUES"
                "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12::jsonb,$13::jsonb,"
                "$14::jsonb,$15::jsonb)",
                event_id,
                self._run_id,
                self._scenario_id,
                sequence,
                self._clock + timedelta(milliseconds=1),
                event_type,
                entity_type,
                entity_id,
                call.authorization.actor_id,
                json.dumps(call.authorization.model_dump(mode="json")),
                call.id,
                json.dumps(before) if before is not None else None,
                json.dumps(after),
                json.dumps(policy),
                json.dumps({"tool": call.tool_name}),
            )
            await connection.execute(
                f"INSERT INTO {schema}.idempotency VALUES($1,$2,$3,$4,$5::jsonb)",
                key,
                call.tool_name,
                fingerprint,
                call.authorization.actor_id,
                json.dumps(output),
            )
            if bool(getattr(data, "inject_failure", False)):
                raise asyncpg.PostgresError("injected transaction failure")
            return output

    def _error(self, call: ToolCall, error_type: str, message: str) -> ToolResult:
        started = self._clock
        self._clock += timedelta(milliseconds=1)
        return ToolResult(
            call_id=call.id,
            status=ToolResultStatus.ERROR,
            error_type=error_type,
            error_message=message,
            started_at=started,
            ended_at=self._clock,
            duration_ms=0,
            audit_metadata={"tool": call.tool_name, "scenario_id": self._scenario_id},
        )

    def _entity_id(self, prefix: str, sequence: int) -> str:
        timestamp_ms = int(self._clock.timestamp() * 1000) + sequence
        return f"{prefix}_{deterministic_ulid(timestamp_ms, f'{self._run_id}:{prefix}:{sequence}')}"

    async def snapshot(self) -> WorldSnapshot:
        """Capture canonical state from the isolated namespace."""
        connection, schema = self._ready()
        raw = await connection.fetchval(f"SELECT state FROM {schema}.world_state WHERE id=1")
        state = cast(dict[str, JsonValue], json.loads(raw) if isinstance(raw, str) else raw)
        size = len(json.dumps(state, sort_keys=True, separators=(",", ":")).encode())
        if size > self._settings.snapshot_max_bytes:
            raise InfrastructureError("world snapshot exceeds configured size limit")
        sequence = int(
            await connection.fetchval(
                f"SELECT COALESCE(MAX(sequence),0) FROM {schema}.world_events"
            )
        )
        return WorldSnapshot(
            world_name=self.name,
            world_version=self.version,
            run_id=self._run_id,
            captured_at=self._clock,
            sequence=sequence,
            state=state,
        )

    async def events(self, after_sequence: int = 0) -> list[WorldEvent]:
        """Return stable run-local append-only event ordering."""
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
                sequence=int(row["sequence"]),
                occurred_at=row["occurred_at"],
                event_type=row["event_type"],
                entity=EntityRef(type=row["entity_type"], id=row["entity_id"]),
                actor_id=row["actor_id"],
                authorization=AuthorizationContext.model_validate(
                    self._decoded_json(row["authorization_context"])
                ),
                request_id=CallId(row["request_id"]) if row["request_id"] else None,
                before=cast(dict[str, JsonValue] | None, self._decoded_json(row["before_state"])),
                after=cast(dict[str, JsonValue] | None, self._decoded_json(row["after_state"])),
                policy_context=cast(
                    dict[str, JsonValue], self._decoded_json(row["policy_context"])
                ),
                metadata=cast(dict[str, JsonValue], self._decoded_json(row["metadata"])),
            )
            for row in rows
        ]

    @staticmethod
    def _decoded_json(value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value

    async def advance_time(self, delta: timedelta) -> list[WorldEvent]:
        """Advance controlled time without wall-clock dependency."""
        if delta.total_seconds() < 0:
            raise ValueError("world time cannot move backwards")
        self._clock += delta
        return []

    async def inject(self, event_type: str, payload: dict[str, JsonValue]) -> WorldEvent:
        """Reject unsupported domain injections explicitly."""
        del event_type, payload
        raise InfrastructureError(f"{self.name} does not support scheduled injections")

    async def close(self) -> None:
        """Drop only this exact validated namespace and release its lease."""
        if self._closed:
            return
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            await self._renewal_task
            self._renewal_task = None
        if self._connection is None or self._namespace is None:
            self.cleanup_succeeded = True
            self._closed = True
            return
        namespace = validate_namespace(self._namespace)
        try:
            async with self._connection.transaction():
                await self._connection.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
                await self._connection.execute(
                    "DELETE FROM worker_worlds.run_leases WHERE run_id=$1", self._run_id
                )
            self.cleanup_succeeded = True
            self._closed = True
        finally:
            await self._connection.close()
            self._connection = None
