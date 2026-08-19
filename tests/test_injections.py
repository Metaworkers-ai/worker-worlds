from __future__ import annotations

from worker_worlds.contracts import Scenario, TerminalReason
from worker_worlds.grading import DeterministicGrader
from worker_worlds.runner import Runner
from worker_worlds.stubs import StubWorkerAdapter, StubWorld


async def test_scheduled_injections_have_stable_delivery_order(
    happy_scenario: Scenario,
) -> None:
    scenario = happy_scenario.model_copy(
        update={
            "metadata": {
                **happy_scenario.metadata,
                "injections": [
                    {
                        "id": "before",
                        "trigger": "before_worker",
                        "event_type": "promotion.expired",
                        "payload": {"entity_type": "promotion", "entity_id": "promo_1"},
                    },
                    {
                        "id": "stockout",
                        "trigger": "after_nth_tool",
                        "after_nth_tool": 1,
                        "event_type": "inventory.stockout",
                        "payload": {"entity_type": "inventory", "entity_id": "inv_1"},
                    },
                    {
                        "id": "message",
                        "trigger": "before_terminal",
                        "event_type": "customer.message_received",
                        "payload": {"entity_type": "customer", "entity_id": "cus_102"},
                    },
                ],
            }
        }
    )
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), StubWorkerAdapter())
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert [event.event_type for event in record.events] == [
        "promotion.expired",
        "refund.issued",
        "inventory.stockout",
        "customer.message_received",
    ]
    assert [event.sequence for event in record.events] == [1, 2, 3, 4]


async def test_injection_budget_is_distinct(happy_scenario: Scenario) -> None:
    scenario = happy_scenario.model_copy(
        update={
            "limits": happy_scenario.limits.model_copy(update={"injections": 0}),
            "metadata": {
                **happy_scenario.metadata,
                "injections": [
                    {
                        "id": "one",
                        "trigger": "before_worker",
                        "event_type": "shipment.updated",
                    }
                ],
            },
        }
    )
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), StubWorkerAdapter())
    assert record.terminal_reason is TerminalReason.INJECTION_BUDGET_EXCEEDED
