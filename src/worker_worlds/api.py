"""Versioned local HTTP API over the framework-neutral Worker Worlds runner."""

from __future__ import annotations

import os
import re
import statistics
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from worker_worlds import __version__
from worker_worlds.adapters import LangGraphAdapter, OpenAIAgentsAdapter, refund_fake_runtime
from worker_worlds.api_models import (
    AgentListResponse,
    AgentSummary,
    ComparisonListResponse,
    ComparisonSummary,
    CreateRunRequest,
    HealthResponse,
    OverviewResponse,
    RunListResponse,
    RunSummary,
    ScenarioListResponse,
    ScenarioSummary,
)
from worker_worlds.config import WorkerWorldsConfig, load_config
from worker_worlds.contracts import ComparisonReport, RunRecord, Scenario, VerdictStatus
from worker_worlds.database import DatabaseSettings, database_health
from worker_worlds.grading import DeterministicGrader
from worker_worlds.postgres_world import PostgresWorld
from worker_worlds.protocols import WorkerAdapter, World
from worker_worlds.reporting import JsonReporter
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


def _artifact_directory() -> Path:
    path = Path(os.environ.get("WORKER_WORLDS_ARTIFACT_DIR", ".worker-worlds/api")).resolve()
    if path == Path(path.anchor):
        raise RuntimeError("artifact directory cannot be a filesystem root")
    return path


def _display_artifact_directory() -> str:
    """Describe storage without disclosing an arbitrary absolute host path."""
    try:
        return str(_artifact_directory().relative_to(Path.cwd().resolve()))
    except ValueError:
        return "[external-artifact-directory]"


def _scenario_roots() -> tuple[Path, ...]:
    configured = os.environ.get("WORKER_WORLDS_SCENARIO_DIR")
    if configured:
        return (Path(configured).resolve(),)
    package_share = Path(sys.prefix) / "share/worker-worlds"
    roots = (Path("examples/scenarios").resolve(), Path("scenarios/release").resolve())
    if (package_share / "scenarios").exists():
        roots = (package_share / "examples/scenarios", package_share / "scenarios")
    return roots


def _scenario_files() -> Iterator[Path]:
    for root in _scenario_roots():
        if root.is_dir():
            yield from sorted(root.glob("*.yaml"))


def _scenarios() -> dict[str, tuple[Scenario, Path]]:
    result: dict[str, tuple[Scenario, Path]] = {}
    for path in _scenario_files():
        scenario = load_scenario(path)
        result.setdefault(str(scenario.id), (scenario, path))
    return result


def _load_runs() -> list[RunRecord]:
    directory = _artifact_directory() / "runs"
    records: list[RunRecord] = []
    if not directory.exists():
        return records
    for path in directory.glob("*.json"):
        try:
            records.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError):
            continue
    return sorted(records, key=lambda item: (item.started_at, str(item.id)), reverse=True)


def _run_summary(record: RunRecord, scenarios: dict[str, tuple[Scenario, Path]]) -> RunSummary:
    scenario = scenarios.get(str(record.scenario_id))
    metadata = scenario[0].metadata if scenario else {}
    objective = scenario[0].trigger.content if scenario else str(record.scenario_id)
    status = "pass" if record.passed else "error" if record.incomplete_evidence else "fail"
    return RunSummary(
        id=str(record.id),
        scenario_id=str(record.scenario_id),
        scenario_name=objective,
        family=str(metadata.get("family", "unclassified")),
        worker=record.worker,
        status=status,
        terminal_reason=record.terminal_reason.value,
        duration_ms=record.total_duration_ms,
        tool_calls=record.tool_call_count,
        mutations=record.mutation_count,
        started_at=record.started_at,
        cleanup_succeeded=record.cleanup_succeeded,
    )


def _worker(name: str) -> WorkerAdapter:
    if name == "langgraph-fake":
        return LangGraphAdapter(refund_fake_runtime())
    if name == "openai-agents-fake":
        return OpenAIAgentsAdapter(refund_fake_runtime())
    return StubWorkerAdapter()


def _config() -> WorkerWorldsConfig:
    config, _ = load_config()
    return config


def _agent_summary(config: WorkerWorldsConfig, agent_id: str) -> AgentSummary:
    registry = config.agent_registry()
    definition = registry.get(agent_id)
    readiness = registry.readiness(agent_id, os.environ)
    metadata = definition.model
    return AgentSummary(
        id=agent_id,
        adapter=definition.adapter.value,
        version=definition.version,
        model_provider=metadata.provider if metadata else None,
        model_name=metadata.name if metadata else None,
        ready=readiness.ready,
        missing_requirements=readiness.missing_requirements,
        deterministic_test_infrastructure=definition.adapter.value == "stub",
    )


def _world(name: str, scenario: Scenario) -> World:
    if name == "postgres":
        return PostgresWorld(DatabaseSettings.from_env(), str(scenario.id))
    return StubWorld()


def _comparison_summaries() -> tuple[ComparisonSummary, ...]:
    directory = _artifact_directory() / "comparisons"
    summaries: list[ComparisonSummary] = []
    if not directory.exists():
        return ()
    for path in directory.rglob("comparison.json"):
        try:
            report = ComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            continue
        summaries.append(
            ComparisonSummary(
                id=str(report.id),
                gate="pass" if report.verdict.passed else "fail",
                baseline_worker=report.baseline_name,
                candidate_worker=Path(report.candidate_source).stem,
                new_critical=report.verdict.new_critical,
                new_high=report.verdict.new_high,
                pass_rate_delta=sum(item.pass_rate_delta for item in report.scenarios)
                / len(report.scenarios)
                if report.scenarios
                else 0,
                path=str(path.relative_to(_artifact_directory())),
            )
        )
    return tuple(sorted(summaries, key=lambda item: item.id, reverse=True))


