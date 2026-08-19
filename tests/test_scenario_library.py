from collections import Counter

from worker_worlds.scenario_library import reviewed_scenarios


def test_reviewed_week3_library_has_required_family_coverage() -> None:
    scenarios = reviewed_scenarios()
    counts = Counter(str(scenario.metadata["family"]) for scenario in scenarios)
    assert len(scenarios) == 88
    assert counts == {
        "refunds-payments": 20,
        "orders-identity": 11,
        "inventory-catalog": 11,
        "tickets-escalation": 11,
        "shipping-fulfillment": 10,
        "adversarial-conflicts": 16,
        "reliability-injection": 9,
    }
    assert len({scenario.id for scenario in scenarios}) == len(scenarios)
    assert all(scenario.metadata["risk"] for scenario in scenarios)
    assert all(scenario.assertions[0].type == "policy" for scenario in scenarios)
