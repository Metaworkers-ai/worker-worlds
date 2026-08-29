"""Versioned domain, role, capability, suite, and scenario catalog."""

from __future__ import annotations

import argparse
import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self, cast

from pydantic import Field, field_validator, model_validator

from worker_worlds.contracts import Contract, JsonValue, Limits, Scenario, ScenarioId
from worker_worlds.enterprise_scenarios import enterprise_scenarios
from worker_worlds.scenario_identity import scenario_content_hash
from worker_worlds.scenario_library import reviewed_scenarios
from worker_worlds.scenarios import load_scenario

CATALOG_VERSION = "1.1.0"
CATALOG_PATH = Path("catalog/v1/catalog.json")
CatalogId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")]
SemanticVersion = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]


class SuiteTier(StrEnum):
    """Supported deterministic suite sizes."""

    SMOKE = "smoke"
    STANDARD = "standard"
    FULL = "full"
    CUSTOM = "custom"


class Difficulty(StrEnum):
    """Bounded scenario difficulty."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    ADVERSARIAL = "adversarial"


class RiskCategory(StrEnum):
    """Bounded enterprise risk category."""

    FINANCIAL = "financial"
    AUTHORIZATION = "authorization"
    OPERATIONAL = "operational"
    SAFETY = "safety"
    RELIABILITY = "reliability"


def _sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    ordered = tuple(sorted(values))
    if len(ordered) != len(set(ordered)):
        raise ValueError(f"{field_name} contains duplicate IDs")
    return ordered


class CapabilityDefinition(Contract):
    """One independently versioned business capability."""

    id: CatalogId
    domain_id: CatalogId
    version: SemanticVersion
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


class RoleDefinition(Contract):
    """A business role and the capabilities it is evaluated against."""

    id: CatalogId
    domain_id: CatalogId
    version: SemanticVersion
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    capability_ids: tuple[CatalogId, ...]

    @field_validator("capability_ids")
    @classmethod
    def canonical_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize capability references to stable order."""
        return _sorted_unique(value, "capability_ids")


