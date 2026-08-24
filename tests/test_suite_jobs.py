from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest

from worker_worlds.catalog import builtin_catalog
from worker_worlds.contracts import RunId
from worker_worlds.database import DatabaseSettings, connect, migrate
from worker_worlds.ids import prefixed_ulid
from worker_worlds.suite_jobs import (
    PostgresSuiteJobRepository,
    SuiteJobCreate,
    SuiteJobStatus,
    SuiteScenarioStatus,
)


@pytest.fixture
def suite_job_settings() -> DatabaseSettings:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    return DatabaseSettings(url=url)


@pytest.fixture(autouse=True)
async def cleanup_created_suite_jobs(
    suite_job_settings: DatabaseSettings,
) -> AsyncIterator[None]:
    """Keep every repository test isolated inside the explicitly configured test database."""
    await migrate(suite_job_settings)
    connection = await connect(suite_job_settings)
    try:
        existing = {
            str(row["id"])
            for row in await connection.fetch("SELECT id FROM worker_worlds.suite_jobs")
        }
    finally:
        await connection.close()
    yield
    connection = await connect(suite_job_settings)
    try:
        created = await connection.fetch("SELECT id FROM worker_worlds.suite_jobs")
        identifiers = [str(row["id"]) for row in created if str(row["id"]) not in existing]
        if identifiers:
            await connection.execute(
                "DELETE FROM worker_worlds.suite_jobs WHERE id=ANY($1::text[])", identifiers
            )
    finally:
        await connection.close()


def _request(key: str) -> SuiteJobCreate:
    catalog = builtin_catalog()
    suite = catalog.suite("commerce.refund-specialist.smoke")
    return SuiteJobCreate(
        request_key=key,
        catalog_version=catalog.catalog_version,
        domain_id=suite.domain_id,
        role_id=suite.role_id,
        suite_id=suite.id,
        suite_revision=suite.revision,
        agent_id="local-stub",
        world="stub",
        scenario_ids=suite.scenario_ids,
        configuration={"concurrency": 2},
    )


async def test_suite_job_progress_is_monotonic_and_idempotent(
    suite_job_settings: DatabaseSettings,
) -> None:
    assert await migrate(suite_job_settings) == "006"
    repository = PostgresSuiteJobRepository(suite_job_settings)
    key = prefixed_ulid("request")
    request = _request(key)
    first, second = await asyncio.gather(repository.create(request), repository.create(request))
    assert first.id == second.id
    assert first.status is SuiteJobStatus.QUEUED
    assert await repository.claim(first.id)
    scenario_id = first.scenarios[0].scenario_id
    assert await repository.scenario_started(first.id, scenario_id)
    await repository.scenario_finished(
        first.id,
        scenario_id,
        status=SuiteScenarioStatus.PASSED,
        run_id=RunId(prefixed_ulid("run")),
        record_hash="a" * 64,
        terminal_reason="completed",
        error_type=None,
        error_message=None,
    )
    after = await repository.get(first.id)
    assert after.completed_scenarios == 1
    assert after.passed_scenarios == 1
    assert after.revision > first.revision
    terminal = await repository.finish(first.id, suite_record_path=f"{first.id}/suite.json")
    assert terminal.status is SuiteJobStatus.COMPLETED
    assert terminal.ended_at is not None


async def test_queued_suite_cancellation_is_terminal_and_idempotent(
    suite_job_settings: DatabaseSettings,
) -> None:
    repository = PostgresSuiteJobRepository(suite_job_settings)
    job = await repository.create(_request(prefixed_ulid("request")))
    cancelled = await repository.request_cancel(job.id)
    again = await repository.request_cancel(job.id)
    assert cancelled.status is SuiteJobStatus.CANCELLED
    assert again.status is SuiteJobStatus.CANCELLED
    assert all(item.status is SuiteScenarioStatus.CANCELLED for item in again.scenarios)


async def test_suite_request_key_conflict_is_rejected(
    suite_job_settings: DatabaseSettings,
) -> None:
    repository = PostgresSuiteJobRepository(suite_job_settings)
    request = _request(prefixed_ulid("request"))
    await repository.create(request)
    conflicting = request.model_copy(update={"world": "postgres"})
    with pytest.raises(ValueError, match="conflicting input"):
        await repository.create(conflicting)


async def test_suite_job_cleanup_uses_exact_ids(suite_job_settings: DatabaseSettings) -> None:
    """Prove tests can remove only the exact global job rows they created."""
    repository = PostgresSuiteJobRepository(suite_job_settings)
    job = await repository.create(_request(prefixed_ulid("request")))
    connection = await connect(suite_job_settings)
    try:
        deleted = await connection.execute(
            "DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id
        )
    finally:
        await connection.close()
    assert deleted.endswith("1")


async def test_suite_job_listing_is_newest_first(suite_job_settings: DatabaseSettings) -> None:
    repository = PostgresSuiteJobRepository(suite_job_settings)
    first = await repository.create(_request(prefixed_ulid("request")))
    second = await repository.create(_request(prefixed_ulid("request")))
    try:
        listed = await repository.list(limit=500)
        positions = {item.id: index for index, item in enumerate(listed)}
        assert positions[second.id] < positions[first.id]
    finally:
        connection = await connect(suite_job_settings)
        try:
            await connection.execute(
                "DELETE FROM worker_worlds.suite_jobs WHERE id=ANY($1::text[])",
                [first.id, second.id],
            )
        finally:
            await connection.close()


async def test_expired_executor_takeover_is_fenced(
    suite_job_settings: DatabaseSettings,
) -> None:
    first_repository = PostgresSuiteJobRepository(suite_job_settings, lease_seconds=5)
    second_repository = PostgresSuiteJobRepository(suite_job_settings, lease_seconds=5)
    job = await first_repository.create(_request(prefixed_ulid("request")))
    try:
        assert await first_repository.claim(job.id)
        scenario_id = job.scenarios[0].scenario_id
        assert await first_repository.scenario_started(job.id, scenario_id)
        connection = await connect(suite_job_settings)
        try:
            await connection.execute(
                "UPDATE worker_worlds.suite_jobs SET executor_expires_at=now()-interval '1 second' "
                "WHERE id=$1",
                job.id,
            )
        finally:
            await connection.close()
        recoverable = await second_repository.recoverable()
        assert job.id in {item.id for item in recoverable}
        assert await second_repository.claim(job.id)
        taken_over = await second_repository.get(job.id)
        assert taken_over.scenarios[0].status is SuiteScenarioStatus.PENDING
        with pytest.raises(RuntimeError, match="ownership was lost"):
            await first_repository.finish(job.id)
        assert await second_repository.heartbeat(job.id)
        terminal = await second_repository.finish(job.id)
        assert terminal.status is SuiteJobStatus.COMPLETED
        with pytest.raises(RuntimeError, match="already terminal"):
            await first_repository.finish(job.id)
    finally:
        connection = await connect(suite_job_settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()


async def test_terminal_cancellation_is_idempotent(
    suite_job_settings: DatabaseSettings,
) -> None:
    repository = PostgresSuiteJobRepository(suite_job_settings)
    job = await repository.create(_request(prefixed_ulid("request")))
    try:
        first = await repository.request_cancel(job.id)
        second = await repository.request_cancel(job.id)
        assert second.status is SuiteJobStatus.CANCELLED
        assert second.revision == first.revision
        assert second.updated_at == first.updated_at
    finally:
        connection = await connect(suite_job_settings)
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job.id)
        finally:
            await connection.close()
