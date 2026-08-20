from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from worker_worlds import AgentModelMetadata
from worker_worlds.agent_registry import (
    AgentDefinition,
    AgentFactoryContext,
    AgentRegistry,
    load_agent_factory,
)
from worker_worlds.config import WorkerWorldsConfig, load_config
from worker_worlds.protocols import WorkerAdapter


def definition(**overrides: object) -> AgentDefinition:
    values: dict[str, object] = {
        "id": "support-agent",
        "version": "1.2.0",
        "adapter": "stub",
        "required_env": ["OPENAI_API_KEY", "TRACE_ID"],
        "model": {"provider": "openai", "name": "gpt-5-mini"},
    }
    values.update(overrides)
    return AgentDefinition.model_validate(values)


def test_valid_definition_is_strict_and_canonical() -> None:
    agent = definition(required_env=["TRACE_ID", "OPENAI_API_KEY"])
    assert agent.required_env == ("OPENAI_API_KEY", "TRACE_ID")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        definition(secret="do-not-persist")
    with pytest.raises(ValidationError, match="String should match pattern"):
        definition(factory="package.factory")
    with pytest.raises(ValidationError, match="String should match pattern"):
        definition(required_env=["api-key"])
    with pytest.raises(ValidationError, match="String should match pattern"):
        definition(version="latest")
    with pytest.raises(ValidationError, match="String should match pattern"):
        definition(model={"provider": "openai", "name": "gpt-5", "version": "preview"})


def test_public_schema_matches_runtime_constraints() -> None:
    schema = AgentDefinition.model_json_schema()
    properties = schema["properties"]
    assert properties["id"]["pattern"].startswith("^")
    assert properties["version"]["pattern"].startswith("^")
    assert properties["factory"]["anyOf"][0]["pattern"].startswith("^")
    assert properties["required_env"]["items"]["pattern"].startswith("^")
    assert properties["required_env"]["uniqueItems"] is True
    assert AgentModelMetadata.__name__ == "AgentModelMetadata"


def test_registry_schemas_reject_unmatched_mapping_keys() -> None:
    for model in (AgentRegistry, WorkerWorldsConfig):
        agents_schema = model.model_json_schema()["properties"]["agents"]
        assert agents_schema["additionalProperties"] is False
        assert list(agents_schema["patternProperties"]) == ["^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$"]


def test_non_stub_adapter_requires_explicit_factory() -> None:
    with pytest.raises(ValidationError, match="require a factory"):
        definition(adapter="langgraph")


def test_registry_validates_ids_and_key_identity() -> None:
    agent = definition()
    registry = AgentRegistry.model_validate({agent.id: agent})
    assert registry.get("support-agent") == agent
    with pytest.raises(ValidationError, match="does not match id"):
        AgentRegistry.model_validate({"different": agent})
    with pytest.raises(ValueError, match="unknown agent"):
        registry.get("missing")


def test_validated_agent_mappings_are_immutable_and_copied() -> None:
    agent = definition(required_env=[])
    source = {agent.id: agent}
    registry = AgentRegistry.model_validate(source)
    config = WorkerWorldsConfig(agents=source)
    source.clear()
    assert registry.get(agent.id) == agent
    assert config.agents[agent.id] == agent
    with pytest.raises(TypeError):
        registry.agents["other"] = agent  # type: ignore[index]
    with pytest.raises(TypeError):
        config.agents["other"] = agent  # type: ignore[index]


def test_agent_id_named_agents_is_not_confused_with_registry_wrapper() -> None:
    agent = definition(id="agents", required_env=[])
    registry = AgentRegistry.model_validate({"agents": agent})
    assert registry.get("agents") == agent


def test_duplicate_yaml_agent_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "worker-worlds.yaml"
    path.write_text(
        "agents:\n"
        "  duplicate: {id: duplicate, version: '1.0', adapter: stub}\n"
        "  duplicate: {id: duplicate, version: '2.0', adapter: stub}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate YAML mapping key 'duplicate'"):
        load_config(path)


def test_legacy_config_loads_without_agents(tmp_path: Path) -> None:
    path = tmp_path / "worker-worlds.yaml"
    path.write_text("schema_version: '1.0'\nexecution: {worker: stub}\n", encoding="utf-8")
    config, selected = load_config(path)
    assert selected == path
    assert not config.agents


def test_registry_order_does_not_affect_serialization_or_hash() -> None:
    first = definition(id="first", required_env=[])
    second = definition(id="second", required_env=[])
    left = WorkerWorldsConfig(agents={"second": second, "first": first})
    right = WorkerWorldsConfig(agents={"first": first, "second": second})
    assert left.configuration_hash() == right.configuration_hash()
    assert list(left.agent_registry().model_dump(mode="json")["agents"]) == ["first", "second"]


