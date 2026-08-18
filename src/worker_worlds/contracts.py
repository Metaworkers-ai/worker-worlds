"""Versioned, framework-neutral public contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, NewType, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RunId = NewType("RunId", str)
ScenarioId = NewType("ScenarioId", str)
EventId = NewType("EventId", str)
CallId = NewType("CallId", str)
TurnId = NewType("TurnId", str)
VerdictId = NewType("VerdictId", str)
type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]
UtcDateTime = Annotated[datetime, Field(description="Timezone-aware UTC timestamp")]


class Contract(BaseModel):
    """Base for persisted records with strict validation and canonical JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)
    schema_version: str = "1.0"
    supported_schema_major: ClassVar[int] = 1

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        """Reject malformed or unsupported schema major versions."""
        try:
            major_text, minor_text = value.split(".", maxsplit=1)
            major, minor = int(major_text), int(minor_text)
        except (ValueError, AttributeError) as exc:
            raise ValueError("schema_version must use '<major>.<minor>'") from exc
        if major != cls.supported_schema_major:
            raise ValueError(
                f"unsupported schema major {major}; supported major is {cls.supported_schema_major}"
            )
        if minor < 0:
            raise ValueError("schema minor must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_utc_timestamps(self) -> Self:
        """Require every datetime anywhere in a public record to be UTC-aware."""

        def check(value: Any, path: str) -> None:
            if isinstance(value, datetime):
                offset = value.utcoffset()
                if value.tzinfo is None or offset is None:
                    raise ValueError(f"{path} must be timezone-aware")
                if offset.total_seconds() != 0:
                    raise ValueError(f"{path} must be UTC")
            elif isinstance(value, BaseModel):
                for name in type(value).model_fields:
                    check(getattr(value, name), f"{path}.{name}")
            elif isinstance(value, dict):
                for key, item in value.items():
                    check(item, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    check(item, f"{path}[{index}]")

        for name in type(self).model_fields:
            check(getattr(self, name), name)
        return self

    def canonical_json(self) -> str:
        """Serialize deterministically as compact UTF-8 JSON."""
        data = self.model_dump(mode="json")
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def canonical_hash(self) -> str:
        """Return a SHA-256 hash of the canonical representation."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class Money(Contract):
    """Money represented without floating point."""

    amount_minor: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class WorldRef(Contract):
    """World implementation selected by a scenario."""

    name: str
    version: str
    seed: int


class Limits(Contract):
    """Execution budgets for a scenario."""

    wall_time_s: Annotated[float, Field(gt=0)] = 30
    tool_calls: Annotated[int, Field(ge=0)] = 20
    model_tokens: Annotated[int, Field(ge=0)] = 12_000
    worker_turns: Annotated[int, Field(gt=0)] = 50
    mutations: Annotated[int, Field(ge=0)] = 20
    cost_minor: Annotated[int, Field(ge=0)] = 0
    tool_timeout_s: Annotated[float, Field(gt=0)] = 10


class AssertionSeverity(StrEnum):
    """Bounded assertion severity."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


class AssertionType(StrEnum):
    """Supported deterministic assertion primitives."""

    STATE_EQUALS = "state_equals"
    STATE_MATCHES = "state_matches"
    VALUE_WITHIN = "value_within"
    ACTION_EXISTS = "action_exists"
    NO_ACTION = "no_action"
    ACTION_COUNT = "action_count"
    SEQUENCE_BEFORE = "sequence_before"
    CHANGED_ENTITIES_SUBSET = "changed_entities_subset"
    RESOURCE_WITHIN = "resource_within"
    POLICY = "policy"


class VerdictStatus(StrEnum):
    """Outcome of deterministic grading."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class TerminalReason(StrEnum):
    """Why worker execution ended."""

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    TOOL_ERROR = "tool_error"
    WORKER_ERROR = "worker_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    ADAPTER_ERROR = "adapter_error"
    PROVIDER_ERROR = "provider_error"
    SCENARIO_ERROR = "scenario_error"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_VALIDATION_ERROR = "tool_validation_error"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    AUTHORIZATION_REJECTION = "authorization_rejection"


class ToolResultStatus(StrEnum):
    """Normalized tool result status."""

    SUCCESS = "success"
    ERROR = "error"


class Trigger(Contract):
    """Initial input delivered to a worker."""

    type: str
    actor: dict[str, JsonValue] = Field(default_factory=dict)
    content: str


class AssertionSpec(Contract):
    """Declarative deterministic assertion."""

    id: str
    type: AssertionType | str
    severity: AssertionSeverity
    path: str | None = None
    value: JsonValue = None
    event: str | None = None
    required: bool = True
    description: str = ""
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    source_file: str | None = None
    source_index: Annotated[int, Field(ge=0)] | None = None
    tags: tuple[str, ...] = ()
    evidence_config: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_combination(self) -> Self:
        """Reject unsupported types and missing required parameters."""
        try:
            assertion_type = AssertionType(self.type)
        except ValueError as exc:
            raise ValueError(f"unsupported assertion type: {self.type}") from exc
        parameters = self.parameters
        path = self.path or parameters.get("path")
        event = self.event or parameters.get("event_type")
        if assertion_type in {
            AssertionType.STATE_EQUALS,
            AssertionType.STATE_MATCHES,
            AssertionType.VALUE_WITHIN,
        } and not isinstance(path, str):
            raise ValueError(f"{assertion_type.value} requires a string path")
        if assertion_type in {
            AssertionType.ACTION_EXISTS,
            AssertionType.NO_ACTION,
            AssertionType.ACTION_COUNT,
        } and not isinstance(event, str):
            raise ValueError(f"{assertion_type.value} requires an event or event_type")
        if assertion_type is AssertionType.SEQUENCE_BEFORE and not all(
            isinstance(parameters.get(name), dict) for name in ("first", "second")
        ):
            raise ValueError("sequence_before requires first and second event filters")
        if assertion_type is AssertionType.POLICY and not isinstance(parameters.get("rule"), str):
            raise ValueError("policy requires a rule")
        return self


class Scenario(Contract):
    """A versioned scenario definition."""

    id: ScenarioId
    world: WorldRef
    trigger: Trigger
    limits: Limits = Field(default_factory=Limits)
    assertions: tuple[AssertionSpec, ...]
    tags: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AuthorizationContext(Contract):
    """Trusted authority supplied by the harness, never world prose."""

    actor_id: str
    customer_id: str | None = None
    scopes: frozenset[str] = frozenset()


class ToolSpec(Contract):
    """Framework-neutral tool declaration."""

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)
    mutation: bool = False


class ToolCall(Contract):
    """Normalized worker request to invoke a world tool."""

    id: CallId
    run_id: RunId
    tool_name: str
    arguments: dict[str, JsonValue]
    authorization: AuthorizationContext
    requested_at: UtcDateTime
    idempotency_key: str | None = None


class ToolResult(Contract):
    """Normalized result, including typed failure provenance."""

    call_id: CallId
    status: ToolResultStatus
    output: JsonValue = None
    error_type: str | None = None
    error_message: str | None = None
    started_at: UtcDateTime
    ended_at: UtcDateTime
    duration_ms: Annotated[int, Field(ge=0)] = 0
    retry_of: CallId | None = None
    audit_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def error_is_complete(self) -> Self:
        """Require structured error fields exactly for failed tool calls."""
        if self.status is ToolResultStatus.ERROR and not (self.error_type and self.error_message):
            raise ValueError("error tool results require error_type and error_message")
        if self.status is ToolResultStatus.SUCCESS and (self.error_type or self.error_message):
            raise ValueError("successful tool results cannot contain error fields")
        return self


class EntityRef(Contract):
    """Stable reference to a world entity."""

    type: str
    id: str


class WorldEvent(Contract):
    """Append-only world mutation evidence."""

    id: EventId
    run_id: RunId
    sequence: Annotated[int, Field(gt=0)]
    occurred_at: UtcDateTime
    event_type: str
    entity: EntityRef
    actor_id: str
    request_id: CallId | None = None
    before: dict[str, JsonValue] | None = None
    after: dict[str, JsonValue] | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    scenario_id: ScenarioId | None = None
    authorization: AuthorizationContext | None = None
    policy_context: dict[str, JsonValue] = Field(default_factory=dict)


class WorldSnapshot(Contract):
    """Canonical world state at a controlled instant."""

    world_name: str
    world_version: str
    run_id: RunId
    captured_at: UtcDateTime
    sequence: Annotated[int, Field(ge=0)]
    state: dict[str, JsonValue]


class WorkerTurn(Contract):
    """One normalized worker decision and its observed result."""

    id: TurnId
    index: Annotated[int, Field(ge=0)]
    occurred_at: UtcDateTime
    message: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    terminal: bool = False
    model_tokens: Annotated[int, Field(ge=0)] | None = None
    cost_minor: Annotated[int, Field(ge=0)] | None = None


class EvidenceKind(StrEnum):
    """Kinds of bounded evidence references."""

    INITIAL_SNAPSHOT = "initial_snapshot"
    FINAL_SNAPSHOT = "final_snapshot"
    EVENT = "event"
    TOOL_CALL = "tool_call"
    WORKER_TURN = "worker_turn"
    AUTHORIZATION = "authorization"
    CHANGED_ENTITY = "changed_entity"
    RESOURCE = "resource"
    POLICY = "policy"


class EvidenceReference(Contract):
    """Resolvable bounded reference into RunRecord evidence."""

    kind: EvidenceKind
    reference: str
    path: str | None = None
    value: JsonValue = None
    sequence: int | None = None
    facts: dict[str, JsonValue] = Field(default_factory=dict)


class Verdict(Contract):
    """Evidence-backed deterministic assertion verdict."""

    id: VerdictId
    assertion_id: str
    status: VerdictStatus
    severity: AssertionSeverity
    message: str
    evidence: tuple[str, ...] = ()
    reason_code: str = "unspecified"
    expected: JsonValue = None
    observed: JsonValue = None
    evidence_refs: tuple[EvidenceReference, ...] = ()
    evaluator_version: str = "1.0"
    assertion_type: str | None = None
    policy_name: str | None = None
    required: bool = True


class DiffClassification(StrEnum):
    """Bounded behavioral comparison classification."""

    NEW_FAILURE = "new_failure"
    FIXED = "fixed"
    UNCHANGED = "unchanged"
    FLAKINESS_INCREASED = "flakiness_increased"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DiffResult(Contract):
    """Persisted result contract for future behavioral comparisons."""

    baseline_run_ids: tuple[RunId, ...]
    candidate_run_ids: tuple[RunId, ...]
    classification: DiffClassification
    summary: str


class RunRecord(Contract):
    """Complete normalized evidence for one worker run."""

    id: RunId
    scenario_id: ScenarioId
    worker: str
    worker_version: str
    adapter: str
    repetition: Annotated[int, Field(ge=0)]
    seed: int
    started_at: UtcDateTime
    ended_at: UtcDateTime
    terminal_reason: TerminalReason
    initial_snapshot: WorldSnapshot | None
    final_snapshot: WorldSnapshot | None
    turns: tuple[WorkerTurn, ...]
    events: tuple[WorldEvent, ...]
    verdicts: tuple[Verdict, ...]
    incomplete_evidence: bool
    error_type: str | None = None
    error_message: str | None = None
    initial_snapshot_hash: str | None = None
    final_snapshot_hash: str | None = None
    tool_duration_ms: Annotated[int, Field(ge=0)] = 0
    total_duration_ms: Annotated[int, Field(ge=0)] = 0
    tool_call_count: Annotated[int, Field(ge=0)] = 0
    mutation_count: Annotated[int, Field(ge=0)] = 0
    model_tokens: Annotated[int, Field(ge=0)] | None = None
    cost_minor: Annotated[int, Field(ge=0)] | None = None
    environment: dict[str, JsonValue] = Field(default_factory=dict)
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    world_version: str | None = None
    migration_version: str | None = None
    cleanup_succeeded: bool = True
    scenario_trigger: dict[str, JsonValue] = Field(default_factory=dict)
    scenario_tags: tuple[str, ...] = ()
    scenario_hash: str | None = None

    @property
    def passed(self) -> bool:
        """Return true only when evidence is complete and every verdict passes."""
        return (
            not self.incomplete_evidence
            and bool(self.verdicts)
            and all(
                verdict.status is VerdictStatus.PASS
                or (
                    verdict.status is VerdictStatus.FAIL
                    and verdict.severity in {AssertionSeverity.MEDIUM, AssertionSeverity.INFO}
                )
                or (verdict.status is VerdictStatus.ERROR and not verdict.required)
                for verdict in self.verdicts
            )
        )


class ScenarioAggregate(Contract):
    """Deterministic summary that retains references to every repetition."""

    scenario_id: ScenarioId
    worker: str
    worker_version: str
    requested_repetitions: Annotated[int, Field(gt=0)]
    completed_repetitions: Annotated[int, Field(ge=0)]
    run_ids: tuple[RunId, ...]
    passed: int
    failed: int
    errors: int
    pass_rate: Annotated[float, Field(ge=0, le=1)]
    verdict_counts: dict[str, int]
    failure_reasons: dict[str, int]
    terminal_reasons: dict[str, int]
    duration_ms: tuple[int, ...]
    model_tokens: int | None = None
    cost_minor: int | None = None
    infrastructure_errors: int = 0
    insufficient_sample: bool = True


class SuiteRecord(Contract):
    """One suite artifact containing aggregates and immutable source runs."""

    id: str
    name: str
    worker: str
    worker_version: str
    world: str
    started_at: UtcDateTime
    ended_at: UtcDateTime
    scenarios: tuple[ScenarioId, ...]
    aggregates: tuple[ScenarioAggregate, ...]
    runs: tuple[RunRecord, ...]
    artifact_references: dict[str, str] = Field(default_factory=dict)
    configuration_hash: str
