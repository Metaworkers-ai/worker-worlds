import json
from collections import Counter

from worker_worlds.scenario_library import reviewed_scenarios


def test_reviewed_week3_library_has_required_family_coverage() -> None:
    scenarios = reviewed_scenarios()
    counts = Counter(str(scenario.metadata["family"]) for scenario in scenarios)
    assert len(scenarios) == 200
    assert counts == {
        "refunds-payments": 42,
        "orders-identity": 24,
        "inventory-catalog": 24,
        "tickets-escalation": 24,
        "shipping-fulfillment": 24,
        "adversarial-conflicts": 38,
        "reliability-injection": 24,
    }
    assert len({scenario.id for scenario in scenarios}) == len(scenarios)
    assert all(scenario.metadata["risk"] for scenario in scenarios)
    assert all(scenario.assertions[0].type == "policy" for scenario in scenarios)


def test_every_release_prompt_is_self_contained_and_inaction_fails_tool_evidence() -> None:
    for scenario in reviewed_scenarios():
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
            assert json.dumps(call["arguments"], sort_keys=True, separators=(",", ":")) in (
                scenario.trigger.content
            )
