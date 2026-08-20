"""HTTP API contract and real runner integration tests."""

import importlib.util
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import worker_worlds.api as api_module
from worker_worlds.api import create_app


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setenv("WORKER_WORLDS_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("WORKER_WORLDS_SCENARIO_DIR", str(Path("examples/scenarios").resolve()))
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


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


async def test_health_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)

    async def ready(_settings: object) -> tuple[bool, str]:
        return True, "Postgres ready (worker_worlds_test), migration 003"

    monkeypatch.setattr(api_module, "database_health", ready)
    payload = (await client.get("/api/v1/health")).json()
    assert payload["status"] == "ready"
    assert "postgresql://" not in str(payload)
    assert payload["artifact_directory"] == "[external-artifact-directory]"


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
