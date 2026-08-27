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
    assert "127.0.0.1:55432/worker_worlds_dev" in nightly
    assert "127.0.0.1:55432/worker_worlds_test" not in nightly
    assert "\n      - run: worker-worlds " not in nightly
    assert "\n      - run: pytest " not in nightly
    verify_target = (
        "verify: lint typecheck schemas-check openapi-check catalog-check scenarios-check "
        "test docs dashboard-verify build"
    )
    assert verify_target in makefile
    assert "npm run lint" in makefile
    assert "npx tsc --noEmit" in makefile
    assert "npm run test:e2e" in makefile
    assert "npm run build" in makefile
    assert "include-hidden-files: true" in nightly


def test_pr_workflow_pins_node_for_dashboard_verification() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/worker-worlds-pr.yml").read_text())
    steps = workflow["jobs"]["worker-worlds"]["steps"]
    setup_node = next(step for step in steps if step.get("uses") == "actions/setup-node@v4")
    smoke = next(step for step in steps if step.get("uses") == "./.github/actions/worker-worlds")
    assert setup_node["with"]["node-version"] == "22"
    assert setup_node["with"]["cache-dependency-path"] == "apps/dashboard/package-lock.json"
    assert smoke["with"]["scenario-path"] == "examples/scenarios/refund_happy.yaml"


def test_release_cold_installs_both_framework_extras() -> None:
    release = Path(".github/workflows/worker-worlds-release.yml").read_text()

    assert "[openai-agents,langgraph]" in release
    assert "./scripts/cold_install_acceptance.sh" in release
    assert "cold-install:" in release
    assert release.count("actions/setup-node@v4") == 2


def test_live_smoke_workflow_is_credential_gated_bounded_and_never_leaks_a_secret() -> None:
    """#20/#9 contract: the live-smoke workflow can never fire or leak by accident."""
    path = Path(".github/workflows/worker-worlds-live-smoke.yml")
    text = path.read_text()
    workflow = yaml.safe_load(text)

    # Triggered only by an explicit, manual, human action -- never push/pull_request/schedule.
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"workflow_dispatch": None}

    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    # Bounded execution time so a hung provider call can never hang CI indefinitely.
    assert isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] > 0

    steps = job["steps"]
    live_step = next(step for step in steps if "pytest -m live tests/live" in step.get("run", ""))
    env = live_step["env"]

    # The provider call is explicitly opt-in, not implicit.
    assert env["WORKER_WORLDS_LIVE_SMOKE"] == "1"
    # The credential is sourced only from GitHub's own secret store, never hardcoded, and
    # never echoed anywhere in the workflow text.
    assert env["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    assert "secrets.OPENAI_API_KEY" in text
    for line in text.splitlines():
        assert not (line.strip().startswith("echo") and "OPENAI_API_KEY" in line)

    # Bounded token/cost/retry ceilings travel with the credential, matching the guarded test's
    # own contract in tests/live/test_live_adapters.py.
    for required_env in (
        "WORKER_WORLDS_LIVE_MODEL",
        "WORKER_WORLDS_LIVE_MAX_TOKENS",
        "WORKER_WORLDS_LIVE_MAX_COST_MINOR",
        "WORKER_WORLDS_LIVE_MAX_RETRIES",
    ):
        assert required_env in env

    # The ceiling *values* themselves must stay within the release-test maximum enforced by
    # tests/live/test_live_adapters.py::_live_ceilings() -- not just be present. A workflow that
    # widened these values would silently defeat the point of having a ceiling at all.
    assert 1 <= int(env["WORKER_WORLDS_LIVE_MAX_TOKENS"]) <= 64
    assert 0 < int(env["WORKER_WORLDS_LIVE_MAX_COST_MINOR"]) <= 5
    assert 0 <= int(env["WORKER_WORLDS_LIVE_MAX_RETRIES"]) <= 2

    # Both real adapters remain covered by whatever this workflow invokes -- verified statically,
    # without requiring a real provider call for this contract test itself.
    live_test_text = Path("tests/live/test_live_adapters.py").read_text()
    assert '["langgraph", "openai-agents"]' in live_test_text
    assert "def test_optional_live_provider_smoke" in live_test_text
    # No mock/fake provider is substituted in the guarded live path.
    assert "ScriptedModel" not in live_test_text
    assert "ToolAwareFakeChatModel" not in live_test_text
    assert "from agents import" in live_test_text
    assert "from langchain_openai import ChatOpenAI" in live_test_text
