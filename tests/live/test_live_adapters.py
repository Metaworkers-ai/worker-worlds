"""Credential-guarded provider smoke tests; never enabled by default."""

from __future__ import annotations

import asyncio
import os
from importlib.metadata import version

import pytest

pytestmark = pytest.mark.live


def _enabled() -> bool:
    return os.environ.get("WORKER_WORLDS_LIVE_SMOKE") == "1"


@pytest.mark.parametrize("adapter_name", ["langgraph", "openai-agents"])
def test_optional_live_provider_smoke(adapter_name: str) -> None:
    """Make one bounded synthetic provider request for each adapter package."""
    if not _enabled():
        pytest.skip("set WORKER_WORLDS_LIVE_SMOKE=1 and explicitly authorize paid calls")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY is required only after live smoke is explicitly enabled")
    maximum_tokens = int(os.environ.get("WORKER_WORLDS_LIVE_MAX_TOKENS", "32"))
    maximum_cost_minor = int(os.environ.get("WORKER_WORLDS_LIVE_MAX_COST_MINOR", "5"))
    if not 1 <= maximum_tokens <= 64 or not 0 < maximum_cost_minor <= 5:
        pytest.fail("live smoke ceilings exceed the release-test maximum")
    model = os.environ.get("WORKER_WORLDS_LIVE_MODEL", "gpt-5-mini")

    async def execute() -> None:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        client = AsyncOpenAI()
        try:
            response = await asyncio.wait_for(
                client.responses.create(
                    model=model,
                    input="Return exactly the word READY. This is synthetic test data.",
                    max_output_tokens=maximum_tokens,
                ),
                timeout=30,
            )
            assert response.id and response.model
            package = "langgraph" if adapter_name == "langgraph" else "openai-agents"
            print(
                f"adapter={adapter_name} package_version={version(package)} "
                f"provider=openai model={response.model} response_id_present=true"
            )
        finally:
            await client.close()

    asyncio.run(execute())
