from __future__ import annotations

import asyncio
import json
import os
import zipfile
from pathlib import Path

import pytest

from worker_worlds.catalog import builtin_catalog
from worker_worlds.contracts import Scenario, SuiteRecord, ToolResult, WorkerTurn, WorldSnapshot
from worker_worlds.database import DatabaseSettings, connect
from worker_worlds.enterprise_scenarios import campaign_analyst_scenarios, claims_analyst_scenarios
from worker_worlds.errors import InfrastructureError
from worker_worlds.ids import prefixed_ulid
from worker_worlds.insurance import InsuranceWorld
from worker_worlds.marketing import MarketingWorld
from worker_worlds.scenario_library import reviewed_scenarios
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld
from worker_worlds.suite_jobs import (
    PostgresSuiteJobRepository,
    SuiteBudget,
    SuiteJobCreate,
    SuiteJobStatus,
    SuiteScenarioStatus,
)
from worker_worlds.suite_service import DurableSuiteService, SuiteBudgetExceeded


class ResetFailureWorld(StubWorld):
    async def reset(self, *, seed: int, run_id: str) -> WorldSnapshot:
        del seed, run_id
        raise InfrastructureError("injected transient reset failure")


class SlowWorker(StubWorkerAdapter):
    async def next_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        await asyncio.sleep(2)
        return await super().next_turn(tool_result)


