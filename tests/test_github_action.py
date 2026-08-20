from pathlib import Path

import yaml


def test_composite_action_and_workflows_are_valid_local_contracts() -> None:
    action = yaml.safe_load(Path(".github/actions/worker-worlds/action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert {"scenario-path", "baseline-path", "repetitions"} <= set(action["inputs"])
    assert {"gate-result", "pass-rate-delta", "comparison-json"} <= set(action["outputs"])
    for name in (
        "worker-worlds-pr.yml",
        "worker-worlds-nightly.yml",
        "worker-worlds-release.yml",
    ):
        workflow = yaml.safe_load(Path(".github/workflows", name).read_text())
        assert workflow["permissions"] == {"contents": "read"}
        text = Path(".github/workflows", name).read_text()
        assert "secrets." not in text
        assert "permissions: write" not in text
    action_text = Path(".github/actions/worker-worlds/action.yml").read_text()
    assert "set -euo pipefail" in action_text
    assert "${{ secrets." not in action_text
    assert Path("scripts/build_docs.py").exists()
    assert Path("scripts/release_artifacts.py").exists()


def test_nightly_uses_the_project_virtualenv_and_full_dashboard_gate() -> None:
    nightly = Path(".github/workflows/worker-worlds-nightly.yml").read_text()
    makefile = Path("Makefile").read_text()

    assert ".venv/bin/worker-worlds suite" in nightly
    assert ".venv/bin/pytest " in nightly
    assert "\n      - run: worker-worlds " not in nightly
    assert "\n      - run: pytest " not in nightly
    assert (
        "verify: lint typecheck schemas-check scenarios-check test docs dashboard-verify build"
        in makefile
    )
    assert "npm run lint" in makefile
    assert "npx tsc --noEmit" in makefile
    assert "npm run test:e2e" in makefile
    assert "npm run build" in makefile


def test_pr_workflow_pins_node_for_dashboard_verification() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/worker-worlds-pr.yml").read_text())
    steps = workflow["jobs"]["worker-worlds"]["steps"]
    setup_node = next(step for step in steps if step.get("uses") == "actions/setup-node@v4")
    assert setup_node["with"]["node-version"] == "22"
    assert setup_node["with"]["cache-dependency-path"] == "apps/dashboard/package-lock.json"


def test_release_cold_installs_both_framework_extras() -> None:
    release = Path(".github/workflows/worker-worlds-release.yml").read_text()

    assert "[openai-agents,langgraph]" in release
    assert "./scripts/cold_install_acceptance.sh" in release
    assert "cold-install:" in release
    assert release.count("actions/setup-node@v4") == 2
