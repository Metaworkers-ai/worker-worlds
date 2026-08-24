"""Framework-neutral world construction by stable public identity."""

from worker_worlds.contracts import Scenario
from worker_worlds.database import DatabaseSettings
from worker_worlds.insurance import InsuranceWorld
from worker_worlds.postgres_world import PostgresWorld
from worker_worlds.protocols import World
from worker_worlds.stubs import StubWorld
from worker_worlds.supply_chain import SupplyChainWorld

WORLD_NAMES = ("stub", "postgres", "supply-chain", "insurance")


def world_version(name: str) -> str:
    """Return the implementation version without creating runtime resources."""
    versions = {
        "stub": StubWorld.version,
        "postgres": PostgresWorld.version,
        "supply-chain": SupplyChainWorld.version,
        "insurance": InsuranceWorld.version,
    }
    try:
        return versions[name]
    except KeyError as exc:
        raise ValueError(f"unknown world: {name}") from exc


def create_world(name: str, scenario: Scenario, settings: DatabaseSettings | None = None) -> World:
    """Create a world without exposing domain-specific types to the runner."""
    if name == "stub":
        return StubWorld()
    selected = settings or DatabaseSettings.from_env()
    if name == "postgres":
        return PostgresWorld(selected, str(scenario.id))
    if name == "supply-chain":
        return SupplyChainWorld(selected, str(scenario.id))
    if name == "insurance":
        return InsuranceWorld(selected, str(scenario.id))
    raise ValueError(f"unknown world: {name}")
