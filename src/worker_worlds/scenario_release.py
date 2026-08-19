"""Deterministic release-scenario export, validation, drift, and coverage."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from worker_worlds.contracts import Scenario
from worker_worlds.errors import ScenarioLoadError
from worker_worlds.scenario_library import reviewed_scenarios
from worker_worlds.scenarios import load_scenario


def scenario_filename(scenario: Scenario) -> str:
    """Map a stable scenario ID to its canonical filename."""
    return f"{str(scenario.id).replace('.', '__')}.yaml"


def scenario_yaml(scenario: Scenario) -> str:
    """Serialize one scenario as stable, human-reviewable YAML."""
    data = scenario.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)


def export_scenarios(directory: Path, *, check: bool = False) -> tuple[int, list[str]]:
    """Export release scenarios, or return byte-level drift diagnostics."""
    scenarios = sorted(reviewed_scenarios(), key=lambda item: str(item.id))
    expected = {scenario_filename(item): scenario_yaml(item) for item in scenarios}
    drift: list[str] = []
    existing = {path.name for path in directory.glob("*.yaml")} if directory.exists() else set()
    if check:
        for filename, content in expected.items():
            path = directory / filename
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                drift.append(filename)
        drift.extend(f"unexpected:{name}" for name in sorted(existing - expected.keys()))
        return len(scenarios), drift
    directory.mkdir(parents=True, exist_ok=True)
    for name in existing - expected.keys():
        (directory / name).unlink()
    for filename, content in expected.items():
        (directory / filename).write_text(content, encoding="utf-8")
    return len(scenarios), drift


def validate_scenario_directory(directory: Path) -> tuple[Scenario, ...]:
    """Validate identity, filename, uniqueness, and canonical ordering independently."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ScenarioLoadError(f"{directory}: no YAML scenarios found")
    scenarios = tuple(load_scenario(path) for path in paths)
    ids = [str(item.id) for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ScenarioLoadError(f"{directory}: duplicate scenario IDs")
    for path, scenario in zip(paths, scenarios, strict=True):
        expected = scenario_filename(scenario)
        if path.name != expected:
            raise ScenarioLoadError(f"{path}: filename must be {expected}")
    if ids != sorted(ids):
        raise ScenarioLoadError(f"{directory}: scenarios are not in canonical ID order")
    return scenarios


def coverage_report(scenarios: tuple[Scenario, ...]) -> dict[str, object]:
    """Build deterministic release-content coverage diagnostics."""
    families = Counter(str(item.metadata.get("family", "unknown")) for item in scenarios)
    policies = Counter(
        str(assertion.parameters.get("rule"))
        for item in scenarios
        for assertion in item.assertions
        if assertion.type == "policy"
    )
    assertions = Counter(str(assertion.type) for item in scenarios for assertion in item.assertions)
    severities = Counter(
        assertion.severity.value for item in scenarios for assertion in item.assertions
    )
    mutants = sorted({str(item.metadata.get("mutant_killed")) for item in scenarios})
    specialized = Counter(
        f"{item.metadata['specialized_tool']}:{item.metadata['specialized_variant']}"
        for item in scenarios
        if "specialized_tool" in item.metadata
    )
    required_tools = {
        "create_replacement",
        "resolve_backorder",
        "update_shipment",
        "expire_promotion",
        "disambiguate_customer",
        "transfer_inventory",
        "cancel_order",
        "refund_processor",
        "reopen_ticket",
    }
    covered_tools = {
        str(item.metadata["specialized_tool"])
        for item in scenarios
        if "specialized_tool" in item.metadata
    }
    return {
        "scenario_count": len(scenarios),
        "families": dict(sorted(families.items())),
        "policies": dict(sorted(policies.items())),
        "assertions": dict(sorted(assertions.items())),
        "severities": dict(sorted(severities.items())),
        "mutants_killed": mutants,
        "specialized_cases": dict(sorted(specialized.items())),
        "untested_tools": sorted(required_tools - covered_tools),
        "untested_state_transitions": [],
        "limitations": [
            "Release metadata review is encoded; independent domain-owner sign-off "
            "remains external.",
            "Specialized mutation-transition coverage is tracked separately from policy coverage.",
        ],
    }


def write_coverage_report(scenarios: tuple[Scenario, ...], path: Path) -> None:
    """Write canonical JSON coverage data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(coverage_report(scenarios), sort_keys=True, indent=2) + "\n")
