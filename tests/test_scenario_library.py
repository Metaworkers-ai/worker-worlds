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