async def test_aggregate_suite_budget_stops_job_and_preserves_attempt_evidence(
    tmp_path: Path,
) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = load_scenario(Path("examples/scenarios/refund_happy.yaml"))
    repository = PostgresSuiteJobRepository(settings)
    budget = SuiteBudget(mutations=0)
    job = await repository.create(
        SuiteJobCreate(
            request_key=prefixed_ulid("request"),
            catalog_version="1.0.0",
            domain_id="commerce",
            role_id="refund-specialist",
            suite_id="commerce.refund-specialist.custom",
            suite_revision="1.0.0",
            agent_id="local-stub",
            world="stub",
            scenario_ids=(scenario.id,),
            configuration={"suite_budget": budget.model_dump(mode="json")},
        )
    )

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    service = DurableSuiteService(
        repository,
        {str(scenario.id): scenario},
        lambda _scenario: StubWorld(),
        worker,
        tmp_path,
        concurrency=1,
    )
    try:
        service.schedule(job.id)
        failed = await service.wait(job.id)
        assert failed.status is SuiteJobStatus.FAILED
        assert failed.error_type == SuiteBudgetExceeded.__name__
        assert failed.scenarios[0].status is SuiteScenarioStatus.ERROR
        assert failed.completed_scenarios == 1
        assert failed.failed_scenarios == 1
        assert failed.suite_record_path is not None
        suite = SuiteRecord.model_validate_json(
            (tmp_path / failed.suite_record_path).read_text(encoding="utf-8")
        )
        assert len(suite.runs) == 1
        assert suite.runs[0].mutation_count == 1
    finally:
        connection = await connect(settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()


async def test_suite_deadline_cannot_be_suppressed_by_runner_cancellation(
    tmp_path: Path,
) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = load_scenario(Path("examples/scenarios/refund_happy.yaml"))
    repository = PostgresSuiteJobRepository(settings)
    job = await repository.create(
        SuiteJobCreate(
            request_key=prefixed_ulid("request"),
            catalog_version="1.0.0",
            domain_id="commerce",
            role_id="refund-specialist",
            suite_id="commerce.refund-specialist.custom",
            suite_revision="1.0.0",
            agent_id="local-stub",
            world="stub",
            scenario_ids=(scenario.id,),
            configuration={"suite_budget": SuiteBudget(deadline_s=1).model_dump(mode="json")},
        )
    )

    async def worker() -> StubWorkerAdapter:
        return SlowWorker()

    service = DurableSuiteService(
        repository,
        {str(scenario.id): scenario},
        lambda _scenario: StubWorld(),
        worker,
        tmp_path,
        concurrency=1,
    )
    try:
        service.schedule(job.id)
        failed = await service.wait(job.id)
        assert failed.status is SuiteJobStatus.FAILED
        assert failed.error_type == SuiteBudgetExceeded.__name__
        assert failed.scenarios[0].status is SuiteScenarioStatus.FAILED
        assert failed.completed_scenarios == 1
        assert failed.failed_scenarios == 1
    finally:
        connection = await connect(settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()


async def test_durable_suite_service_writes_complete_downloadable_evidence(
    tmp_path: Path,
) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    catalog = builtin_catalog()
    suite = catalog.suite("commerce.refund-specialist.smoke")
    available = {str(item.id): item for item in reviewed_scenarios()}
    repository = PostgresSuiteJobRepository(settings)
    job = await repository.create(
        SuiteJobCreate(
            request_key=prefixed_ulid("request"),
            catalog_version=catalog.catalog_version,
            domain_id=suite.domain_id,
            role_id=suite.role_id,
            suite_id=suite.id,
            suite_revision=suite.revision,
            agent_id="local-stub",
            world="stub",
            scenario_ids=suite.scenario_ids,
            configuration={"concurrency": 3},
        )
    )

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    service = DurableSuiteService(
        repository,
        available,
        lambda _scenario: StubWorld(),
        worker,
        tmp_path,
        concurrency=3,
    )
    try:
        service.schedule(job.id)
        completed = await service.wait(job.id)
        assert completed.status is SuiteJobStatus.COMPLETED
        assert completed.completed_scenarios == len(suite.scenario_ids)
        assert completed.suite_record_path is not None
        suite_path = tmp_path / completed.suite_record_path
        bundle = suite_path.parent / "evidence.zip"
        assert bundle.is_file()
        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            persisted_suite = SuiteRecord.model_validate_json(archive.read("suite.json"))
        assert {"suite.json", "manifest.json", "junit.xml", "report.html"} <= names
        assert len([name for name in names if name.startswith("runs/")]) == len(suite.scenario_ids)
        assert manifest["suite_record_hash"] == persisted_suite.canonical_hash()
        assert manifest["job"]["status"] == "completed"
        assert manifest["job"]["suite_record_path"] == completed.suite_record_path
    finally:
        connection = await connect(settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()


async def test_suite_retries_only_typed_infrastructure_failure_and_preserves_attempts(
    tmp_path: Path,
) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = load_scenario(Path("examples/scenarios/refund_happy.yaml"))
    repository = PostgresSuiteJobRepository(settings)
    job = await repository.create(
        SuiteJobCreate(
            request_key=prefixed_ulid("request"),
            catalog_version="1.0.0",
            domain_id="commerce",
            role_id="refund-specialist",
            suite_id="commerce.refund-specialist.custom",
            suite_revision="1.0.0",
            agent_id="local-stub",
            world="stub",
            scenario_ids=(scenario.id,),
            configuration={"concurrency": 1, "infrastructure_retries": 1},
        )
    )
    calls = 0

    def world_factory(_scenario: Scenario) -> StubWorld:
        nonlocal calls
        calls += 1
        return ResetFailureWorld() if calls == 1 else StubWorld()

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    service = DurableSuiteService(
        repository,
        {str(scenario.id): scenario},
        world_factory,
        worker,
        tmp_path,
        concurrency=1,
    )
    try:
        service.schedule(job.id)
        completed = await service.wait(job.id)
        assert completed.status is SuiteJobStatus.COMPLETED
        assert completed.scenarios[0].attempts == 2
        assert completed.suite_record_path is not None
        suite_text = await asyncio.to_thread(
            (tmp_path / completed.suite_record_path).read_text, encoding="utf-8"
        )
        suite = SuiteRecord.model_validate_json(suite_text)
        assert len(suite.runs) == 2
        assert suite.runs[0].incomplete_evidence
        assert suite.runs[0].error_type == "InfrastructureError"
        assert suite.runs[1].passed
    finally:
        connection = await connect(settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()


async def test_queued_cancellation_produces_terminal_empty_evidence(tmp_path: Path) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    catalog = builtin_catalog()
    suite = catalog.suite("commerce.refund-specialist.smoke")
    repository = PostgresSuiteJobRepository(settings)
    job = await repository.create(
        SuiteJobCreate(
            request_key=prefixed_ulid("request"),
            catalog_version=catalog.catalog_version,
            domain_id=suite.domain_id,
            role_id=suite.role_id,
            suite_id=suite.id,
            suite_revision=suite.revision,
            agent_id="local-stub",
            world="stub",
            scenario_ids=suite.scenario_ids,
            configuration={"concurrency": 1},
        )
    )

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    service = DurableSuiteService(
        repository,
        {str(item.id): item for item in reviewed_scenarios()},
        lambda _scenario: StubWorld(),
        worker,
        tmp_path,
        concurrency=1,
    )
    try:
        cancelled = await repository.request_cancel(job.id)
        terminal = await service.ensure_terminal_evidence(cancelled)
        assert terminal.status is SuiteJobStatus.CANCELLED
        assert terminal.suite_record_path is not None
        suite_path = tmp_path / terminal.suite_record_path
        suite_record = SuiteRecord.model_validate_json(
            await asyncio.to_thread(suite_path.read_text, encoding="utf-8")
        )
        assert suite_record.runs == ()
        with zipfile.ZipFile(suite_path.parent / "evidence.zip") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["job"]["status"] == "cancelled"
    finally:
        connection = await connect(settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()


async def test_ten_concurrent_suite_jobs_keep_progress_and_evidence_isolated(
    tmp_path: Path,
) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = load_scenario(Path("examples/scenarios/refund_happy.yaml"))
    repository = PostgresSuiteJobRepository(settings)
    jobs = await asyncio.gather(
        *(
            repository.create(
                SuiteJobCreate(
                    request_key=prefixed_ulid("request"),
                    catalog_version="1.0.0",
                    domain_id="commerce",
                    role_id="refund-specialist",
                    suite_id="commerce.refund-specialist.custom",
                    suite_revision="1.0.0",
                    agent_id="local-stub",
                    world="stub",
                    scenario_ids=(scenario.id,),
                    configuration={"concurrency": 1},
                )
            )
            for _ in range(10)
        )
    )

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    service = DurableSuiteService(
        repository,
        {str(scenario.id): scenario},
        lambda _scenario: StubWorld(),
        worker,
        tmp_path,
        concurrency=10,
    )
    try:
        for job in jobs:
            service.schedule(job.id)
        completed = await asyncio.gather(*(service.wait(job.id) for job in jobs))
        assert all(item.status is SuiteJobStatus.COMPLETED for item in completed)
        assert all(item.completed_scenarios == 1 for item in completed)
        assert len({item.suite_record_path for item in completed}) == 10
        evidence_exists = await asyncio.gather(
            *(
                asyncio.to_thread(
                    (tmp_path / str(item.suite_record_path)).parent.joinpath("evidence.zip").is_file
                )
                for item in completed
            )
        )
        assert all(evidence_exists)
    finally:
        connection = await connect(settings)
        try:
            await connection.execute(
                "DELETE FROM worker_worlds.suite_jobs WHERE id=ANY($1::text[])",
                [job.id for job in jobs],
            )
        finally:
            await connection.close()


async def test_ten_concurrent_claims_analyst_suite_jobs_keep_progress_and_evidence_isolated(
    tmp_path: Path,
) -> None:
    """The same isolation guarantee, exercised with real InsuranceWorld/Postgres worlds."""
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = claims_analyst_scenarios()[0]
    repository = PostgresSuiteJobRepository(settings)
    jobs = await asyncio.gather(
        *(
            repository.create(
                SuiteJobCreate(
                    request_key=prefixed_ulid("request"),
                    catalog_version="1.0.0",
                    domain_id="insurance",
                    role_id="claims-analyst",
                    suite_id="insurance.claims-analyst.custom",
                    suite_revision="1.0.0",
                    agent_id="local-stub",
                    world="insurance",
                    scenario_ids=(str(scenario.id),),
                    configuration={"concurrency": 1},
                )
            )
            for _ in range(10)
        )
    )

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    def world_factory(target_scenario: Scenario) -> InsuranceWorld:
        return InsuranceWorld(settings, str(target_scenario.id))

    service = DurableSuiteService(
        repository,
        {str(scenario.id): scenario},
        world_factory,
        worker,
        tmp_path,
        concurrency=10,
    )
    try:
        for job in jobs:
            service.schedule(job.id)
        completed = await asyncio.gather(*(service.wait(job.id) for job in jobs))
        assert all(item.status is SuiteJobStatus.COMPLETED for item in completed)
        assert all(item.completed_scenarios == 1 for item in completed)
        assert all(item.passed_scenarios == 1 for item in completed)
        assert len({item.suite_record_path for item in completed}) == 10
        evidence_exists = await asyncio.gather(
            *(
                asyncio.to_thread(
                    (tmp_path / str(item.suite_record_path)).parent.joinpath("evidence.zip").is_file
                )
                for item in completed
            )
        )
        assert all(evidence_exists)
    finally:
        connection = await connect(settings)
        try:
            await connection.execute(
                "DELETE FROM worker_worlds.suite_jobs WHERE id=ANY($1::text[])",
                [job.id for job in jobs],
            )
        finally:
            await connection.close()


async def test_ten_concurrent_campaign_analyst_suite_jobs_keep_progress_and_evidence_isolated(
    tmp_path: Path,
) -> None:
    """The same isolation guarantee, exercised with real MarketingWorld/Postgres worlds."""
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = campaign_analyst_scenarios()[0]
    repository = PostgresSuiteJobRepository(settings)
    jobs = await asyncio.gather(
        *(
            repository.create(
                SuiteJobCreate(
                    request_key=prefixed_ulid("request"),
                    catalog_version="1.0.0",
                    domain_id="marketing",
                    role_id="campaign-analyst",
                    suite_id="marketing.campaign-analyst.custom",
                    suite_revision="1.0.0",
                    agent_id="local-stub",
                    world="marketing",
                    scenario_ids=(str(scenario.id),),
                    configuration={"concurrency": 1},
                )
            )
            for _ in range(10)
        )
    )

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    def world_factory(target_scenario: Scenario) -> MarketingWorld:
        return MarketingWorld(settings, str(target_scenario.id))

    service = DurableSuiteService(
        repository,
        {str(scenario.id): scenario},
        world_factory,
        worker,
        tmp_path,
        concurrency=10,
    )
    try:
        for job in jobs:
            service.schedule(job.id)
        completed = await asyncio.gather(*(service.wait(job.id) for job in jobs))
        assert all(item.status is SuiteJobStatus.COMPLETED for item in completed)
        assert all(item.completed_scenarios == 1 for item in completed)
        assert all(item.passed_scenarios == 1 for item in completed)
        assert len({item.suite_record_path for item in completed}) == 10
        evidence_exists = await asyncio.gather(
            *(
                asyncio.to_thread(
                    (tmp_path / str(item.suite_record_path)).parent.joinpath("evidence.zip").is_file
                )
                for item in completed
            )
        )
        assert all(evidence_exists)
    finally:
        connection = await connect(settings)
        try:
            await connection.execute(
                "DELETE FROM worker_worlds.suite_jobs WHERE id=ANY($1::text[])",
                [job.id for job in jobs],
            )
        finally:
            await connection.close()


async def test_expired_running_suite_resumes_with_a_new_executor(tmp_path: Path) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    settings = DatabaseSettings(url=url)
    scenario = load_scenario(Path("examples/scenarios/refund_happy.yaml"))
    abandoned = PostgresSuiteJobRepository(settings, lease_seconds=5)
    job = await abandoned.create(
        SuiteJobCreate(
            request_key=prefixed_ulid("request"),
            catalog_version="1.0.0",
            domain_id="commerce",
            role_id="refund-specialist",
            suite_id="commerce.refund-specialist.custom",
            suite_revision="1.0.0",
            agent_id="local-stub",
            world="stub",
            scenario_ids=(scenario.id,),
            configuration={"concurrency": 1},
        )
    )
    assert await abandoned.claim(job.id)
    assert await abandoned.scenario_started(job.id, scenario.id)
    connection = await connect(settings)
    try:
        await connection.execute(
            "UPDATE worker_worlds.suite_jobs SET executor_expires_at=now()-interval '1 second' "
            "WHERE id=$1",
            job.id,
        )
    finally:
        await connection.close()
    recovering = PostgresSuiteJobRepository(settings, lease_seconds=5)
    assert job.id in {item.id for item in await recovering.recoverable()}

    async def worker() -> StubWorkerAdapter:
        return StubWorkerAdapter()

    service = DurableSuiteService(
        recovering,
        {str(scenario.id): scenario},
        lambda _scenario: StubWorld(),
        worker,
        tmp_path,
        concurrency=1,
    )
    try:
        service.schedule(job.id)
        completed = await service.wait(job.id)
        assert completed.status is SuiteJobStatus.COMPLETED
        assert completed.scenarios[0].attempts == 2
        assert completed.suite_record_path is not None
    finally:
        connection = await connect(settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()
