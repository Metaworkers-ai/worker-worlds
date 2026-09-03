"""Versioned, credential-free agent definitions and lazy factory loading."""

from __future__ import annotations

import importlib.util
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from difflib import get_close_matches
from enum import StrEnum
from importlib import import_module
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from worker_worlds.protocols import WorkerAdapter

ID_PATTERN = r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$"
VERSION_PATTERN = r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][0-9A-Za-z.-]+)?$"
ENV_PATTERN = r"^[A-Z_][A-Z0-9_]*$"
FACTORY_PATTERN = (
    r"^(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):"
    r"(?P<callable>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)$"
)

_ID_PATTERN = re.compile(ID_PATTERN)
_VERSION_PATTERN = re.compile(VERSION_PATTERN)
_ENV_PATTERN = re.compile(ENV_PATTERN)
_FACTORY_PATTERN = re.compile(FACTORY_PATTERN)

AgentId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
AgentVersion = Annotated[str, StringConstraints(pattern=VERSION_PATTERN)]
EnvironmentName = Annotated[str, StringConstraints(pattern=ENV_PATTERN)]
FactoryPath = Annotated[str, StringConstraints(pattern=FACTORY_PATTERN)]


class RegistryModel(BaseModel):
    """Strict immutable registry model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentAdapterType(StrEnum):
    """Framework boundaries supported by schema version one."""

    STUB = "stub"
    OPENAI_AGENTS = "openai-agents"
    LANGGRAPH = "langgraph"


class AgentModelMetadata(RegistryModel):
    """Non-secret model identity used for reproducibility."""

    provider: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]*$")
    version: AgentVersion | None = None


class AgentDefinition(RegistryModel):
    """A selectable agent without credentials or imported SDK objects."""

    schema_version: Literal["1.0"] = "1.0"
    id: AgentId
    version: AgentVersion
    adapter: AgentAdapterType
    factory: FactoryPath | None = None
    required_env: tuple[EnvironmentName, ...] = Field(
        default=(), json_schema_extra={"uniqueItems": True}
    )
    model: AgentModelMetadata | None = None
    supported_domain_ids: tuple[str, ...] = ("commerce", "insurance", "marketing")

    @field_validator("supported_domain_ids")
    @classmethod
    def validate_supported_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require a non-empty stable set of declared business-domain identifiers."""
        if not value or any(not _ID_PATTERN.fullmatch(item) for item in value):
            raise ValueError("supported domain ids must be stable catalog identifiers")
        if len(value) != len(set(value)):
            raise ValueError("supported domain ids must be unique")
        return tuple(sorted(value))

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """Require stable, shell- and URL-friendly identifiers."""
        if not _ID_PATTERN.fullmatch(value):
            raise ValueError(
                "agent id must be a lowercase dotted, dashed, or underscored identifier"
            )
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        """Require an explicit semantic-style version."""
        if not _VERSION_PATTERN.fullmatch(value):
            raise ValueError("agent version must be numeric major.minor or major.minor.patch")
        return value

    @field_validator("factory")
    @classmethod
    def validate_factory(cls, value: str | None) -> str | None:
        """Accept only explicit importable module:callable references."""
        if value is not None and not _FACTORY_PATTERN.fullmatch(value):
            raise ValueError("factory must be an explicit module:callable path")
        return value

    @field_validator("required_env")
    @classmethod
    def validate_required_env(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Store unique environment variable names in canonical order."""
        invalid = [name for name in value if not _ENV_PATTERN.fullmatch(name)]
        if invalid:
            raise ValueError("required_env entries must be uppercase environment variable names")
        if len(set(value)) != len(value):
            raise ValueError("required_env entries must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def require_native_factory(self) -> AgentDefinition:
        """Native SDK adapters always require explicit user-owned construction."""
        if self.adapter is not AgentAdapterType.STUB and self.factory is None:
            raise ValueError(f"{self.adapter.value} agents require a factory")
        return self


class AgentReadiness(RegistryModel):
    """Credential-safe readiness result."""

    id: str
    adapter: AgentAdapterType
    version: str
    ready: bool
    missing_env: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    package_ready: bool = True
    factory_ready: bool = True
    database_ready: bool = True
    database: str = "not checked"
    diagnostic: str


class AgentFactoryContext(RegistryModel):
    """Runtime factory input; its representation never includes environment values."""

    definition: AgentDefinition
    environment: Mapping[str, str] = Field(default_factory=dict, exclude=True, repr=False)

    @field_validator("environment")
    @classmethod
    def copy_environment(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        """Prevent mutation of runtime credentials after context construction."""
        return MappingProxyType(dict(value))

    def __repr__(self) -> str:
        """Return a safe diagnostic representation containing names only."""
        names = sorted(self.environment)
        return f"AgentFactoryContext(definition={self.definition!r}, environment_names={names!r})"


class AgentRegistry(RegistryModel):
    """Canonical collection of uniquely identified agents."""

    agents: Mapping[AgentId, AgentDefinition] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )

    @model_validator(mode="before")
    @classmethod
    def accept_agents_mapping(cls, value: object) -> object:
        """Allow the top-level config's natural ID-to-definition mapping shape."""
        if isinstance(value, Mapping):
            # A one-entry natural registry may itself contain an agent literally named "agents".
            inner = value.get("agents")
            is_wrapper = (
                set(value) == {"agents"}
                and isinstance(inner, Mapping)
                and not {"id", "version", "adapter"}.issubset(inner)
            )
            if not is_wrapper:
                return {"agents": value}
        return value

    @field_validator("agents")
    @classmethod
    def freeze_agents(cls, value: Mapping[str, AgentDefinition]) -> Mapping[str, AgentDefinition]:
        """Copy and freeze the mapping so validated invariants cannot later be bypassed."""
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_keys(self) -> AgentRegistry:
        """Ensure mapping keys and embedded IDs agree and IDs remain unique."""
        for key, definition in self.agents.items():
            if not _ID_PATTERN.fullmatch(key):
                raise ValueError(f"invalid agent registry key {key!r}")
            if key != definition.id:
                raise ValueError(f"agent registry key {key!r} does not match id {definition.id!r}")
        return self

    @field_serializer("agents")
    def serialize_agents(self, value: Mapping[str, AgentDefinition]) -> dict[str, AgentDefinition]:
        """Emit a canonical mapping independent of source order."""
        return {key: value[key] for key in sorted(value)}

    def readiness(
        self,
        agent_id: str,
        environment: Mapping[str, str],
        *,
        database_ready: bool = True,
        database: str = "not checked",
    ) -> AgentReadiness:
        """Inspect environment, SDK, factory, and optional database readiness safely."""
        definition = self.get(agent_id)
        missing = tuple(name for name in definition.required_env if not environment.get(name))
        package = {
            AgentAdapterType.OPENAI_AGENTS: "agents",
            AgentAdapterType.LANGGRAPH: "langgraph",
            AgentAdapterType.STUB: None,
        }[definition.adapter]
        package_ready = package is None or importlib.util.find_spec(package) is not None
        factory_ready = True
        try:
            if definition.factory is not None:
                load_agent_factory(definition.factory)
        except (TypeError, ValueError, RuntimeError):
            factory_ready = False
        missing_requirements = tuple(
            [f"Environment variable {name} is not set" for name in missing]
            + ([f"Optional SDK package {package!r} is not installed"] if not package_ready else [])
            + (["Configured agent factory is unavailable"] if not factory_ready else [])
            + (["Database is unavailable"] if not database_ready else [])
        )
        ready = not missing and package_ready and factory_ready and database_ready
        if missing:
            diagnostic = f"agent {agent_id!r} is missing required environment: {', '.join(missing)}"
        elif not package_ready:
            diagnostic = f"agent {agent_id!r} is missing its optional SDK package"
        elif not factory_ready:
            diagnostic = f"agent {agent_id!r} factory is unavailable"
        elif not database_ready:
            diagnostic = f"agent {agent_id!r} database is unavailable"
        else:
            diagnostic = f"agent {agent_id!r} is ready"
        return AgentReadiness(
            id=agent_id,
            adapter=definition.adapter,
            version=definition.version,
            ready=ready,
            missing_env=missing,
            missing_requirements=missing_requirements,
            package_ready=package_ready,
            factory_ready=factory_ready,
            database_ready=database_ready,
            database=database,
            diagnostic=diagnostic,
        )

    def get(self, agent_id: str) -> AgentDefinition:
        """Return a definition with a clear unknown-agent failure."""
        try:
            return self.agents[agent_id]
        except KeyError as exc:
            suggestions = get_close_matches(agent_id, self.agents, n=3, cutoff=0.35)
            suffix = f"; did you mean: {', '.join(suggestions)}" if suggestions else ""
            raise ValueError(f"unknown agent id {agent_id!r}{suffix}") from exc

    async def create(self, agent_id: str, environment: Mapping[str, str]) -> WorkerAdapter:
        """Lazily load and invoke a sync or async factory."""
        definition = self.get(agent_id)
        readiness = self.readiness(agent_id, environment)
        if not readiness.ready:
            raise ValueError(readiness.diagnostic)
        if definition.factory is None:
            from worker_worlds.stubs import StubWorkerAdapter

            result: object = StubWorkerAdapter()
        else:
            factory = load_agent_factory(definition.factory)
            context = AgentFactoryContext(definition=definition, environment=environment)
            try:
                result = factory(context)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                # Factory exceptions often contain provider responses or credential values.
                raise RuntimeError(f"factory for agent {agent_id!r} failed") from None
        if not isinstance(result, WorkerAdapter):
            raise TypeError(
                f"factory for agent {agent_id!r} returned {type(result).__name__}; "
                "expected WorkerAdapter"
            )
        return result


AgentFactory = Callable[[AgentFactoryContext], object | Awaitable[object]]


def load_agent_factory(path: str) -> AgentFactory:
    """Import a validated module:callable factory without importing during config loading."""
    match = _FACTORY_PATTERN.fullmatch(path)
    if match is None:
        raise ValueError("factory must be an explicit module:callable path")
    module_name = match.group("module")
    attribute_path = match.group("callable")
    import_failure: ValueError | RuntimeError | None = None
    try:
        value: Any = import_module(module_name)
    except (ImportError, ModuleNotFoundError):
        import_failure = ValueError(f"could not import agent factory module {module_name!r}")
    except Exception:
        import_failure = RuntimeError(f"agent factory module {module_name!r} failed during import")
    if import_failure is not None:
        # Raise outside the except block so no secret-bearing exception survives as context.
        raise import_failure
    for attribute in attribute_path.split("."):
        resolution_failure: ValueError | RuntimeError | None = None
        try:
            value = getattr(value, attribute)
        except AttributeError:
            resolution_failure = ValueError(f"agent factory {path!r} does not exist")
        except Exception:
            resolution_failure = RuntimeError(f"agent factory {path!r} failed during resolution")
        if resolution_failure is not None:
            raise resolution_failure
    if not callable(value):
        raise TypeError(f"agent factory {path!r} is not callable")
    return cast(AgentFactory, value)
