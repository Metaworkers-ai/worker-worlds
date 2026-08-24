import asyncio
from datetime import UTC, datetime
from pathlib import Path

from worker_worlds.contracts import (
    AuthorizationContext,
    RunRecord,
    Scenario,
    TerminalReason,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    VerdictStatus,
    WorkerTurn,
)
from worker_worlds.grading import DeterministicGrader
from worker_worlds.runner import Runner
from worker_worlds.scenarios import load_scenario
from worker_worlds.stubs import StubWorkerAdapter, StubWorld


async def _run_fixture(name: str, *, fail_snapshot: bool = False) -> RunRecord:
    scenario = load_scenario(Path("tests/fixtures") / name)
    return await Runner(DeterministicGrader()).run(
        scenario, StubWorld(fail_snapshot=fail_snapshot), StubWorkerAdapter()
    )


async def test_happy_path_runner() -> None:
    record = await _run_fixture("successful_partial_refund.yaml")
    assert record.terminal_reason is TerminalReason.COMPLETED
    assert record.passed
    assert len(record.events) == 1
    assert record.events[0].event_type == "refund.issued"


async def test_timeout_is_distinct_and_preserves_snapshots() -> None:
    record = await _run_fixture("worker_timeout.yaml")
    assert record.terminal_reason is TerminalReason.TIMEOUT
    assert not record.incomplete_evidence
    assert record.error_type == "TimeoutError"


async def test_tool_error_is_normalized() -> None:
    record = await _run_fixture("tool_failure.yaml")
    assert record.terminal_reason is TerminalReason.TOOL_ERROR
    assert record.turns[-1].tool_result is not None
    assert record.turns[-1].tool_result.error_type == "StubToolError"


async def test_infrastructure_failure_preserves_provenance() -> None:
    record = await _run_fixture("infrastructure_failure.yaml", fail_snapshot=True)
    assert record.terminal_reason is TerminalReason.INFRASTRUCTURE_ERROR
    assert record.incomplete_evidence
    assert record.error_type == "InfrastructureError"


async def test_incomplete_evidence_cannot_pass() -> None:
    record = await _run_fixture("incomplete_evidence.yaml", fail_snapshot=True)
    assert not record.passed
    assert record.verdicts[0].status is VerdictStatus.ERROR
    assert "cannot pass" in record.verdicts[0].message


async def test_unauthorized_refund_does_not_mutate() -> None:
    record = await _run_fixture("unauthorized_refund_attempt.yaml")
    assert record.terminal_reason is TerminalReason.TOOL_ERROR
    assert not record.events
    assert record.turns[-1].tool_result is not None
    assert record.turns[-1].tool_result.error_type == "AuthorizationDenied"


class SelfGrantingWorker(StubWorkerAdapter):
    async def next_turn(self, tool_result: ToolResult | None) -> WorkerTurn:
        turn = await super().next_turn(tool_result)
        if turn.tool_call is None:
            return turn
        call = turn.tool_call.model_copy(
            update={
                "authorization": AuthorizationContext(
                    actor_id="untrusted-worker",
                    customer_id="cus_102",
                    scopes=frozenset({"refund:own_order", "claim:pay"}),
                )
            }
        )
        return turn.model_copy(update={"tool_call": call})


async def test_runner_ignores_worker_self_granted_authorization() -> None:
    scenario = load_scenario(Path("tests/fixtures/unauthorized_refund_attempt.yaml"))
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), SelfGrantingWorker())
    assert not record.events
    assert record.turns[0].tool_call is not None
    assert record.turns[0].tool_call.authorization.scopes == frozenset()


class SlowToolWorld(StubWorld):
    async def invoke(self, call: ToolCall) -> ToolResult:
        await asyncio.sleep(0.05)
        return await super().invoke(call)


class ValidationErrorWorld(StubWorld):
    async def invoke(self, call: ToolCall) -> ToolResult:
        now = datetime.now(UTC)
        return ToolResult(
            call_id=call.id,
            status=ToolResultStatus.ERROR,
            error_type="ToolValidationError",
            error_message="invalid input",
            started_at=now,
            ended_at=now,
        )


async def test_tool_timeout_is_distinct_and_cleans_up(happy_scenario: Scenario) -> None:
    scenario = happy_scenario.model_copy(
        update={"limits": happy_scenario.limits.model_copy(update={"tool_timeout_s": 0.001})}
    )
    world = SlowToolWorld()
    record = await Runner(DeterministicGrader()).run(scenario, world, StubWorkerAdapter())
    assert record.terminal_reason is TerminalReason.TOOL_TIMEOUT
    assert record.cleanup_succeeded
    assert world.closed


async def test_tool_validation_failure_is_preserved(happy_scenario: Scenario) -> None:
    world = ValidationErrorWorld()
    record = await Runner(DeterministicGrader()).run(
        scenario=happy_scenario, world=world, worker=StubWorkerAdapter()
    )
    assert record.terminal_reason is TerminalReason.TOOL_ERROR
    assert record.turns[-1].tool_result is not None
    assert record.turns[-1].tool_result.error_type == "ToolValidationError"


async def test_tool_call_budget_exceeded(happy_scenario: Scenario) -> None:
    scenario = happy_scenario.model_copy(
        update={"limits": happy_scenario.limits.model_copy(update={"tool_calls": 0})}
    )
    record = await Runner(DeterministicGrader()).run(scenario, StubWorld(), StubWorkerAdapter())
    assert record.terminal_reason is TerminalReason.BUDGET_EXCEEDED


async def test_external_cancellation_is_recorded_and_cleaned(happy_scenario: Scenario) -> None:
    scenario = happy_scenario.model_copy(
        update={
            "metadata": {"stub_behavior": "timeout"},
            "limits": happy_scenario.limits.model_copy(update={"wall_time_s": 5}),
        }
    )
    world = StubWorld()
    task = asyncio.create_task(
        Runner(DeterministicGrader()).run(scenario, world, StubWorkerAdapter())
    )
    await asyncio.sleep(0)
    task.cancel()
    record = await task
    assert record.terminal_reason is TerminalReason.CANCELLED
    assert world.closed
