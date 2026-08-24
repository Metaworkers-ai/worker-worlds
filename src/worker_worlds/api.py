"""Versioned local HTTP API over the framework-neutral Worker Worlds runner."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import statistics
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError

from worker_worlds import __version__
from worker_worlds.adapters import LangGraphAdapter, OpenAIAgentsAdapter, refund_fake_runtime
from worker_worlds.api_models import (
    AgentListResponse,
    AgentSummary,
    ComparisonListResponse,
    ComparisonSummary,
    CreateContextualComparisonRequest,
    CreateRunRequest,
    CreateSuiteJobRequest,
    HealthResponse,
    OverviewResponse,
    RunListResponse,
    RunSummary,
    ScenarioListResponse,
    ScenarioSummary,
    SuiteJobListResponse,
)
from worker_worlds.catalog import (
    CapabilityDefinition,
    Catalog,
    DomainDefinition,
    EvaluationSuiteDefinition,
    RoleDefinition,
    builtin_catalog,
    load_catalog,
)
from worker_worlds.comparison_context import (
    ContextualComparisonRecord,
    compare_contextual_suites,
)
from worker_worlds.config import WorkerWorldsConfig, load_config
from worker_worlds.contracts import (
    ComparisonReport,
    RunRecord,
    Scenario,
    SuiteRecord,
    VerdictStatus,
)
from worker_worlds.database import DatabaseSettings, database_health
from worker_worlds.evaluation import (
    EvaluationContext,
    build_context,
    build_suite_context,
    run_manifest,
)
from worker_worlds.grading import DeterministicGrader
from worker_worlds.protocols import WorkerAdapter, World
from worker_worlds.reporting import JsonReporter
from worker_worlds.runner import Runner
from worker_worlds.scenario_identity import scenario_content_hash
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter
from worker_worlds.suite_jobs import PostgresSuiteJobRepository, SuiteJobCreate, SuiteJobRecord
from worker_worlds.suite_service import DurableSuiteService
from worker_worlds.world_registry import create_world, world_version

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the durable-suite recovery loop for the API process lifetime."""
    recovery_task = asyncio.create_task(app.state.recover_suite_jobs())
    try:
        yield
    finally:
        recovery_task.cancel()
        await asyncio.gather(recovery_task, return_exceptions=True)


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
    workspace_roots = (
        Path("examples/scenarios").resolve(),
        Path("scenarios/release").resolve(),
        Path("scenarios/enterprise").resolve(),
    )
    if all(root.is_dir() for root in workspace_roots):
        return workspace_roots
    if (package_share / "scenarios").exists():
        return (
            package_share / "examples/scenarios",
            package_share / "scenarios",
            package_share / "enterprise-scenarios",
        )
    return workspace_roots


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


def _catalog() -> Catalog:
    """Load the explicitly configured catalog or the checked built-in catalog."""
    config = _config()
    return (
        load_catalog(Path(config.catalog_path).resolve())
        if config.catalog_path
        else builtin_catalog()
    )


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
        supported_domain_ids=definition.supported_domain_ids,
    )


def _write_context_manifest(record: RunRecord, context: EvaluationContext) -> None:
    """Atomically persist an additive context sidecar without changing RunRecord bytes."""
    manifest = run_manifest(record.id, record.canonical_hash(), context)
    directory = _artifact_directory() / "contexts"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix=".context-", delete=False
    ) as handle:
        handle.write(manifest.canonical_json() + "\n")
        temporary = Path(handle.name)
    temporary.replace(directory / f"{record.id}.json")


def _world(name: str, scenario: Scenario) -> World:
    return create_world(name, scenario)


def _validate_world_selection(domain_id: str, role_id: str, world: str) -> None:
    """Reject a runtime world that cannot implement the selected business context."""
    allowed = (
        {"insurance"}
        if domain_id == "insurance"
        else {"supply-chain"}
        if role_id == "supply-chain-analyst"
        else {"postgres", "stub"}
    )
    if world not in allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "type": "IncompatibleWorldSelection",
                "message": f"world {world} cannot execute {domain_id}/{role_id}",
            },
        )


