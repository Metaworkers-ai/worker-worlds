import json
from pathlib import Path

from worker_worlds.catalog import builtin_catalog
from worker_worlds.enterprise_scenarios import enterprise_scenarios
from worker_worlds.scenario_release import scenario_filename
from worker_worlds.scenarios import load_scenario


def test_enterprise_scenarios_are_checked_and_catalogued() -> None:
    scenarios = enterprise_scenarios()
    catalog = builtin_catalog()
    classified = {str(item.scenario_id) for item in catalog.classifications}
    assert len(scenarios) == 287
    for scenario in scenarios:
        path = Path("scenarios/enterprise") / scenario_filename(scenario)
        loaded = load_scenario(path)
        assert loaded.id == scenario.id
        normalized = loaded.model_copy(
            update={
                "assertions": tuple(
                    assertion.model_copy(update={"source_file": None, "source_index": None})
                    for assertion in loaded.assertions
                )
            }
        )
        assert normalized.canonical_hash() == scenario.canonical_hash()
        assert str(scenario.id) in classified


def test_new_roles_have_smoke_standard_and_full_suites() -> None:
    catalog = builtin_catalog()
    expected = {
        "supply-chain-analyst": {"full": 13, "smoke": 6, "standard": 10},
        "claims-adjuster": {"full": 132, "smoke": 6, "standard": 10},
        # Full REQUIREMENT.md FR-012 target reached: smoke=10, standard=40, full=102.
        "claims-analyst": {"full": 102, "smoke": 10, "standard": 40},
        # Phase 1 target (ADR 008 / docs/marketing-analyst-scenario-matrix.md):
        # smoke=8, standard=20, full=40. Scaling further is explicit Phase 2 work.
        "campaign-analyst": {"full": 40, "smoke": 8, "standard": 20},
    }
    for role_id, expected_counts in expected.items():
        suites = [item for item in catalog.suites if item.role_id == role_id]
        assert [item.tier.value for item in suites] == [
            "custom",
            "full",
            "smoke",
            "standard",
        ]
        counts = {item.tier.value: len(item.scenario_ids) for item in suites}
        assert counts == {"custom": 0, **expected_counts}


def test_every_live_enterprise_prompt_is_self_contained_and_requires_tool_evidence() -> None:
    for scenario in enterprise_scenarios():
        calls = scenario.metadata["stub_tool_calls"]
        assert isinstance(calls, list) and calls
        assert scenario.metadata["live_ready"] is True
        tool_assertions = [
            assertion
            for assertion in scenario.assertions
            if assertion.type == "tool_result_matches"
        ]
        assert tool_assertions
        expected_counts = []
        for assertion in tool_assertions:
            count = assertion.parameters["count"]
            assert isinstance(count, int) and not isinstance(count, bool)
            expected_counts.append(count)
        assert sum(expected_counts) == len(calls)
        for call in calls:
            assert isinstance(call, dict)
            assert f"`{call['tool']}`" in scenario.trigger.content
            assert (
                json.dumps(call["arguments"], sort_keys=True, separators=(",", ":"))
                in scenario.trigger.content
            )
        assert "Do not ask for details already supplied" in scenario.trigger.content