def test_empty_agents_preserve_legacy_hash_and_redacted_shape() -> None:
    config = WorkerWorldsConfig()
    legacy_data = config.model_dump(mode="json", exclude={"agents"})
    assert "agents" not in config.redacted()
    import hashlib
    import json

    legacy_hash = hashlib.sha256(
        json.dumps(legacy_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert config.configuration_hash() == legacy_hash


def test_readiness_and_context_diagnostics_never_emit_values() -> None:
    agent = definition()
    registry = AgentRegistry.model_validate({agent.id: agent})
    readiness = registry.readiness(agent.id, {"OPENAI_API_KEY": "super-secret"})
    assert not readiness.ready
    assert readiness.missing_env == ("TRACE_ID",)
    assert readiness.missing_requirements == ("Environment variable TRACE_ID is not set",)
    assert "super-secret" not in readiness.model_dump_json()
    context = AgentFactoryContext(definition=agent, environment={"OPENAI_API_KEY": "super-secret"})
    assert "super-secret" not in repr(context)
    assert "super-secret" not in context.model_dump_json()


def test_readiness_explains_missing_packages_and_broken_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = definition(
        adapter="openai-agents",
        factory="worker_worlds.example_factories:create_openai_agent",
        required_env=[],
    )
    registry = AgentRegistry.model_validate({agent.id: agent})
    monkeypatch.setattr("worker_worlds.agent_registry.importlib.util.find_spec", lambda _name: None)
    package = registry.readiness(agent.id, {})
    assert package.missing_requirements == ("Optional SDK package 'agents' is not installed",)

    monkeypatch.setattr(
        "worker_worlds.agent_registry.importlib.util.find_spec", lambda _name: object()
    )

    def broken_factory(_path: str) -> object:
        raise RuntimeError("secret-canary")

    monkeypatch.setattr("worker_worlds.agent_registry.load_agent_factory", broken_factory)
    factory = registry.readiness(agent.id, {})
    assert factory.missing_requirements == ("Configured agent factory is unavailable",)
    assert "secret-canary" not in factory.model_dump_json()


def test_packaged_project_factories_are_ready_without_provider_call() -> None:
    config, _ = load_config(Path("worker-worlds.yaml"))
    registry = config.agent_registry()
    for agent_id in ("openai-project", "langgraph-project"):
        readiness = registry.readiness(agent_id, {"OPENAI_API_KEY": "fake-not-a-real-key"})
        assert readiness.ready
        assert readiness.package_ready
        assert readiness.factory_ready


def test_console_entrypoint_reports_both_factories_ready() -> None:
    project = Path(__file__).parents[1]
    executable = project / ".venv" / "bin" / "worker-worlds"
    environment = {**os.environ, "OPENAI_API_KEY": "fake-not-a-real-key"}
    for agent_id in ("openai-project", "langgraph-project"):
        result = subprocess.run(
            [str(executable), "agents", "show", agent_id, "--json"],
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        readiness = json.loads(result.stdout)["agent"]
        assert readiness["factory_ready"] is True
        assert readiness["ready"] is True


def test_fresh_wheel_can_import_both_registered_factory_paths(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse)],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    environment = tmp_path / "environment"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(environment)],
        check=True,
    )
    python = environment / "bin" / "python"
    wheel = next(wheelhouse.glob("*.whl"))
    subprocess.run(
        [str(python), "-m", "pip", "install", str(wheel)],
        text=True,
        capture_output=True,
        check=True,
    )
    script = (
        "from worker_worlds.agent_registry import load_agent_factory; "
        "load_agent_factory('worker_worlds.example_factories:create_openai_agent'); "
        "load_agent_factory('worker_worlds.example_factories:create_langgraph_agent')"
    )
    result = subprocess.run(
        [str(python), "-I", "-c", script],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_factory_loading_failures_are_clear() -> None:
    with pytest.raises(ValueError, match="could not import") as missing_module:
        load_agent_factory("definitely_missing_worker_worlds_module:create")
    assert missing_module.value.__cause__ is None
    assert missing_module.value.__context__ is None
    with pytest.raises(ValueError, match="does not exist"):
        load_agent_factory("worker_worlds.stubs:missing")
    with pytest.raises(TypeError, match="not callable"):
        load_agent_factory("worker_worlds.stubs:UTC")


def test_import_failure_diagnostic_does_not_leak_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "import-time-secret-sentinel"
    monkeypatch.setenv("AGENT_IMPORT_SECRET", secret)
    module = tmp_path / "unsafe_import_factory.py"
    module.write_text(
        "import os\nraise RuntimeError(os.environ['AGENT_IMPORT_SECRET'])\n", encoding="utf-8"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="failed during import") as error:
            load_agent_factory("unsafe_import_factory:create")
        rendered = "".join(
            traceback.format_exception(type(error.value), error.value, error.value.__traceback__)
        )
        assert secret not in str(error.value)
        assert secret not in rendered
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("unsafe_import_factory", None)


@pytest.mark.asyncio
async def test_sync_and_async_factories_and_return_boundary(tmp_path: Path) -> None:
    module = tmp_path / "agent_factories.py"
    module.write_text(
        "from worker_worlds.stubs import StubWorkerAdapter\n"
        "def sync_factory(context): return StubWorkerAdapter()\n"
        "async def async_factory(context): return StubWorkerAdapter()\n"
        "def invalid_factory(context): return object()\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        for name in ("sync_factory", "async_factory"):
            agent = definition(required_env=[], factory=f"agent_factories:{name}")
            adapter = await AgentRegistry.model_validate({agent.id: agent}).create(agent.id, {})
            assert isinstance(adapter, WorkerAdapter)
        invalid = definition(required_env=[], factory="agent_factories:invalid_factory")
        with pytest.raises(TypeError, match="expected WorkerAdapter"):
            await AgentRegistry.model_validate({invalid.id: invalid}).create(invalid.id, {})
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("agent_factories", None)


@pytest.mark.asyncio
async def test_factory_exception_diagnostic_does_not_leak_secret(tmp_path: Path) -> None:
    module = tmp_path / "failing_agent_factory.py"
    module.write_text(
        "def create(context): raise RuntimeError(context.environment['API_TOKEN'])\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        agent = definition(required_env=["API_TOKEN"], factory="failing_agent_factory:create")
        registry = AgentRegistry.model_validate({agent.id: agent})
        with pytest.raises(RuntimeError, match="factory for agent 'support-agent' failed") as error:
            await registry.create(agent.id, {"API_TOKEN": "secret-value"})
        assert "secret-value" not in str(error.value)
        assert error.value.__cause__ is None
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("failing_agent_factory", None)
