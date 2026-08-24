"""PostgreSQL-backed durable suite job state and progress."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, cast

import asyncpg
from pydantic import Field

from worker_worlds.catalog import CatalogId, SemanticVersion
from worker_worlds.contracts import Contract, JsonValue, RunId, ScenarioId
from worker_worlds.database import DatabaseSettings, connect, migrate
from worker_worlds.ids import prefixed_ulid


class SuiteJobStatus(StrEnum):
    """Legal persisted suite-job lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class SuiteScenarioStatus(StrEnum):
    """Persisted state for one scenario inside a suite job."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"


class SuiteScenarioRecord(Contract):
    """Restart-safe progress for one suite scenario."""

    scenario_id: ScenarioId
    ordinal: Annotated[int, Field(ge=0)]
    status: SuiteScenarioStatus
    attempts: Annotated[int, Field(ge=0)] = 0
    run_id: RunId | None = None
    run_record_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    terminal_reason: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    updated_at: datetime


class SuiteBudget(Contract):
    """Aggregate limits enforced across every attempt in one suite job."""

    deadline_s: Annotated[int, Field(gt=0, le=86_400)] = 3_600
    scenarios: Annotated[int, Field(gt=0, le=500)] = 200
    tool_calls: Annotated[int, Field(ge=0)] = 10_000
    model_tokens: Annotated[int, Field(ge=0)] = 10_000_000
    mutations: Annotated[int, Field(ge=0)] = 10_000
    cost_minor: Annotated[int, Field(ge=0)] = 0


class SuiteJobRecord(Contract):
    """Durable mutable-job snapshot kept separate from final SuiteRecord."""

    id: str
    request_key: str
    status: SuiteJobStatus
    catalog_version: SemanticVersion
    domain_id: CatalogId
    role_id: CatalogId
    suite_id: CatalogId
    suite_revision: SemanticVersion
    agent_id: str
    world: str
    configuration: dict[str, JsonValue]
    total_scenarios: Annotated[int, Field(ge=0)]
    completed_scenarios: Annotated[int, Field(ge=0)]
    passed_scenarios: Annotated[int, Field(ge=0)]
    failed_scenarios: Annotated[int, Field(ge=0)]
    cancel_requested: bool
    revision: Annotated[int, Field(ge=0)]
    scenarios: tuple[SuiteScenarioRecord, ...]
    error_type: str | None = None
    error_message: str | None = None
    suite_record_path: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    executor_id: str | None = None
    executor_expires_at: datetime | None = None


class SuiteJobCreate(Contract):
    """Server-validated immutable input for one durable suite job."""

    request_key: str = Field(min_length=1, max_length=200)
    catalog_version: SemanticVersion
    domain_id: CatalogId
    role_id: CatalogId
    suite_id: CatalogId
    suite_revision: SemanticVersion
    agent_id: str = Field(min_length=1, max_length=200)
    world: str = Field(min_length=1, max_length=100)
    scenario_ids: tuple[ScenarioId, ...]
    configuration: dict[str, JsonValue]


class PostgresSuiteJobRepository:
    """Transactional suite-job repository using the explicit Worker Worlds database."""

    def __init__(self, settings: DatabaseSettings, *, lease_seconds: int = 30) -> None:
        """Bind repository operations to validated database settings."""
        settings.validate()
        if lease_seconds < 5 or lease_seconds > 300:
            raise ValueError("suite executor lease must be between 5 and 300 seconds")
        self._settings = settings
        self._executor_id = prefixed_ulid("executor")
        self._lease_seconds = lease_seconds

    @property
    def heartbeat_seconds(self) -> float:
        """Return the safe renewal cadence for this repository's executor lease."""
        return self._lease_seconds / 3

    @property
    def executor_id(self) -> str:
        """Return this repository instance's opaque fencing identity."""
        return self._executor_id

    async def create(self, request: SuiteJobCreate) -> SuiteJobRecord:
        """Create an idempotent queued job and its deterministic scenario rows."""
        await migrate(self._settings)
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        identity = hashlib.sha256(request.canonical_json().encode()).hexdigest()
        job_id = "suitejob_" + prefixed_ulid("job").split("_", 1)[1]
        try:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", request.request_key
                )
                existing = await connection.fetchval(
                    "SELECT id FROM worker_worlds.suite_jobs WHERE request_key=$1",
                    request.request_key,
                )
                if existing is not None:
                    existing_job = await self._get_with_connection(connection, str(existing))
                    stored = str(existing_job.configuration.get("request_hash", ""))
                    if stored != identity:
                        raise ValueError("suite request key was reused with conflicting input")
                    return existing_job
                configuration = {**request.configuration, "request_hash": identity}
                await connection.execute(
                    "INSERT INTO worker_worlds.suite_jobs "
                    "(id,request_key,state,catalog_version,domain_id,role_id,suite_id,"
                    "suite_revision,agent_id,world,configuration,total_scenarios,"
                    "created_at,updated_at) "
                    "VALUES($1,$2,'queued',$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$12)",
                    job_id,
                    request.request_key,
                    request.catalog_version,
                    request.domain_id,
                    request.role_id,
                    request.suite_id,
                    request.suite_revision,
                    request.agent_id,
                    request.world,
                    json.dumps(configuration),
                    len(request.scenario_ids),
                    now,
                )
                await connection.executemany(
                    "INSERT INTO worker_worlds.suite_job_scenarios "
                    "(suite_job_id,scenario_id,ordinal,state,updated_at) "
                    "VALUES($1,$2,$3,'pending',$4)",
                    [
                        (job_id, str(scenario_id), ordinal, now)
                        for ordinal, scenario_id in enumerate(request.scenario_ids)
                    ],
                )
                return await self._get_with_connection(connection, job_id)
        finally:
            await connection.close()

    async def get(self, job_id: str) -> SuiteJobRecord:
        """Load one job and every scenario progress row."""
        connection = await connect(self._settings)
        try:
            return await self._get_with_connection(connection, job_id)
        finally:
            await connection.close()

    async def list(self, *, limit: int = 100) -> tuple[SuiteJobRecord, ...]:
        """List newest jobs with complete per-scenario progress."""
        if limit <= 0 or limit > 500:
            raise ValueError("suite job list limit must be between 1 and 500")
        connection = await connect(self._settings)
        try:
            identifiers = await connection.fetch(
                "SELECT id FROM worker_worlds.suite_jobs ORDER BY created_at DESC,id DESC LIMIT $1",
                limit,
            )
            return tuple(
                [
                    await self._get_with_connection(connection, str(item["id"]))
                    for item in identifiers
                ]
            )
        finally:
            await connection.close()

    async def claim(self, job_id: str) -> bool:
        """Claim queued work or safely take over a job whose executor lease expired."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._lease_seconds)
        try:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT state,executor_expires_at FROM worker_worlds.suite_jobs "
                    "WHERE id=$1 FOR UPDATE",
                    job_id,
                )
                if row is None:
                    raise KeyError(f"suite job not found: {job_id}")
                state = str(row["state"])
                expired = row["executor_expires_at"] is None or row["executor_expires_at"] <= now
                if state == "running" and expired:
                    await connection.execute(
                        "UPDATE worker_worlds.suite_job_scenarios SET state='pending',"
                        "updated_at=$2 WHERE suite_job_id=$1 AND state='running'",
                        job_id,
                        now,
                    )
                elif state != "queued":
                    return False
                await connection.execute(
                    "UPDATE worker_worlds.suite_jobs SET state='running',"
                    "started_at=COALESCE(started_at,$2),updated_at=$2,revision=revision+1,"
                    "executor_id=$3,executor_expires_at=$4 WHERE id=$1",
                    job_id,
                    now,
                    self._executor_id,
                    expires_at,
                )
                return True
        finally:
            await connection.close()

    async def heartbeat(self, job_id: str) -> bool:
        """Extend this executor's lease without changing user-visible progress."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            status = await connection.execute(
                "UPDATE worker_worlds.suite_jobs SET executor_expires_at=$3,updated_at=$2 "
                "WHERE id=$1 AND executor_id=$4 AND state IN ('running','cancelling')",
                job_id,
                now,
                now + timedelta(seconds=self._lease_seconds),
                self._executor_id,
            )
            return status.endswith("1")
        finally:
            await connection.close()

    async def recoverable(self) -> tuple[SuiteJobRecord, ...]:
        """List queued jobs and active jobs whose owning process stopped heartbeating."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            rows = await connection.fetch(
                "SELECT id FROM worker_worlds.suite_jobs WHERE state='queued' OR "
                "(state IN ('running','cancelling') AND "
                "(executor_expires_at IS NULL OR executor_expires_at <= $1)) "
                "ORDER BY created_at,id",
                now,
            )
            return tuple(
                [await self._get_with_connection(connection, str(row["id"])) for row in rows]
            )
        finally:
            await connection.close()

    async def terminal_without_evidence(self) -> tuple[SuiteJobRecord, ...]:
        """List terminal jobs whose crash-safe evidence publication is unfinished."""
        connection = await connect(self._settings)
        try:
            rows = await connection.fetch(
                "SELECT id FROM worker_worlds.suite_jobs "
                "WHERE state IN ('cancelled','completed','failed') "
                "AND suite_record_path IS NULL ORDER BY created_at,id"
            )
            return tuple(
                [await self._get_with_connection(connection, str(row["id"])) for row in rows]
            )
        finally:
            await connection.close()

    async def finalize_abandoned_cancellation(self, job_id: str) -> SuiteJobRecord:
        """Terminalize a cancelling job only after its prior executor lease expired."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            async with connection.transaction():
                updated = await connection.execute(
                    "UPDATE worker_worlds.suite_jobs SET executor_id=$3,executor_expires_at=$4 "
                    "WHERE id=$1 AND state='cancelling' AND "
                    "(executor_expires_at IS NULL OR executor_expires_at <= $2)",
                    job_id,
                    now,
                    self._executor_id,
                    now + timedelta(seconds=self._lease_seconds),
                )
                if not updated.endswith("1"):
                    return await self._get_with_connection(connection, job_id)
            return await self.finish(job_id)
        finally:
            await connection.close()

    async def scenario_started(self, job_id: str, scenario_id: ScenarioId) -> bool:
        """Claim one pending scenario unless cancellation has begun."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            status = await connection.execute(
                "UPDATE worker_worlds.suite_job_scenarios s SET state='running',"
                "attempts=attempts+1,updated_at=$3 FROM worker_worlds.suite_jobs j "
                "WHERE s.suite_job_id=j.id AND s.suite_job_id=$1 AND s.scenario_id=$2 "
                "AND s.state='pending' AND j.state='running' AND NOT j.cancel_requested "
                "AND j.executor_id=$4",
                job_id,
                str(scenario_id),
                now,
                self._executor_id,
            )
            return status.endswith("1")
        finally:
            await connection.close()

    async def scenario_retrying(self, job_id: str, scenario_id: ScenarioId) -> bool:
        """Record a bounded retry while keeping the scenario actively claimed."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            status = await connection.execute(
                "UPDATE worker_worlds.suite_job_scenarios s SET attempts=attempts+1,"
                "updated_at=$3 FROM worker_worlds.suite_jobs j "
                "WHERE s.suite_job_id=j.id AND s.suite_job_id=$1 AND s.scenario_id=$2 "
                "AND s.state='running' AND j.state='running' AND NOT j.cancel_requested "
                "AND j.executor_id=$4",
                job_id,
                str(scenario_id),
                now,
                self._executor_id,
            )
            return status.endswith("1")
        finally:
            await connection.close()

    async def register_run_evidence(
        self,
        job_id: str,
        scenario_id: ScenarioId,
        run_id: RunId,
        record_hash: str,
        relative_path: str,
    ) -> bool:
        """Register immutable attempt evidence only while this executor owns the lease."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            status = await connection.execute(
                "INSERT INTO worker_worlds.suite_run_evidence "
                "(suite_job_id,scenario_id,run_id,record_hash,relative_path,executor_id,"
                "created_at) "
                "SELECT $1,$2,$3,$4,$5,$6,$7 FROM worker_worlds.suite_jobs j "
                "WHERE j.id=$1 AND j.state='running' AND j.executor_id=$6 "
                "ON CONFLICT (suite_job_id,run_id) DO NOTHING",
                job_id,
                str(scenario_id),
                str(run_id),
                record_hash,
                relative_path,
                self._executor_id,
                now,
            )
            return status.endswith("1")
        finally:
            await connection.close()

    async def run_evidence(self, job_id: str) -> tuple[tuple[str, str, str], ...]:
        """Return registered run ID, hash, and relative path in creation order."""
        connection = await connect(self._settings)
        try:
            rows = await connection.fetch(
                "SELECT run_id,record_hash,relative_path FROM worker_worlds.suite_run_evidence "
                "WHERE suite_job_id=$1 ORDER BY created_at,run_id",
                job_id,
            )
            return tuple(
                (str(row["run_id"]), str(row["record_hash"]), str(row["relative_path"]))
                for row in rows
            )
        finally:
            await connection.close()

    async def scenario_finished(
        self,
        job_id: str,
        scenario_id: ScenarioId,
        *,
        status: SuiteScenarioStatus,
        run_id: RunId,
        record_hash: str,
        terminal_reason: str,
        error_type: str | None,
        error_message: str | None,
    ) -> bool:
        """Persist one terminal scenario result and monotonic aggregate progress."""
        if status not in {
            SuiteScenarioStatus.PASSED,
            SuiteScenarioStatus.FAILED,
            SuiteScenarioStatus.ERROR,
        }:
            raise ValueError("scenario result must be terminal")
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            async with connection.transaction():
                updated = await connection.execute(
                    "UPDATE worker_worlds.suite_job_scenarios SET state=$3,run_id=$4,"
                    "run_record_hash=$5,terminal_reason=$6,error_type=$7,error_message=$8,"
                    "updated_at=$9 WHERE suite_job_id=$1 AND scenario_id=$2 AND state='running' "
                    "AND EXISTS (SELECT 1 FROM worker_worlds.suite_jobs j WHERE j.id=$1 "
                    "AND j.state='running' AND j.executor_id=$10)",
                    job_id,
                    str(scenario_id),
                    status.value,
                    str(run_id),
                    record_hash,
                    terminal_reason,
                    error_type,
                    error_message,
                    now,
                    self._executor_id,
                )
                if not updated.endswith("1"):
                    return False
                passed = 1 if status is SuiteScenarioStatus.PASSED else 0
                failed = 0 if status is SuiteScenarioStatus.PASSED else 1
                await connection.execute(
                    "UPDATE worker_worlds.suite_jobs SET completed_scenarios="
                    "completed_scenarios+1,passed_scenarios=passed_scenarios+$2,"
                    "failed_scenarios=failed_scenarios+$3,updated_at=$4,revision=revision+1 "
                    "WHERE id=$1",
                    job_id,
                    passed,
                    failed,
                    now,
                )
                return True
        finally:
            await connection.close()

    async def request_cancel(self, job_id: str) -> SuiteJobRecord:
        """Idempotently request cancellation and terminalize queued work."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE worker_worlds.suite_jobs SET cancel_requested=true,"
                    "state=CASE WHEN state='queued' THEN 'cancelled' "
                    "WHEN state='running' THEN 'cancelling' ELSE state END,"
                    "ended_at=CASE WHEN state='queued' THEN $2 ELSE ended_at END,"
                    "updated_at=$2,revision=revision+1 WHERE id=$1 "
                    "AND state IN ('queued','running')",
                    job_id,
                    now,
                )
                await connection.execute(
                    "UPDATE worker_worlds.suite_job_scenarios SET state='cancelled',updated_at=$2 "
                    "WHERE suite_job_id=$1 AND state='pending'",
                    job_id,
                    now,
                )
                return await self._get_with_connection(connection, job_id)
        finally:
            await connection.close()

    async def finish(
        self,
        job_id: str,
        *,
        suite_record_path: str | None = None,
        error: Exception | None = None,
    ) -> SuiteJobRecord:
        """Move a claimed job to exactly one terminal state."""
        connection = await connect(self._settings)
        now = datetime.now(UTC)
        try:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT state,cancel_requested,executor_id FROM worker_worlds.suite_jobs "
                    "WHERE id=$1 FOR UPDATE",
                    job_id,
                )
                if row is None:
                    raise KeyError(f"suite job not found: {job_id}")
                current = str(row["state"])
                if current in {"cancelled", "completed", "failed"}:
                    raise RuntimeError("suite job is already terminal")
                if str(row["executor_id"] or "") != self._executor_id:
                    raise RuntimeError("suite executor lease ownership was lost")
                target = (
                    SuiteJobStatus.CANCELLED
                    if bool(row["cancel_requested"])
                    else SuiteJobStatus.FAILED
                    if error is not None
                    else SuiteJobStatus.COMPLETED
                )
                await connection.execute(
                    "UPDATE worker_worlds.suite_jobs SET state=$2,ended_at=$3,updated_at=$3,"
                    "revision=revision+1,error_type=$4,error_message=$5,suite_record_path=$6,"
                    "executor_id=NULL,executor_expires_at=NULL "
                    "WHERE id=$1",
                    job_id,
                    target.value,
                    now,
                    type(error).__name__ if error else None,
                    str(error) if error else None,
                    suite_record_path,
                )
                if target in {SuiteJobStatus.CANCELLED, SuiteJobStatus.FAILED}:
                    await connection.execute(
                        "UPDATE worker_worlds.suite_job_scenarios "
                        "SET state=$3,error_type=COALESCE(error_type,$4),"
                        "error_message=COALESCE(error_message,$5),updated_at=$2 "
                        "WHERE suite_job_id=$1 AND state IN ('pending','running')",
                        job_id,
                        now,
                        "cancelled" if target is SuiteJobStatus.CANCELLED else "error",
                        type(error).__name__ if error else None,
                        str(error) if error else None,
                    )
                await connection.execute(
                    "UPDATE worker_worlds.suite_jobs SET "
                    "completed_scenarios=(SELECT count(*) FROM "
                    "worker_worlds.suite_job_scenarios s WHERE s.suite_job_id=$1 "
                    "AND s.state IN ('passed','failed','error')),"
                    "passed_scenarios=(SELECT count(*) FROM "
                    "worker_worlds.suite_job_scenarios s WHERE s.suite_job_id=$1 "
                    "AND s.state='passed'),"
                    "failed_scenarios=(SELECT count(*) FROM "
                    "worker_worlds.suite_job_scenarios s WHERE s.suite_job_id=$1 "
                    "AND s.state IN ('failed','error')) WHERE id=$1",
                    job_id,
                )
                return await self._get_with_connection(connection, job_id)
        finally:
            await connection.close()

    async def attach_evidence(self, job_id: str, suite_record_path: str) -> SuiteJobRecord:
        """Attach a relative evidence path to an already terminal job."""
        connection = await connect(self._settings)
        try:
            await connection.execute(
                "UPDATE worker_worlds.suite_jobs SET suite_record_path=$2 "
                "WHERE id=$1 AND state IN ('cancelled','completed','failed') "
                "AND suite_record_path IS NULL",
                job_id,
                suite_record_path,
            )
            return await self._get_with_connection(connection, job_id)
        finally:
            await connection.close()

    async def _get_with_connection(
        self, connection: asyncpg.Connection[asyncpg.Record], job_id: str
    ) -> SuiteJobRecord:
        row = await connection.fetchrow(
            "SELECT * FROM worker_worlds.suite_jobs WHERE id=$1", job_id
        )
        if row is None:
            raise KeyError(f"suite job not found: {job_id}")
        scenario_rows = await connection.fetch(
            "SELECT * FROM worker_worlds.suite_job_scenarios "
            "WHERE suite_job_id=$1 ORDER BY ordinal",
            job_id,
        )
        configuration = row["configuration"]
        if isinstance(configuration, str):
            configuration = json.loads(configuration)
        return SuiteJobRecord(
            id=str(row["id"]),
            request_key=str(row["request_key"]),
            status=SuiteJobStatus(str(row["state"])),
            catalog_version=str(row["catalog_version"]),
            domain_id=str(row["domain_id"]),
            role_id=str(row["role_id"]),
            suite_id=str(row["suite_id"]),
            suite_revision=str(row["suite_revision"]),
            agent_id=str(row["agent_id"]),
            world=str(row["world"]),
            configuration=cast(dict[str, JsonValue], configuration),
            total_scenarios=int(row["total_scenarios"]),
            completed_scenarios=int(row["completed_scenarios"]),
            passed_scenarios=int(row["passed_scenarios"]),
            failed_scenarios=int(row["failed_scenarios"]),
            cancel_requested=bool(row["cancel_requested"]),
            revision=int(row["revision"]),
            scenarios=tuple(
                SuiteScenarioRecord(
                    scenario_id=ScenarioId(str(item["scenario_id"])),
                    ordinal=int(item["ordinal"]),
                    status=SuiteScenarioStatus(str(item["state"])),
                    attempts=int(item["attempts"]),
                    run_id=RunId(str(item["run_id"])) if item["run_id"] else None,
                    run_record_hash=str(item["run_record_hash"])
                    if item["run_record_hash"]
                    else None,
                    terminal_reason=str(item["terminal_reason"])
                    if item["terminal_reason"]
                    else None,
                    error_type=str(item["error_type"]) if item["error_type"] else None,
                    error_message=str(item["error_message"]) if item["error_message"] else None,
                    updated_at=item["updated_at"],
                )
                for item in scenario_rows
            ),
            error_type=str(row["error_type"]) if row["error_type"] else None,
            error_message=str(row["error_message"]) if row["error_message"] else None,
            suite_record_path=str(row["suite_record_path"]) if row["suite_record_path"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            executor_id=str(row["executor_id"]) if row["executor_id"] else None,
            executor_expires_at=row["executor_expires_at"],
        )