class DomainDefinition(Contract):
    """A deterministic enterprise world offered by Worker Worlds."""

    id: CatalogId
    version: SemanticVersion
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    world_names: tuple[CatalogId, ...]
    role_ids: tuple[CatalogId, ...]
    capability_ids: tuple[CatalogId, ...]

    @field_validator("world_names", "role_ids", "capability_ids")
    @classmethod
    def canonical_references(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        """Normalize all domain references to stable order."""
        return _sorted_unique(value, info.field_name)


class ScenarioClassification(Contract):
    """External taxonomy for one immutable scenario definition."""

    scenario_id: ScenarioId
    scenario_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_id: CatalogId
    role_ids: tuple[CatalogId, ...]
    capability_id: CatalogId
    difficulty: Difficulty
    risk_category: RiskCategory

    @field_validator("role_ids")
    @classmethod
    def canonical_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize role references to stable order."""
        if not value:
            raise ValueError("role_ids cannot be empty")
        return _sorted_unique(value, "role_ids")


class EvaluationSuiteDefinition(Contract):
    """Immutable deterministic membership for a role evaluation."""

    id: CatalogId
    domain_id: CatalogId
    role_id: CatalogId
    revision: SemanticVersion
    label: str = Field(min_length=1, max_length=120)
    tier: SuiteTier
    scenario_ids: tuple[ScenarioId, ...]
    capability_ids: tuple[CatalogId, ...]
    estimated_duration_s: Annotated[int, Field(ge=0)]
    default_limits: Limits = Field(default_factory=Limits)

    @field_validator("scenario_ids", "capability_ids")
    @classmethod
    def canonical_membership(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        """Normalize deterministic suite membership."""
        return _sorted_unique(value, info.field_name)


class Catalog(Contract):
    """Validated immutable aggregate for every catalog reference."""

    catalog_version: SemanticVersion = CATALOG_VERSION
    domains: tuple[DomainDefinition, ...]
    roles: tuple[RoleDefinition, ...]
    capabilities: tuple[CapabilityDefinition, ...]
    suites: tuple[EvaluationSuiteDefinition, ...]
    classifications: tuple[ScenarioClassification, ...]

    @model_validator(mode="before")
    @classmethod
    def canonical_collections(cls, value: object) -> object:
        """Sort aggregate collections so construction order cannot affect bytes."""
        if not isinstance(value, dict):
            return value
        result = dict(value)
        keys = {
            "domains": "id",
            "roles": "id",
            "capabilities": "id",
            "suites": "id",
            "classifications": "scenario_id",
        }
        for name, key in keys.items():
            items = result.get(name)
            if isinstance(items, (list, tuple)):
                result[name] = sorted(
                    items,
                    key=lambda item: str(
                        item.get(key) if isinstance(item, dict) else getattr(item, key)
                    ),
                )
        return result

    @model_validator(mode="after")
    def references_are_valid(self) -> Self:
        """Reject duplicates, orphans, and cross-domain references."""
        domains = {item.id: item for item in self.domains}
        roles = {item.id: item for item in self.roles}
        capabilities = {item.id: item for item in self.capabilities}
        suites = {item.id: item for item in self.suites}
        classifications = {str(item.scenario_id): item for item in self.classifications}
        collections = (
            ("domain", self.domains, domains),
            ("role", self.roles, roles),
            ("capability", self.capabilities, capabilities),
            ("suite", self.suites, suites),
            ("classification", self.classifications, classifications),
        )
        for name, items, indexed in collections:
            if len(items) != len(indexed):
                raise ValueError(f"duplicate {name} ID")
        for role in self.roles:
            if role.domain_id not in domains:
                raise ValueError(f"role {role.id} references missing domain {role.domain_id}")
            for capability_id in role.capability_ids:
                capability = capabilities.get(capability_id)
                if capability is None or capability.domain_id != role.domain_id:
                    raise ValueError(f"role {role.id} has invalid capability {capability_id}")
        for capability in self.capabilities:
            if capability.domain_id not in domains:
                raise ValueError(
                    f"capability {capability.id} references missing domain {capability.domain_id}"
                )
        for domain in self.domains:
            if any(
                world_name
                not in {
                    "stub-commerce",
                    "postgres-commerce",
                    "postgres-commerce-supply-chain",
                    "postgres-insurance",
                }
                for world_name in domain.world_names
            ):
                raise ValueError(f"domain {domain.id} has unknown world reference")
            if any(
                role_id not in roles or roles[role_id].domain_id != domain.id
                for role_id in domain.role_ids
            ):
                raise ValueError(f"domain {domain.id} has invalid role reference")
            if any(
                capability_id not in capabilities
                or capabilities[capability_id].domain_id != domain.id
                for capability_id in domain.capability_ids
            ):
                raise ValueError(f"domain {domain.id} has invalid capability reference")
        for classification in self.classifications:
            if classification.domain_id not in domains:
                raise ValueError(f"scenario {classification.scenario_id} has missing domain")
            capability = capabilities.get(classification.capability_id)
            if capability is None or capability.domain_id != classification.domain_id:
                raise ValueError(f"scenario {classification.scenario_id} has invalid capability")
            for role_id in classification.role_ids:
                referenced_role = roles.get(role_id)
                if referenced_role is None or referenced_role.domain_id != classification.domain_id:
                    raise ValueError(f"scenario {classification.scenario_id} has invalid role")
                if classification.capability_id not in referenced_role.capability_ids:
                    raise ValueError(
                        f"scenario {classification.scenario_id} capability is not assigned "
                        f"to {role_id}"
                    )
        for suite in self.suites:
            suite_role = roles.get(suite.role_id)
            if suite_role is None or suite_role.domain_id != suite.domain_id:
                raise ValueError(f"suite {suite.id} has invalid role")
            if not suite.scenario_ids and suite.tier is not SuiteTier.CUSTOM:
                raise ValueError(f"suite {suite.id} cannot be empty")
            for scenario_id in suite.scenario_ids:
                suite_classification = classifications.get(str(scenario_id))
                if (
                    suite_classification is None
                    or suite_classification.domain_id != suite.domain_id
                    or suite.role_id not in suite_classification.role_ids
                ):
                    raise ValueError(f"suite {suite.id} has invalid scenario {scenario_id}")
            if any(
                capability_id not in suite_role.capability_ids
                for capability_id in suite.capability_ids
            ):
                raise ValueError(f"suite {suite.id} has cross-role capability")
        return self

    def domain(self, domain_id: str) -> DomainDefinition:
        """Return a domain or raise a stable lookup error."""
        return next(item for item in self.domains if item.id == domain_id)

    def role(self, role_id: str) -> RoleDefinition:
        """Return a role or raise a stable lookup error."""
        return next(item for item in self.roles if item.id == role_id)

    def suite(self, suite_id: str) -> EvaluationSuiteDefinition:
        """Return a suite or raise a stable lookup error."""
        return next(item for item in self.suites if item.id == suite_id)


_FAMILY_TAXONOMY: dict[str, tuple[str, tuple[str, ...], RiskCategory]] = {
    "refunds-payments": (
        "refund-resolution",
        ("customer-support-agent", "refund-specialist"),
        RiskCategory.FINANCIAL,
    ),
    "orders-identity": (
        "order-operations",
        ("customer-support-agent", "order-operations-specialist"),
        RiskCategory.AUTHORIZATION,
    ),
    "inventory-catalog": (
        "inventory-control",
        ("inventory-controller", "order-operations-specialist"),
        RiskCategory.OPERATIONAL,
    ),
    "tickets-escalation": (
        "support-escalation",
        ("customer-support-agent", "support-escalation-manager"),
        RiskCategory.SAFETY,
    ),
    "shipping-fulfillment": (
        "fulfillment-coordination",
        ("fulfillment-coordinator", "order-operations-specialist"),
        RiskCategory.OPERATIONAL,
    ),
    "adversarial-conflicts": (
        "policy-resilience",
        (
            "customer-support-agent",
            "fulfillment-coordinator",
            "inventory-controller",
            "order-operations-specialist",
            "refund-specialist",
            "support-escalation-manager",
        ),
        RiskCategory.SAFETY,
    ),
    "reliability-injection": (
        "operational-reliability",
        (
            "customer-support-agent",
            "fulfillment-coordinator",
            "inventory-controller",
            "order-operations-specialist",
            "refund-specialist",
            "support-escalation-manager",
        ),
        RiskCategory.RELIABILITY,
    ),
}


def _difficulty(case_kind: str) -> Difficulty:
    return {
        "safe-reference": Difficulty.BASIC,
        "boundary-value": Difficulty.INTERMEDIATE,
        "idempotent-retry": Difficulty.INTERMEDIATE,
        "direct-violation": Difficulty.ADVANCED,
        "conflicting-state": Difficulty.ADVANCED,
        "adversarial-input": Difficulty.ADVERSARIAL,
    }[case_kind]


def _example_scenarios() -> tuple[Scenario, ...]:
    """Load the packaged CLI examples so API discovery and catalog coverage agree."""
    roots = (
        Path("examples/scenarios"),
        Path(sys.prefix) / "share/worker-worlds/examples/scenarios",
    )
    root = next((item for item in roots if item.is_dir()), None)
    if root is None:
        return ()
    return tuple(load_scenario(path) for path in sorted(root.glob("*.yaml")))


def _example_family(scenario_id: str) -> str:
    if scenario_id.startswith("refund."):
        return "refunds-payments"
    if scenario_id.startswith("inventory."):
        return "inventory-catalog"
    if scenario_id.startswith("ticket."):
        return "tickets-escalation"
    return "reliability-injection"


def builtin_catalog() -> Catalog:
    """Construct the checked commerce catalog without filesystem state."""
    capability_labels = {
        "refund-resolution": ("Refund resolution", "commerce"),
        "order-operations": ("Order operations", "commerce"),
        "inventory-control": ("Inventory control", "commerce"),
        "support-escalation": ("Support escalation", "commerce"),
        "fulfillment-coordination": ("Fulfillment coordination", "commerce"),
        "policy-resilience": ("Policy resilience", "commerce"),
        "operational-reliability": ("Operational reliability", "commerce"),
        "supply-chain-analysis": ("Supply-chain analysis", "commerce"),
        "claims-adjustment": ("Claims adjustment", "insurance"),
    }
    capabilities = tuple(
        CapabilityDefinition(
            id=capability_id,
            domain_id=domain_id,
            version=CATALOG_VERSION,
            label=label,
            description=f"Evaluate {label.lower()} behavior in deterministic commerce worlds.",
        )
        for capability_id, (label, domain_id) in sorted(capability_labels.items())
    )
    role_labels = {
        "customer-support-agent": "Customer Support Agent",
        "refund-specialist": "Refund Specialist",
        "order-operations-specialist": "Order Operations Specialist",
        "inventory-controller": "Inventory Controller",
        "fulfillment-coordinator": "Fulfillment Coordinator",
        "support-escalation-manager": "Support Escalation Manager",
    }
    role_capabilities = {
        role_id: tuple(
            sorted(
                capability_id
                for capability_id, role_ids, _ in _FAMILY_TAXONOMY.values()
                if role_id in role_ids
            )
        )
        for role_id in role_labels
    }
    roles = tuple(
        RoleDefinition(
            id=role_id,
            domain_id="commerce",
            version=CATALOG_VERSION,
            label=label,
            description=f"Evaluate an AI worker acting as a {label.lower()}.",
            capability_ids=role_capabilities[role_id],
        )
        for role_id, label in sorted(role_labels.items())
    ) + (
        RoleDefinition(
            id="supply-chain-analyst",
            domain_id="commerce",
            version=CATALOG_VERSION,
            label="Supply Chain Analyst",
            description="Evaluate inventory risk, replenishment, transfers, and supplier delay.",
            capability_ids=("supply-chain-analysis",),
        ),
        RoleDefinition(
            id="claims-adjuster",
            domain_id="insurance",
            version=CATALOG_VERSION,
            label="Claims Adjuster",
            description=(
                "Evaluate policy, evidence, claim decision, investigation, and payment work."
            ),
            capability_ids=("claims-adjustment",),
        ),
    )
    scenarios = reviewed_scenarios()
    legacy_classifications = tuple(
        ScenarioClassification(
            scenario_id=scenario.id,
            scenario_hash=scenario_content_hash(scenario),
            domain_id="commerce",
            role_ids=_FAMILY_TAXONOMY[str(scenario.metadata["family"])][1],
            capability_id=_FAMILY_TAXONOMY[str(scenario.metadata["family"])][0],
            difficulty=_difficulty(str(scenario.metadata["case_kind"])),
            risk_category=_FAMILY_TAXONOMY[str(scenario.metadata["family"])][2],
        )
        for scenario in scenarios
    )
    added_classifications = tuple(
        ScenarioClassification(
            scenario_id=scenario.id,
            scenario_hash=scenario_content_hash(scenario),
            domain_id=str(scenario.metadata["domain_id"]),
            role_ids=tuple(
                str(item) for item in cast(list[JsonValue], scenario.metadata["role_ids"])
            ),
            capability_id=str(scenario.metadata["capability"]),
            difficulty=Difficulty(str(scenario.metadata["difficulty"])),
            risk_category=RiskCategory(str(scenario.metadata["risk_category"])),
        )
        for scenario in enterprise_scenarios()
    )
    example_classifications = tuple(
        ScenarioClassification(
            scenario_id=scenario.id,
            scenario_hash=scenario_content_hash(scenario),
            domain_id="commerce",
            role_ids=_FAMILY_TAXONOMY[_example_family(str(scenario.id))][1],
            capability_id=_FAMILY_TAXONOMY[_example_family(str(scenario.id))][0],
            difficulty=(
                Difficulty.ADVERSARIAL
                if any(tag in {"authorization", "unsafe", "budget"} for tag in scenario.tags)
                else Difficulty.BASIC
            ),
            risk_category=_FAMILY_TAXONOMY[_example_family(str(scenario.id))][2],
        )
        for scenario in _example_scenarios()
    )
    classifications = (*legacy_classifications, *added_classifications, *example_classifications)
    live_scenario_ids = {scenario.id for scenario in (*scenarios, *enterprise_scenarios())}
    suites: list[EvaluationSuiteDefinition] = []
    for role in roles:
        eligible = tuple(
            item.scenario_id
            for item in classifications
            if role.id in item.role_ids and item.scenario_id in live_scenario_ids
        )
        standard_count = 10 if role.id in {"supply-chain-analyst", "claims-adjuster"} else 30
        for tier, count in (
            (SuiteTier.SMOKE, 6),
            (SuiteTier.STANDARD, min(standard_count, len(eligible))),
            (SuiteTier.FULL, len(eligible)),
        ):
            selected = eligible[:count]
            suites.append(
                EvaluationSuiteDefinition(
                    id=f"{role.domain_id}.{role.id}.{tier.value}",
                    domain_id=role.domain_id,
                    role_id=role.id,
                    revision=CATALOG_VERSION,
                    label=f"{role.label} {tier.value.title()}",
                    tier=tier,
                    scenario_ids=selected,
                    capability_ids=tuple(
                        sorted(
                            {
                                item.capability_id
                                for item in classifications
                                if item.scenario_id in selected
                            }
                        )
                    ),
                    estimated_duration_s=len(selected) * 3,
                )
            )
        suites.append(
            EvaluationSuiteDefinition(
                id=f"{role.domain_id}.{role.id}.custom",
                domain_id=role.domain_id,
                role_id=role.id,
                revision=CATALOG_VERSION,
                label=f"{role.label} Custom",
                tier=SuiteTier.CUSTOM,
                scenario_ids=(),
                capability_ids=role.capability_ids,
                estimated_duration_s=0,
            )
        )
    return Catalog(
        domains=(
            DomainDefinition(
                id="commerce",
                version=CATALOG_VERSION,
                label="Retail & E-commerce",
                description="Orders, refunds, support, inventory, and fulfillment operations.",
                world_names=(
                    "postgres-commerce",
                    "postgres-commerce-supply-chain",
                    "stub-commerce",
                ),
                role_ids=tuple(role.id for role in roles if role.domain_id == "commerce"),
                capability_ids=tuple(
                    item.id for item in capabilities if item.domain_id == "commerce"
                ),
            ),
            DomainDefinition(
                id="insurance",
                version=CATALOG_VERSION,
                label="Insurance",
                description="Policies, coverage, claims, evidence, investigations, and payments.",
                world_names=("postgres-insurance",),
                role_ids=tuple(role.id for role in roles if role.domain_id == "insurance"),
                capability_ids=tuple(
                    item.id for item in capabilities if item.domain_id == "insurance"
                ),
            ),
        ),
        roles=roles,
        capabilities=capabilities,
        suites=tuple(suites),
        classifications=classifications,
    )


def catalog_text() -> str:
    """Return the canonical checked catalog representation."""
    return (
        json.dumps(
            builtin_catalog().model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False
        )
        + "\n"
    )


def load_catalog(path: Path | None = None) -> Catalog:
    """Load a strict catalog, defaulting to the source or installed artifact."""
    selected = path or CATALOG_PATH
    if not selected.exists():
        return builtin_catalog()
    return Catalog.model_validate_json(selected.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """Generate or check the catalog artifact."""
    parser = argparse.ArgumentParser(prog="python -m worker_worlds.catalog")
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("--path", type=Path, default=CATALOG_PATH)
    args = parser.parse_args(argv)
    expected = catalog_text()
    if args.command == "generate":
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(expected, encoding="utf-8")
        print(f"catalog generated: {args.path}")
        return 0
    if not args.path.exists() or args.path.read_text(encoding="utf-8") != expected:
        print(f"catalog drift detected: {args.path}")
        return 1
    print(f"catalog current: {args.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
