from pathlib import Path

import yaml


def test_composite_action_and_workflows_are_valid_local_contracts() -> None:
    action = yaml.safe_load(Path(".github/actions/worker-worlds/action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert {"scenario-path", "baseline-path", "repetitions"} <= set(action["inputs"])
    assert {"gate-result", "pass-rate-delta", "comparison-json"} <= set(action["outputs"])
    for name in ("worker-worlds-pr.yml", "worker-worlds-nightly.yml"):
        workflow = yaml.safe_load(Path(".github/workflows", name).read_text())
        assert workflow["permissions"] == {"contents": "read"}
        text = Path(".github/workflows", name).read_text()
        assert "secrets." not in text
