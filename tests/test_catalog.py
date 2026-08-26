from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from worker_worlds.catalog import Catalog, SuiteTier, builtin_catalog, catalog_text, load_catalog
from worker_worlds.scenario_library import reviewed_scenarios


def test_builtin_catalog_is_complete_and_canonical() -> None:
    catalog = builtin_catalog()
    scenario_ids = {str(item.id) for item in reviewed_scenarios()}
    assert catalog.domains[0].id == "commerce"
    assert len(catalog.roles) == 8
    assert len(catalog.classifications) == 235
    assert scenario_ids < {str(item.scenario_id) for item in catalog.classifications}
    assert catalog.canonical_json() == load_catalog().canonical_json()
    assert catalog_text() == Path("catalog/v1/catalog.json").read_text(encoding="utf-8")


def test_catalog_order_does_not_change_serialization() -> None:
    catalog = builtin_catalog()
    data = catalog.model_dump(mode="json")
    for name in ("domains", "roles", "capabilities", "suites", "classifications"):
        data[name] = list(reversed(data[name]))
    reordered = Catalog.model_validate(data)
    assert reordered.canonical_json() == catalog.canonical_json()
    assert reordered.canonical_hash() == catalog.canonical_hash()


def test_catalog_rejects_unknown_duplicate_or_cross_domain_references() -> None:
    catalog = builtin_catalog()
    unknown = catalog.model_dump(mode="json")
    unknown["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Catalog.model_validate(unknown)

    duplicate = catalog.model_dump(mode="json")
    duplicate["roles"].append(duplicate["roles"][0])
    with pytest.raises(ValidationError, match="duplicate role ID"):
        Catalog.model_validate(duplicate)

    orphan = catalog.model_dump(mode="json")
    orphan["suites"][0]["role_id"] = "missing-role"
    with pytest.raises(ValidationError, match="invalid role"):
        Catalog.model_validate(orphan)


def test_role_suite_tiers_are_nested_and_deterministic() -> None:
    catalog = builtin_catalog()
    for role in catalog.roles:
        tiers = {suite.tier: suite for suite in catalog.suites if suite.role_id == role.id}
        assert set(tiers) == {
            SuiteTier.SMOKE,
            SuiteTier.STANDARD,
            SuiteTier.FULL,
            SuiteTier.CUSTOM,
        }
        smoke = set(tiers[SuiteTier.SMOKE].scenario_ids)
        standard = set(tiers[SuiteTier.STANDARD].scenario_ids)
        full = set(tiers[SuiteTier.FULL].scenario_ids)
        assert smoke <= standard <= full
        assert len(smoke) == 6
        assert tiers[SuiteTier.CUSTOM].scenario_ids == ()


def test_live_suites_exclude_demonstration_only_examples() -> None:
    catalog = builtin_catalog()
    example_ids = {
        "inventory.unchanged",
        "refund.duplicate",
        "refund.excessive",
        "refund.partial.happy",
        "refund.unauthorized",
        "refund.unsafe_intermediate",
        "resource.missing_usage",
        "resource.tool_budget",
        "ticket.escalation.required",
        "ticket.resolution.required",
    }
    assert all(
        not (set(suite.scenario_ids) & example_ids)
        for suite in catalog.suites
        if suite.tier is not SuiteTier.CUSTOM
    )