def create_app() -> FastAPI:
    """Build the v1 API without starting a server or mutating runtime state."""
    app = FastAPI(
        title="Worker Worlds API",
        version=__version__,
        description="Local versioned control plane for deterministic AI worker evaluations.",
    )
    origins = tuple(
        item.strip()
        for item in os.environ.get(
            "WORKER_WORLDS_DASHBOARD_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if item.strip()
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        settings = DatabaseSettings.from_env()
        ready, message = await database_health(settings)
        return HealthResponse(
            status="ready" if ready else "degraded",
            package_version=__version__,
            database_ready=ready,
            database=message,
            artifact_directory=_display_artifact_directory(),
        )

    @router.get("/overview", response_model=OverviewResponse)
    async def overview() -> OverviewResponse:
        records = _load_runs()
        passed = sum(record.passed for record in records)
        failed = len(records) - passed
        critical = sum(
            verdict.status is VerdictStatus.FAIL and verdict.severity.value == "critical"
            for record in records
            for verdict in record.verdicts
        )
        windows = [records[index : index + 10] for index in range(0, min(len(records), 120), 10)]
        rates = tuple(
            sum(record.passed for record in window) / len(window) for window in reversed(windows)
        )
        return OverviewResponse(
            total_runs=len(records),
            passed_runs=passed,
            failed_runs=failed,
            critical_regressions=critical,
            pass_rate=passed / len(records) if records else 0,
            median_duration_ms=int(
                statistics.median(record.total_duration_ms for record in records)
            )
            if records
            else 0,
            scenario_count=len(_scenarios()),
            recent_pass_rates=rates,
        )

    @router.get("/scenarios", response_model=ScenarioListResponse)
    async def scenarios() -> ScenarioListResponse:
        items = tuple(
            ScenarioSummary(
                id=scenario_id,
                objective=scenario.trigger.content,
                family=str(scenario.metadata.get("family", "examples")),
                severity=max(
                    (assertion.severity.value for assertion in scenario.assertions), default="low"
                ),
                tools=tuple(
                    sorted(
                        {
                            str(scenario.metadata["specialized_tool"])
                            for _ in (0,)
                            if "specialized_tool" in scenario.metadata
                        }
                    )
                ),
                tags=tuple(scenario.tags),
                review_status=str(scenario.metadata.get("review_status", "not_applicable")),
                source=str(path.name),
            )
            for scenario_id, (scenario, path) in sorted(_scenarios().items())
        )
        return ScenarioListResponse(scenarios=items, total=len(items))

    @router.get("/agents", response_model=AgentListResponse)
    async def agents() -> AgentListResponse:
        config = _config()
        items = tuple(_agent_summary(config, agent_id) for agent_id in sorted(config.agents))
        return AgentListResponse(agents=items, total=len(items))

    @router.get("/agents/{agent_id}", response_model=AgentSummary)
    async def agent_detail(agent_id: str) -> AgentSummary:
        if not _SAFE_ID.fullmatch(agent_id):
            raise HTTPException(status_code=404, detail="agent not found")
        config = _config()
        if agent_id not in config.agents:
            raise HTTPException(status_code=404, detail="agent not found")
        return _agent_summary(config, agent_id)

    @router.get("/runs", response_model=RunListResponse)
    async def runs(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> RunListResponse:
        scenarios_by_id = _scenarios()
        records = _load_runs()
        return RunListResponse(
            runs=tuple(_run_summary(item, scenarios_by_id) for item in records[:limit]),
            total=len(records),
        )

    @router.get("/runs/{run_id}", response_model=RunRecord)
    async def run_detail(run_id: str) -> RunRecord:
        if not _SAFE_ID.fullmatch(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        path = _artifact_directory() / "runs" / f"{run_id}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="run not found")
        try:
            return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise HTTPException(status_code=500, detail="stored run is invalid") from exc

    @router.post("/runs", response_model=RunRecord, status_code=201)
    async def create_run(request: CreateRunRequest) -> RunRecord:
        available = _scenarios()
        selected = available.get(request.scenario_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        scenario = selected[0]
        worker = _worker(request.worker)
        if request.agent_id is not None:
            config = _config()
            if request.agent_id not in config.agents:
                raise HTTPException(
                    status_code=404,
                    detail={"type": "UnknownAgent", "message": "registered agent not found"},
                )
            summary = _agent_summary(config, request.agent_id)
            if not summary.ready:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "AgentNotReady",
                        "message": "registered agent is not ready",
                        "missing_requirements": list(summary.missing_requirements),
                    },
                )
            try:
                worker = await config.agent_registry().create(request.agent_id, os.environ)
            except (TypeError, ValueError, RuntimeError):
                raise HTTPException(
                    status_code=409,
                    detail={"type": "AgentUnavailable", "message": "agent factory is unavailable"},
                ) from None
        record = await Runner(DeterministicGrader()).run(
            scenario, _world(request.world, scenario), worker
        )
        await JsonReporter(_artifact_directory() / "runs").report(record)
        return record

    @router.get("/comparisons", response_model=ComparisonListResponse)
    async def comparisons() -> ComparisonListResponse:
        items = _comparison_summaries()
        return ComparisonListResponse(comparisons=items, total=len(items))

    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    """Serve the local API with explicit host/port configuration."""
    host = os.environ.get("WORKER_WORLDS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("WORKER_WORLDS_API_PORT", "8000"))
    uvicorn.run("worker_worlds.api:app", host=host, port=port, reload=False)
