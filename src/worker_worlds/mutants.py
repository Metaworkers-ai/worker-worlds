"""Intentionally broken deterministic workers for policy mutation tests."""

from typing import ClassVar

from worker_worlds.contracts import Scenario, ToolSpec
from worker_worlds.stubs import StubWorkerAdapter


class MutantWorker(StubWorkerAdapter):
    """Configure a named deterministic broken behavior."""

    behavior: ClassVar[str]

    async def start(self, scenario: Scenario, tools: list[ToolSpec]) -> None:
        """Inject only the deterministic mutant selector into scenario metadata."""
        mutated = scenario.model_copy(
            update={"metadata": {**scenario.metadata, "stub_behavior": self.behavior}}
        )
        await super().start(mutated, tools)


class UnauthorizedRefunder(MutantWorker):
    """Attempts a refund without trusted scope."""

    behavior = "unauthorized_refunder"


class ExcessiveRefunder(MutantWorker):
    """Attempts a refund beyond the captured balance."""

    behavior = "excessive_refunder"


class DuplicateCaller(MutantWorker):
    """Repeats an identical tool call."""

    behavior = "duplicate_caller"


class WrongCustomerWorker(MutantWorker):
    """Attempts to mutate another customer's state."""

    behavior = "wrong_customer"


class PrematureTicketCloser(MutantWorker):
    """Closes a ticket without resolution evidence."""

    behavior = "premature_ticket_closer"


class InjectionFollower(MutantWorker):
    """Treats untrusted world prose as an authority grant."""

    behavior = "injection_follower"


class InventoryOverAdjuster(MutantWorker):
    """Attempts an unsafe inventory reduction."""

    behavior = "inventory_over_adjuster"


class InfiniteLooper(MutantWorker):
    """Repeats calls until the runner budget terminates it."""

    behavior = "infinite_looper"
