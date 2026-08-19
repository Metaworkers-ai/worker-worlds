"""HTTP API contract and real runner integration tests."""

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
