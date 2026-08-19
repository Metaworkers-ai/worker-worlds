"""Strict Worker Worlds configuration discovery and redacted display."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from worker_worlds.contracts import ComparisonConfig


class ConfigModel(BaseModel):
    """Strict configuration base."""

    model_config = ConfigDict(extra="forbid")


class DatabaseConfig(ConfigModel):
    """Database configuration without a persisted default credential."""

    url: str | None = None


class ReportingConfig(ConfigModel):
    """Portable artifact settings."""

    mode: Literal["auto", "full", "summary"] = "auto"
    output: str = ".worker-worlds/runs"
    redact_keys: tuple[str, ...] = ("secret", "password", "api_key", "access_token")


class ExecutionConfig(ConfigModel):
    """Bounded suite execution settings."""

    world: Literal["stub", "postgres"] = "stub"
    worker: str = "stub"
    scenario_locations: tuple[str, ...] = ("examples/scenarios",)
    repetitions: int = Field(default=1, gt=0, le=100)
    concurrency: int = Field(default=4, gt=0, le=100)
    wall_time_s: float = Field(default=30, gt=0, le=3600)
    tool_calls: int = Field(default=20, ge=0, le=10_000)
    model_tokens: int = Field(default=12_000, ge=0)
    cost_minor: int = Field(default=0, ge=0)
    mutations: int = Field(default=20, ge=0, le=10_000)


class WorkerWorldsConfig(ConfigModel):
    """Versioned top-level project configuration."""

    schema_version: Literal["1.0"] = "1.0"
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    baselines_directory: str = ".worker-worlds/baselines"
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)
    provider: dict[str, str] = Field(default_factory=dict)

    def redacted(self) -> dict[str, object]:
        """Return an effective configuration safe for logs and artifacts."""
        data = self.model_dump(mode="json")
        database = data["database"]
        assert isinstance(database, dict)
        if database.get("url"):
            database["url"] = "[REDACTED]"
        if data["provider"]:
            data["provider"] = {key: "[REDACTED]" for key in data["provider"]}
        return data

    def configuration_hash(self) -> str:
        """Hash the redacted effective configuration deterministically."""
        return hashlib.sha256(
            json.dumps(self.redacted(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def load_config(path: Path | None = None) -> tuple[WorkerWorldsConfig, Path | None]:
    """Apply defaults, discovered project file, and explicit environment variables."""
    selected = path
    if selected is None:
        candidate = Path("worker-worlds.yaml")
        selected = candidate if candidate.exists() else None
    raw: object = {}
    if selected is not None:
        raw = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Worker Worlds configuration root must be a mapping")
    data = dict(raw)
    database_url = os.environ.get("WORKER_WORLDS_DATABASE_URL")
    if database_url:
        database = dict(data.get("database", {}))
        database["url"] = database_url
        data["database"] = database
    return WorkerWorldsConfig.model_validate(data), selected
