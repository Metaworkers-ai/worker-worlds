"""HTTP API contract and real runner integration tests."""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import worker_worlds.api as api_module
from worker_worlds.api import create_app, main
from worker_worlds.catalog import load_catalog
from worker_worlds.database import DatabaseSettings, connect
from worker_worlds.ids import prefixed_ulid


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setenv("WORKER_WORLDS_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("WORKER_WORLDS_SCENARIO_DIR", str(Path("examples/scenarios").resolve()))
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


def test_source_checkout_scenarios_precede_stale_editable_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WORKER_WORLDS_SCENARIO_DIR", raising=False)
    workspace = tmp_path / "workspace"
    expected = tuple(
        workspace / relative
        for relative in ("examples/scenarios", "scenarios/release", "scenarios/enterprise")
    )
    for root in expected:
        root.mkdir(parents=True)
    package_share = tmp_path / "venv" / "share" / "worker-worlds" / "scenarios"
    package_share.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))

    assert api_module._scenario_roots() == expected


def test_default_scenario_roots_cover_every_catalog_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_WORLDS_SCENARIO_DIR", raising=False)
    available = set(api_module._scenarios())
    catalog = load_catalog()

    missing = {
        str(scenario_id)
        for suite in catalog.suites
        for scenario_id in suite.scenario_ids
        if str(scenario_id) not in available
    }
    assert not missing


async def test_api_lists_real_scenarios_and_empty_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    scenarios = await client.get("/api/v1/scenarios")
    assert scenarios.status_code == 200
    payload = scenarios.json()
    assert payload["schema_version"] == "1.0"
    assert payload["total"] >= 1
    assert any(item["id"] == "refund.partial.happy" for item in payload["scenarios"])
    overview = (await client.get("/api/v1/overview")).json()
    assert overview["total_runs"] == 0
    assert overview["scenario_count"] == payload["total"]
    openapi = (await client.get("/openapi.json")).json()
    assert openapi["info"]["version"] == "1.0.0rc1"
    assert all(path.startswith("/api/v1/") for path in openapi["paths"])


async def test_api_runs_scenario_persists_and_returns_canonical_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = await client.post(
        "/api/v1/runs",
        json={
            "schema_version": "1.0",
            "scenario_id": "refund.partial.happy",
            "worker": "stub",
            "world": "stub",
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["scenario_id"] == "refund.partial.happy"
    assert record["cleanup_succeeded"] is True
    run_id = record["id"]
    listed = (await client.get("/api/v1/runs")).json()
    assert listed["total"] == 1
    assert listed["runs"][0]["id"] == run_id
    assert listed["runs"][0]["status"] == "pass"
    detail = await client.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200 and detail.json() == record
    overview = (await client.get("/api/v1/overview")).json()
    assert overview["total_runs"] == 1 and overview["pass_rate"] == 1


async def test_api_rejects_unknown_scenario_and_path_like_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    missing = await client.post(
        "/api/v1/runs",
        json={"schema_version": "1.0", "scenario_id": "missing", "world": "stub"},
    )
    assert missing.status_code == 404
    assert (await client.get("/api/v1/runs/..%2Fsecret")).status_code == 404


def test_contained_path_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    regular = root / "regular.json"
    regular.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    symlink = root / "linked.json"
    symlink.symlink_to(outside)

    assert api_module._resolve_contained_path(root, regular) == regular.resolve()
    assert api_module._resolve_contained_path(root, root / ".." / outside.name) is None
    assert api_module._resolve_contained_path(root, symlink) is None


async def test_api_run_evidence_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    (runs / "run_link.json").symlink_to(outside)

    assert (await client.get("/api/v1/runs/run_link")).status_code == 404
    assert (await client.get("/api/v1/runs")).json()["total"] == 0


async def test_cors_allows_dashboard_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = await client.options(
        "/api/v1/suite-jobs/suitejob_test",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


async def test_health_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)

    async def ready(_settings: object) -> tuple[bool, str]:
        return True, "Postgres ready (worker_worlds_test), migration 003"

    monkeypatch.setattr(api_module, "database_health", ready)
    payload = (await client.get("/api/v1/health")).json()
    assert payload["status"] == "ready"
    assert "postgresql://" not in str(payload)
    assert payload["artifact_directory"] == "[external-artifact-directory]"


def test_api_refuses_accidental_public_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_WORLDS_API_HOST", "0.0.0.0")
    monkeypatch.delenv("WORKER_WORLDS_ALLOW_NON_LOOPBACK_API", raising=False)
    with pytest.raises(SystemExit, match="refusing non-loopback"):
        main()


async def test_api_agents_are_registry_backed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "api-secret-canary")
    client = _client(tmp_path, monkeypatch)
    response = await client.get("/api/v1/agents")
    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["agents"]] == [
        "langgraph-project",
        "local-stub",
        "openai-project",
    ]
    assert all(item["ready"] for item in payload["agents"])
    local_stub = next(item for item in payload["agents"] if item["id"] == "local-stub")
    assert local_stub["ready"] is True
    assert local_stub["deterministic_test_infrastructure"] is True
    assert "api-secret-canary" not in response.text
    detail = await client.get("/api/v1/agents/openai-project")
    assert detail.status_code == 200
    assert detail.json()["adapter"] == "openai-agents"


