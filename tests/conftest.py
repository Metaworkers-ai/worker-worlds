"""Shared contract fixtures."""

from pathlib import Path

import pytest

from worker_worlds.contracts import Scenario
from worker_worlds.scenarios import load_scenario


@pytest.fixture
def happy_scenario() -> Scenario:
    return load_scenario(Path("tests/fixtures/successful_partial_refund.yaml"))
