"""Strict public HTTP API models for the v1 local control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base API representation with a stable version and strict fields."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"


class HealthResponse(ApiModel):
    """Runtime and database readiness without credential disclosure."""

    status: Literal["ready", "degraded"]
    package_version: str
    database_ready: bool
    database: str
    artifact_directory: str


class OverviewResponse(ApiModel):
    """Workspace metrics derived only from persisted run evidence."""

    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    critical_regressions: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    median_duration_ms: int = Field(ge=0)
    scenario_count: int = Field(ge=0)
    recent_pass_rates: tuple[float, ...]


class ScenarioSummary(ApiModel):
    """Readable scenario metadata for browsing and run selection."""

    id: str
    objective: str
    family: str
    severity: str
    tools: tuple[str, ...]
    tags: tuple[str, ...]
    review_status: str
    source: str


class RunSummary(ApiModel):
    """Compact representation of a canonical RunRecord."""

    id: str
    scenario_id: str
    scenario_name: str
    family: str
    worker: str
    status: Literal["pass", "fail", "error"]
    terminal_reason: str
    duration_ms: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    mutations: int = Field(ge=0)
    started_at: datetime
    cleanup_succeeded: bool


class RunListResponse(ApiModel):
    """Stable run collection ordered newest first."""

    runs: tuple[RunSummary, ...]
    total: int = Field(ge=0)


class ScenarioListResponse(ApiModel):
    """Stable scenario collection."""

    scenarios: tuple[ScenarioSummary, ...]
    total: int = Field(ge=0)


class AgentSummary(ApiModel):
    """Credential-free registered-agent identity and readiness."""

    id: str
    adapter: str
    version: str
    model_provider: str | None = None
    model_name: str | None = None
    ready: bool
    missing_requirements: tuple[str, ...] = ()
    deterministic_test_infrastructure: bool = False


class AgentListResponse(ApiModel):
    """Stable registered-agent collection."""

    agents: tuple[AgentSummary, ...]
    total: int = Field(ge=0)


class CreateRunRequest(ApiModel):
    """Bounded request to run one locally available scenario."""

    scenario_id: str = Field(min_length=1, max_length=200)
    worker: Literal["stub", "langgraph-fake", "openai-agents-fake"] = "stub"
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)
    world: Literal["stub", "postgres"] = "postgres"


class ComparisonSummary(ApiModel):
    """Compact persisted behavioral comparison."""

    id: str
    gate: Literal["pass", "fail"]
    baseline_worker: str
    candidate_worker: str
    new_critical: int = Field(ge=0)
    new_high: int = Field(ge=0)
    pass_rate_delta: float
    path: str


class ComparisonListResponse(ApiModel):
    """Stable comparison collection."""

    comparisons: tuple[ComparisonSummary, ...]
    total: int = Field(ge=0)