async def test_api_explains_missing_agent_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-not-a-real-key")
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        "worker_worlds.agent_registry.importlib.util.find_spec",
        lambda name: None if name == "agents" else real_find_spec(name),
    )
    client = _client(tmp_path, monkeypatch)
    payload = (await client.get("/api/v1/agents/openai-project")).json()
    assert payload["ready"] is False
    assert payload["missing_requirements"] == ["Optional SDK package 'agents' is not installed"]


async def test_registered_local_stub_runs_without_provider_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)
    response = await client.post(
        "/api/v1/runs",
        json={
            "scenario_id": "refund.partial.happy",
            "agent_id": "local-stub",
            "world": "stub",
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["worker"] == "stub"
    assert record["cleanup_succeeded"] is True


async def test_api_agent_errors_are_typed_and_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    unknown = await client.post(
        "/api/v1/runs",
        json={"scenario_id": "refund.partial.happy", "agent_id": "missing", "world": "stub"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["type"] == "UnknownAgent"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unavailable = await client.post(
        "/api/v1/runs",
        json={
            "scenario_id": "refund.partial.happy",
            "agent_id": "openai-project",
            "world": "stub",
        },
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["type"] == "AgentNotReady"
    extra = await client.post(
        "/api/v1/runs",
        json={"scenario_id": "refund.partial.happy", "world": "stub", "unknown": True},
    )
    assert extra.status_code == 422


async def test_api_rejects_demonstration_scenario_for_live_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-not-a-real-key")
    client = _client(tmp_path, monkeypatch)
    response = await client.post(
        "/api/v1/runs",
        json={
            "scenario_id": "refund.partial.happy",
            "agent_id": "openai-project",
            "world": "stub",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "type": "ScenarioNotLiveReady",
        "message": "selected scenario is not approved for live adapters",
        "scenario_count": 1,
    }


async def test_catalog_endpoints_are_versioned_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    first = await client.get("/api/v1/catalog")
    second = await client.get("/api/v1/catalog")
    assert first.status_code == 200
    assert first.content == second.content
    payload = first.json()
    assert payload["catalog_version"] == "1.0.0"
    assert payload["domains"][0]["id"] == "commerce"
    roles = await client.get("/api/v1/domains/commerce/roles")
    assert len(roles.json()) == 7
    suites = await client.get("/api/v1/roles/refund-specialist/suites")
    assert {item["tier"] for item in suites.json()} == {"smoke", "standard", "full", "custom"}
    assert (await client.get("/api/v1/domains/missing/roles")).status_code == 404


async def test_context_selection_is_validated_and_persisted_as_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKER_WORLDS_SCENARIO_DIR", str(Path("scenarios/release").resolve()))  # noqa: ASYNC240
    payload = {
        "scenario_id": "commerce.refunds-payments.001",
        "worker": "stub",
        "world": "stub",
        "domain_id": "commerce",
        "role_id": "refund-specialist",
        "suite_id": "commerce.refund-specialist.smoke",
    }
    response = await client.post("/api/v1/runs", json=payload)
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    manifest = tmp_path / "artifacts" / "contexts" / f"{run_id}.json"
    assert manifest.is_file()
    assert '"role_id":"refund-specialist"' in manifest.read_text(encoding="utf-8")
    mismatch = await client.post(
        "/api/v1/runs", json={**payload, "role_id": "inventory-controller"}
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["type"] == "IncompatibleEvaluationSelection"
    world_mismatch = await client.post("/api/v1/runs", json={**payload, "world": "insurance"})
    assert world_mismatch.status_code == 409
    assert world_mismatch.json()["detail"]["type"] == "IncompatibleWorldSelection"


async def test_custom_suite_api_runs_and_downloads_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = os.environ.get("WORKER_WORLDS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_WORLDS_TEST_DATABASE_URL is not explicitly set")
    monkeypatch.setenv("WORKER_WORLDS_DATABASE_URL", url)
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKER_WORLDS_SCENARIO_DIR", str(Path("scenarios/release").resolve()))  # noqa: ASYNC240
    response = await client.post(
        "/api/v1/suite-jobs",
        json={
            "request_key": prefixed_ulid("request"),
            "domain_id": "commerce",
            "role_id": "refund-specialist",
            "suite_id": "commerce.refund-specialist.custom",
            "agent_id": "local-stub",
            "world": "stub",
            "concurrency": 1,
            "scenario_ids": ["commerce.refunds-payments.001"],
            "seed": 3001,
            "budget": {
                "deadline_s": 30,
                "scenarios": 1,
                "tool_calls": 10,
                "model_tokens": 1000,
                "mutations": 10,
                "cost_minor": 0,
            },
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["configuration"]["seed_override"] == 3001
    assert response.json()["configuration"]["suite_budget"]["scenarios"] == 1
    job_id = response.json()["id"]
    try:
        for _ in range(100):
            detail = await client.get(f"/api/v1/suite-jobs/{job_id}")
            if (
                detail.json()["status"] in {"completed", "failed", "cancelled"}
                and detail.json()["suite_record_path"]
            ):
                break
            await asyncio.sleep(0.01)
        assert detail.json()["status"] == "completed"
        evidence = await client.get(f"/api/v1/suite-jobs/{job_id}/evidence")
        assert evidence.status_code == 200
        assert evidence.headers["content-type"] == "application/zip"
    finally:
        connection = await connect(DatabaseSettings(url=url))
        try:
            await connection.execute("DELETE FROM worker_worlds.suite_jobs WHERE id=$1", job_id)
        finally:
            await connection.close()
