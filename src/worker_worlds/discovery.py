"""Shared deterministic scenario discovery and identifier resolution."""

from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path

from worker_worlds.config import WorkerWorldsConfig
from worker_worlds.contracts import Scenario
from worker_worlds.errors import WorkerWorldsError
from worker_worlds.scenarios import load_scenario


def discover_scenarios(config: WorkerWorldsConfig) -> dict[str, tuple[Scenario, Path]]:
    """Load configured scenario locations into a stable ID-keyed mapping."""
    discovered: dict[str, tuple[Scenario, Path]] = {}
    for location in config.execution.scenario_locations:
        root = Path(location)
        paths = sorted(root.rglob("*.yaml")) if root.is_dir() else ([root] if root.exists() else [])
        for path in paths:
            scenario = load_scenario(path)
            identifier = str(scenario.id)
            if identifier in discovered:
                previous = discovered[identifier][1]
                raise WorkerWorldsError(
                    f"duplicate scenario id {identifier!r}: {previous} and {path}"
                )
            discovered[identifier] = (scenario, path)
    return dict(sorted(discovered.items()))


def resolve_scenario(config: WorkerWorldsConfig, identifier: str) -> tuple[Scenario, Path]:
    """Resolve an exact configured scenario ID with useful suggestions."""
    scenarios = discover_scenarios(config)
    try:
        return scenarios[identifier]
    except KeyError:
        suggestions = get_close_matches(identifier, scenarios, n=3, cutoff=0.35)
        suffix = f"; did you mean: {', '.join(suggestions)}" if suggestions else ""
        raise WorkerWorldsError(f"unknown scenario id {identifier!r}{suffix}") from None