def _comparison_summaries() -> tuple[ComparisonSummary, ...]:
    directory = _artifact_directory() / "comparisons"
    summaries: list[ComparisonSummary] = []
    if not directory.exists():
        return ()
    for path in directory.rglob("comparison.json"):
        contextual: ContextualComparisonRecord | None = None
        try:
            report = ComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            try:
                contextual = ContextualComparisonRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                report = contextual.report
            except (OSError, ValidationError):
                continue
        summaries.append(
            ComparisonSummary(
                id=str(report.id),
                gate="pass"
                if (contextual.passed if contextual is not None else report.verdict.passed)
                else "fail",
                baseline_worker=report.baseline_name,
                candidate_worker=Path(report.candidate_source).stem,
                new_critical=report.verdict.new_critical,
                new_high=report.verdict.new_high,
                pass_rate_delta=sum(item.pass_rate_delta for item in report.scenarios)
                / len(report.scenarios)
                if report.scenarios
                else 0,
                path=str(path.relative_to(_artifact_directory())),
                domain_id=contextual.baseline.context.domain_id if contextual else None,
                role_id=contextual.baseline.context.role_id if contextual else None,
                suite_id=contextual.baseline.context.suite_id if contextual else None,
                compatibility=contextual.compatibility.value if contextual else None,
            )
        )
    return tuple(sorted(summaries, key=lambda item: item.id, reverse=True))


