from pathlib import Path

from worker_worlds.scenario_release import (
    export_scenarios,
    scenario_yaml,
    validate_scenario_directory,
)
from worker_worlds.scenarios import load_scenario


def test_release_scenarios_are_current_and_independently_valid() -> None:
    directory = Path("scenarios/release")
    count, drift = export_scenarios(directory, check=True)
    assert count == 200
    assert drift == []
    scenarios = validate_scenario_directory(directory)
    assert len(scenarios) == 200
    assert all(item.metadata["review_status"] == "pending_domain_review" for item in scenarios)


def test_export_is_byte_deterministic(tmp_path: Path) -> None:
    export_scenarios(tmp_path)
    first = {path.name: path.read_bytes() for path in tmp_path.glob("*.yaml")}
    export_scenarios(tmp_path)
    second = {path.name: path.read_bytes() for path in tmp_path.glob("*.yaml")}
    assert first == second
    sample = load_scenario(next(iter(sorted(tmp_path.glob("*.yaml")))))
    assert scenario_yaml(sample).startswith("schema_version: '1.0'")
