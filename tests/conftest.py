"""Shared contract fixtures."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from worker_worlds.contracts import Scenario
from worker_worlds.database import close_all_pools
from worker_worlds.scenarios import load_scenario


@pytest.fixture
def happy_scenario() -> Scenario:
    return load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))


@pytest.fixture(autouse=True)
async def _close_database_pools_after_each_test() -> AsyncIterator[None]:
    """Release any asyncpg pool this test's event loop created via ``get_pool``.

    ``get_pool`` (database.py) caches one connection pool (up to
    ``max_size=20`` real connections) per ``(event loop, database URL)``
    pair, and pytest-asyncio gives each async test function its own fresh
    event loop by default (``asyncio_mode = "auto"``, no session-scoped
    override). Without an explicit close, every test that touches
    ``get_pool`` -- directly, or via ``migrate``, ``database_health``, or
    any ``PostgresSuiteJobRepository`` method -- leaks its pool: the
    connections are never gracefully closed (asyncpg needs an awaited
    ``close()`` on the same loop to send the wire-protocol termination
    message), so they accumulate across the whole test run until
    Postgres's ``max_connections`` is exhausted
    (``asyncpg.exceptions.TooManyConnectionsError``). Tests that never
    call ``get_pool`` are unaffected -- ``close_all_pools`` is a no-op
    when the current loop has no cached pool.
    """
    yield
    await close_all_pools()