def create_app() -> FastAPI:
    """Build the v1 API without starting a server or mutating runtime state."""
    app = FastAPI(
        title="Worker Worlds API",
        version=__version__,
        description="Local versioned control plane for deterministic AI worker evaluations.",
        lifespan=_lifespan,
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
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    router = APIRouter(prefix="/api/v1")
    suite_services: dict[str, DurableSuiteService] = {}

    def service_for(
        job: SuiteJobRecord, repository: PostgresSuiteJobRepository
    ) -> DurableSuiteService:
        available = {scenario_id: item[0] for scenario_id, item in _scenarios().items()}
        config = _config()

        async def create_worker() -> WorkerAdapter:
            return await config.agent_registry().create(job.agent_id, os.environ)

        configured_concurrency = job.configuration.get("concurrency", 4)
        if not isinstance(configured_concurrency, int) or isinstance(configured_concurrency, bool):
            raise ValueError("suite concurrency must be an integer")
        return DurableSuiteService(
            repository,
            available,
            lambda scenario: _world(job.world, scenario),
            create_worker,
            _artifact_directory() / "suite-jobs",
            concurrency=configured_concurrency,
        )

    def schedule_job(job: SuiteJobRecord, repository: PostgresSuiteJobRepository) -> None:
        existing = suite_services.get(job.id)
        if existing is not None and existing.is_active(job.id):
            return
        service = service_for(job, repository)
        suite_services[job.id] = service
        service.schedule(job.id)

        async def retire() -> None:
            try:
                await service.wait(job.id)
            finally:
                if suite_services.get(job.id) is service:
                    suite_services.pop(job.id, None)

        asyncio.create_task(retire())

    async def recover_suite_jobs() -> None:
        """Continuously adopt queued work and expired executor leases."""
        repository = PostgresSuiteJobRepository(DatabaseSettings.from_env())
        while True:
            try:
                for job in await repository.recoverable():
                    if job.status.value == "cancelling":
                        terminal = await repository.finalize_abandoned_cancellation(job.id)
                        await service_for(terminal, repository).ensure_terminal_evidence(terminal)
                    else:
                        schedule_job(job, repository)
                for job in await repository.terminal_without_evidence():
                    await service_for(job, repository).ensure_terminal_evidence(job)
            except Exception as exc:  # recovery must survive temporary database outages
                _LOGGER.warning("suite recovery unavailable (%s)", type(exc).__name__)
            await asyncio.sleep(10)

    app.state.recover_suite_jobs = recover_suite_jobs

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

    @router.get("/catalog", response_model=Catalog)
    async def catalog() -> Catalog:
        return _catalog()

    @router.get("/domains", response_model=tuple[DomainDefinition, ...])
    async def domains() -> tuple[DomainDefinition, ...]:
        return tuple(_catalog().domains)

    @router.get("/capabilities", response_model=tuple[CapabilityDefinition, ...])
    async def capabilities() -> tuple[CapabilityDefinition, ...]:
        return tuple(_catalog().capabilities)

    @router.get("/domains/{domain_id}/roles", response_model=tuple[RoleDefinition, ...])
    async def domain_roles(domain_id: str) -> tuple[RoleDefinition, ...]:
        selected = tuple(role for role in _catalog().roles if role.domain_id == domain_id)
        if not selected:
            raise HTTPException(
                status_code=404,
                detail={"type": "UnknownDomain", "message": "domain not found"},
            )
        return selected

    @router.get("/roles/{role_id}/suites", response_model=tuple[EvaluationSuiteDefinition, ...])
    async def role_suites(role_id: str) -> tuple[EvaluationSuiteDefinition, ...]:
        selected = tuple(suite for suite in _catalog().suites if suite.role_id == role_id)
        if not selected:
            raise HTTPException(
                status_code=404,
                detail={"type": "UnknownRole", "message": "role not found"},
            )
        return selected

    @router.get("/suites/{suite_id}", response_model=EvaluationSuiteDefinition)
    async def suite_detail(suite_id: str) -> EvaluationSuiteDefinition:
        try:
            return _catalog().suite(suite_id)
        except StopIteration as exc:
            raise HTTPException(
                status_code=404,
                detail={"type": "UnknownSuite", "message": "suite not found"},
            ) from exc

    @router.post("/suite-jobs", response_model=SuiteJobRecord, status_code=202)
    async def create_suite_job(request: CreateSuiteJobRequest) -> SuiteJobRecord:
        catalog_data = _catalog()
        try:
            role = catalog_data.role(request.role_id)
            suite = catalog_data.suite(request.suite_id)
        except StopIteration as exc:
            raise HTTPException(
                status_code=404,
                detail={"type": "UnknownCatalogSelection", "message": "role or suite not found"},
            ) from exc
        if role.domain_id != request.domain_id or (
            suite.domain_id != request.domain_id or suite.role_id != request.role_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "IncompatibleEvaluationSelection",
                    "message": "domain, role, and suite do not match",
                },
            )
        _validate_world_selection(request.domain_id, request.role_id, request.world)
        config = _config()
        if request.agent_id not in config.agents:
            raise HTTPException(
                status_code=404,
                detail={"type": "UnknownAgent", "message": "registered agent not found"},
            )
        readiness = _agent_summary(config, request.agent_id)
        if request.domain_id not in readiness.supported_domain_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "IncompatibleAgentSelection",
                    "message": "registered agent does not support the selected domain",
                },
            )
        if not readiness.ready:
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "AgentNotReady",
                    "message": "registered agent is not ready",
                    "missing_requirements": list(readiness.missing_requirements),
                },
            )
        available = {scenario_id: item[0] for scenario_id, item in _scenarios().items()}
        allowed_for_role = {
            str(item.scenario_id)
            for item in catalog_data.classifications
            if item.domain_id == request.domain_id and request.role_id in item.role_ids
        }
        requested_scenarios = set(request.scenario_ids)
        if suite.tier.value == "custom" and any(
            item not in allowed_for_role for item in requested_scenarios
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "IncompatibleCustomScenario",
                    "message": "custom suite contains a scenario outside the selected role",
                },
            )
        selected_scenario_ids = (
            tuple(
                item.scenario_id
                for item in catalog_data.classifications
                if str(item.scenario_id) in requested_scenarios
            )
            if suite.tier.value == "custom"
            else suite.scenario_ids
        )
        if suite.tier.value == "custom" and not selected_scenario_ids:
            raise HTTPException(
                status_code=422,
                detail={"type": "EmptyCustomSuite", "message": "select at least one scenario"},
            )
        if any(str(item) not in allowed_for_role for item in selected_scenario_ids):
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "IncompatibleCustomScenario",
                    "message": "custom suite contains a scenario outside the selected role",
                },
            )
        if request.budget is not None and len(selected_scenario_ids) > request.budget.scenarios:
            raise HTTPException(
                status_code=422,
                detail={
                    "type": "SuiteScenarioBudgetExceeded",
                    "message": "selected scenarios exceed the configured suite budget",
                },
            )
        missing = [str(item) for item in selected_scenario_ids if str(item) not in available]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "SuiteScenariosUnavailable",
                    "message": "configured scenario roots do not contain the complete suite",
                    "missing_count": len(missing),
                },
            )
        if readiness.adapter != "stub":
            not_live_ready = [
                str(item)
                for item in selected_scenario_ids
                if available[str(item)].metadata.get("live_ready") is not True
            ]
            if not_live_ready:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "ScenarioNotLiveReady",
                        "message": "selected scenarios are not approved for live adapters",
                        "scenario_count": len(not_live_ready),
                    },
                )
        classifications = {str(item.scenario_id): item for item in catalog_data.classifications}
        mismatched = [
            str(item)
            for item in selected_scenario_ids
            if scenario_content_hash(available[str(item)])
            != classifications[str(item)].scenario_hash
        ]
        if mismatched:
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "ScenarioCatalogHashMismatch",
                    "message": "loaded scenario does not match the reviewed catalog",
                    "mismatch_count": len(mismatched),
                },
            )
        effective_available = dict(available)
        for scenario_id in selected_scenario_ids:
            effective = effective_available[str(scenario_id)]
            if request.seed is not None:
                effective = effective.model_copy(
                    update={"world": effective.world.model_copy(update={"seed": request.seed})}
                )
            if request.limits is not None:
                effective = effective.model_copy(update={"limits": request.limits})
            effective_available[str(scenario_id)] = effective
        effective_suite = suite.model_copy(
            update={
                "scenario_ids": selected_scenario_ids,
                "default_limits": request.limits or suite.default_limits,
            }
        )
        repository = PostgresSuiteJobRepository(DatabaseSettings.from_env())
        create = SuiteJobCreate(
            request_key=request.request_key,
            catalog_version=catalog_data.catalog_version,
            domain_id=request.domain_id,
            role_id=request.role_id,
            suite_id=request.suite_id,
            suite_revision=suite.revision,
            agent_id=request.agent_id,
            world=request.world,
            scenario_ids=selected_scenario_ids,
            configuration={
                "concurrency": request.concurrency,
                "infrastructure_retries": 1,
                "seed_override": request.seed,
                "limits_override": request.limits.model_dump(mode="json")
                if request.limits is not None
                else None,
                "suite_budget": request.budget.model_dump(mode="json")
                if request.budget is not None
                else None,
                "suite_tier": suite.tier.value,
                "default_limits": effective_suite.default_limits.model_dump(mode="json"),
                "evaluation_context": build_suite_context(
                    catalog_data,
                    effective_suite,
                    effective_available,
                    agent_id=request.agent_id,
                    agent_version=readiness.version,
                    world_name=request.world,
                    world_version=world_version(request.world),
                ).model_dump(mode="json"),
            },
        )
        try:
            job = await repository.create(create)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"type": "IdempotencyConflict", "message": str(exc)},
            ) from exc

        schedule_job(job, repository)
        return job

    @router.get("/suite-jobs", response_model=SuiteJobListResponse)
    async def suite_jobs(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> SuiteJobListResponse:
        items = await PostgresSuiteJobRepository(DatabaseSettings.from_env()).list(limit=limit)
        return SuiteJobListResponse(jobs=items, total=len(items))

    @router.get("/suite-jobs/{job_id}", response_model=SuiteJobRecord)
    async def suite_job_detail(job_id: str) -> SuiteJobRecord:
        if not _SAFE_ID.fullmatch(job_id):
            raise HTTPException(status_code=404, detail="suite job not found")
        try:
            return await PostgresSuiteJobRepository(DatabaseSettings.from_env()).get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="suite job not found") from exc

    @router.delete("/suite-jobs/{job_id}", response_model=SuiteJobRecord)
    async def cancel_suite_job(job_id: str) -> SuiteJobRecord:
        service = suite_services.get(job_id)
        try:
            if service is not None:
                return await service.cancel(job_id)
            repository = PostgresSuiteJobRepository(DatabaseSettings.from_env())
            job = await repository.request_cancel(job_id)
            if job.status.value == "cancelled":
                return await service_for(job, repository).ensure_terminal_evidence(job)
            return job
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="suite job not found") from exc

    @router.get("/suite-jobs/{job_id}/evidence")
    async def suite_job_evidence(job_id: str) -> FileResponse:
        try:
            job = await PostgresSuiteJobRepository(DatabaseSettings.from_env()).get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="suite job not found") from exc
        if not job.suite_record_path:
            raise HTTPException(status_code=409, detail="suite evidence is not ready")
        root = (_artifact_directory() / "suite-jobs").resolve()
        path = (root / job.suite_record_path).resolve()
        if root not in path.parents or path.is_symlink() or not path.is_file():
            raise HTTPException(status_code=500, detail="stored suite evidence is invalid")
        bundle = path.parent / "evidence.zip"
        if not bundle.is_file() or bundle.is_symlink():
            raise HTTPException(status_code=409, detail="suite evidence bundle is not ready")
        return FileResponse(bundle, media_type="application/zip", filename=f"{job_id}.zip")

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
        selected_catalog = _catalog()
        reviewed = next(
            (
                item
                for item in selected_catalog.classifications
                if str(item.scenario_id) == request.scenario_id
            ),
            None,
        )
        if reviewed is not None and scenario_content_hash(scenario) != reviewed.scenario_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "ScenarioCatalogHashMismatch",
                    "message": "loaded scenario does not match the reviewed catalog",
                },
            )
        context = None
        worker = _worker(request.worker)
        context_agent_version = worker.worker_version
        if request.agent_id is not None:
            configured_agent = _config().agents.get(request.agent_id)
            if configured_agent is not None:
                context_agent_version = configured_agent.version
        context_fields = (request.domain_id, request.role_id, request.suite_id)
        if any(item is not None for item in context_fields):
            if request.domain_id is None or request.role_id is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "type": "IncompleteEvaluationContext",
                        "message": "domain_id and role_id are required together",
                    },
                )
            try:
                context = build_context(
                    selected_catalog,
                    scenario,
                    domain_id=request.domain_id,
                    role_id=request.role_id,
                    suite_id=request.suite_id,
                    agent_id=request.agent_id or request.worker,
                    agent_version=context_agent_version,
                    world_name=request.world,
                    world_version=world_version(request.world),
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"type": "IncompatibleEvaluationSelection", "message": str(exc)},
                ) from exc
            _validate_world_selection(request.domain_id, request.role_id, request.world)
        if request.agent_id is not None:
            config = _config()
            if request.agent_id not in config.agents:
                raise HTTPException(
                    status_code=404,
                    detail={"type": "UnknownAgent", "message": "registered agent not found"},
                )
            summary = _agent_summary(config, request.agent_id)
            if context is not None and context.domain_id not in summary.supported_domain_ids:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "IncompatibleAgentSelection",
                        "message": "registered agent does not support the selected domain",
                    },
                )
            if not summary.ready:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "AgentNotReady",
                        "message": "registered agent is not ready",
                        "missing_requirements": list(summary.missing_requirements),
                    },
                )
            if summary.adapter != "stub" and scenario.metadata.get("live_ready") is not True:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "ScenarioNotLiveReady",
                        "message": "selected scenario is not approved for live adapters",
                        "scenario_count": 1,
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
        if context is not None:
            _write_context_manifest(record, context)
        return record

    @router.get("/comparisons", response_model=ComparisonListResponse)
    async def comparisons() -> ComparisonListResponse:
        items = _comparison_summaries()
        return ComparisonListResponse(comparisons=items, total=len(items))

    @router.post(
        "/comparisons/contextual", response_model=ContextualComparisonRecord, status_code=201
    )
    async def create_contextual_comparison(
        request: CreateContextualComparisonRequest,
    ) -> ContextualComparisonRecord:
        if not _SAFE_ID.fullmatch(request.baseline_job_id) or not _SAFE_ID.fullmatch(
            request.candidate_job_id
        ):
            raise HTTPException(status_code=404, detail="suite job not found")
        repository = PostgresSuiteJobRepository(DatabaseSettings.from_env())
        try:
            baseline_job = await repository.get(request.baseline_job_id)
            candidate_job = await repository.get(request.candidate_job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="suite job not found") from exc

        def evidence(job: SuiteJobRecord) -> tuple[SuiteRecord, EvaluationContext]:
            if job.status.value != "completed" or job.suite_record_path is None:
                raise HTTPException(status_code=409, detail="suite job evidence is incomplete")
            root = (_artifact_directory() / "suite-jobs").resolve()
            path = (root / job.suite_record_path).resolve()
            if root not in path.parents or path.is_symlink() or not path.is_file():
                raise HTTPException(status_code=500, detail="stored suite evidence is invalid")
            raw_context = job.configuration.get("evaluation_context")
            if not isinstance(raw_context, dict):
                raise HTTPException(status_code=409, detail="suite evaluation context is missing")
            try:
                return (
                    SuiteRecord.model_validate_json(path.read_text(encoding="utf-8")),
                    EvaluationContext.model_validate(raw_context),
                )
            except (OSError, ValidationError) as exc:
                raise HTTPException(
                    status_code=500, detail="stored suite evidence is invalid"
                ) from exc

        baseline_suite, baseline_context = evidence(baseline_job)
        candidate_suite, candidate_context = evidence(candidate_job)
        record = compare_contextual_suites(
            baseline_suite,
            candidate_suite,
            baseline_context,
            candidate_context,
        )
        output = _artifact_directory() / "comparisons" / "contextual" / record.id
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output, prefix=".comparison-", delete=False
        ) as handle:
            handle.write(record.canonical_json() + "\n")
            temporary = Path(handle.name)
        await asyncio.to_thread(temporary.replace, output / "comparison.json")
        return record

    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    """Serve the local API with explicit host/port configuration."""
    host = os.environ.get("WORKER_WORLDS_API_HOST", "127.0.0.1")
    if (
        host not in {"127.0.0.1", "localhost", "::1"}
        and os.environ.get("WORKER_WORLDS_ALLOW_NON_LOOPBACK_API") != "1"
    ):
        raise SystemExit(
            "refusing non-loopback API bind without WORKER_WORLDS_ALLOW_NON_LOOPBACK_API=1"
        )
    port = int(os.environ.get("WORKER_WORLDS_API_PORT", "8000"))
    uvicorn.run("worker_worlds.api:app", host=host, port=port, reload=False)
