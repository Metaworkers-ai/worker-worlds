"""Immutable evaluation context sidecars for legacy-compatible evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from worker_worlds.catalog import (
    Catalog,
    CatalogId,
    EvaluationSuiteDefinition,
    SemanticVersion,
    SuiteTier,
)
from worker_worlds.contracts import Contract, Limits, RunId, Scenario, ScenarioId
from worker_worlds.scenario_identity import scenario_content_hash


class EvaluationContext(Contract):
    """Server-derived business and runtime provenance for an evaluation."""

    catalog_version: SemanticVersion
    domain_id: CatalogId
    role_id: CatalogId
    suite_id: CatalogId | None = None
    suite_revision: SemanticVersion | None = None
    scenario_ids: tuple[ScenarioId, ...]
    scenario_hashes: dict[str, str]
    agent_id: str
    agent_version: str
    world_name: str
    world_version: str
    seeds: tuple[int, ...]
    limits: Limits


class EvaluationRunManifest(Contract):
    """Context sidecar referencing one immutable legacy RunRecord."""

    run_id: RunId
    run_record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context: EvaluationContext
    created_at: datetime


def validate_selection(
    catalog: Catalog,
    scenario: Scenario,
    *,
    domain_id: str,
    role_id: str,
    suite_id: str | None,
) -> None:
    """Validate a domain-role-suite-scenario selection without mutation."""
    try:
        domain = catalog.domain(domain_id)
    except StopIteration as exc:
        raise ValueError(f"unknown domain: {domain_id}") from exc
    try:
        role = catalog.role(role_id)
    except StopIteration as exc:
        raise ValueError(f"unknown role: {role_id}") from exc
    if role.domain_id != domain.id:
        raise ValueError(f"role {role_id} does not belong to domain {domain_id}")
    classification = next(
        (item for item in catalog.classifications if item.scenario_id == scenario.id), None
    )
    if classification is None or classification.domain_id != domain.id:
        raise ValueError(f"scenario {scenario.id} does not belong to domain {domain_id}")
    if role.id not in classification.role_ids:
        raise ValueError(f"scenario {scenario.id} is not assigned to role {role_id}")
    if suite_id is not None:
        try:
            suite = catalog.suite(suite_id)
        except StopIteration as exc:
            raise ValueError(f"unknown suite: {suite_id}") from exc
        if suite.domain_id != domain.id or suite.role_id != role.id:
            raise ValueError(f"suite {suite_id} is incompatible with {domain_id}/{role_id}")
        if suite.tier is not SuiteTier.CUSTOM and scenario.id not in suite.scenario_ids:
            raise ValueError(f"scenario {scenario.id} is not a member of suite {suite_id}")


def build_context(
    catalog: Catalog,
    scenario: Scenario,
    *,
    domain_id: str,
    role_id: str,
    suite_id: str | None,
    agent_id: str,
    agent_version: str,
    world_name: str,
    world_version: str,
) -> EvaluationContext:
    """Build immutable context after validating the selected business scope."""
    validate_selection(catalog, scenario, domain_id=domain_id, role_id=role_id, suite_id=suite_id)
    suite = catalog.suite(suite_id) if suite_id else None
    return EvaluationContext(
        catalog_version=catalog.catalog_version,
        domain_id=domain_id,
        role_id=role_id,
        suite_id=suite_id,
        suite_revision=suite.revision if suite else None,
        scenario_ids=(scenario.id,),
        scenario_hashes={str(scenario.id): scenario_content_hash(scenario)},
        agent_id=agent_id,
        agent_version=agent_version,
        world_name=world_name,
        world_version=world_version,
        seeds=(scenario.world.seed,),
        limits=scenario.limits,
    )


def run_manifest(
    run_id: RunId, run_record_hash: str, context: EvaluationContext
) -> EvaluationRunManifest:
    """Create a timestamped sidecar with controlled UTC semantics."""
    return EvaluationRunManifest(
        run_id=run_id,
        run_record_hash=run_record_hash,
        context=context,
        created_at=datetime.now(UTC),
    )


def build_suite_context(
    catalog: Catalog,
    suite: EvaluationSuiteDefinition,
    scenarios: dict[str, Scenario],
    *,
    agent_id: str,
    agent_version: str,
    world_name: str,
    world_version: str,
) -> EvaluationContext:
    """Build complete immutable context for a server-validated suite selection."""
    selected: list[Scenario] = []
    for scenario_id in suite.scenario_ids:
        scenario = scenarios.get(str(scenario_id))
        if scenario is None:
            raise ValueError(f"suite scenario is unavailable: {scenario_id}")
        validate_selection(
            catalog,
            scenario,
            domain_id=suite.domain_id,
            role_id=suite.role_id,
            suite_id=suite.id,
        )
        selected.append(scenario)
    return EvaluationContext(
        catalog_version=catalog.catalog_version,
        domain_id=suite.domain_id,
        role_id=suite.role_id,
        suite_id=suite.id,
        suite_revision=suite.revision,
        scenario_ids=suite.scenario_ids,
        scenario_hashes={str(item.id): scenario_content_hash(item) for item in selected},
        agent_id=agent_id,
        agent_version=agent_version,
        world_name=world_name,
        world_version=world_version,
        seeds=tuple(item.world.seed for item in selected),
        limits=suite.default_limits,
    )
