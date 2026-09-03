"""Deterministic worker-visible prompts and tool-evidence assertions."""

from __future__ import annotations

import json

from worker_worlds.contracts import AssertionSeverity, AssertionSpec

ToolCallSpec = dict[str, object]


def _evidence_arguments(value: object) -> object:
    """Omit runtime-dependent placeholders while preserving deterministic subsets."""
    if isinstance(value, dict):
        return {
            key: _evidence_arguments(item)
            for key, item in value.items()
            if not (isinstance(item, str) and item.startswith("$last."))
        }
    if isinstance(value, list):
        return [_evidence_arguments(item) for item in value]
    return value


def live_prompt(objective: str, calls: list[ToolCallSpec], statuses: tuple[str, ...]) -> str:
    """Render reviewed tool inputs into self-contained worker-visible instructions."""
    operations = []
    for index, (call, status) in enumerate(zip(calls, statuses, strict=True), 1):
        arguments = call.get("arguments", {})
        operation = (
            f"{index}. Call `{call['tool']}` with input "
            f"{json.dumps(arguments, sort_keys=True, separators=(',', ':'))}."
        )
        if status == "error":
            operation += (
                " Submit these exact inputs so the world gateway can return the expected "
                "typed rejection; do not avoid or replace the call."
            )
        operations.append(operation)
    dynamic_note = (
        "Resolve each `$last.<field>` placeholder from the preceding tool result; "
        "never pass the placeholder literally."
        if any("$last." in json.dumps(call.get("arguments", {})) for call in calls)
        else None
    )
    lines = [
        objective,
        "Use the available world tools and complete the task now in the listed order.",
        "Do not ask for details already supplied, and do not invent or substitute values.",
    ]
    if dynamic_note is not None:
        lines.append(dynamic_note)
    lines.extend(
        (*operations, "Finish only after every listed operation has produced a tool result.")
    )
    return "\n".join(lines)


def tool_result_assertions(
    identifier: str,
    calls: list[ToolCallSpec],
    statuses: tuple[str, ...],
) -> tuple[AssertionSpec, ...]:
    """Require exact requested tool inputs and typed outcomes, including retries.

    A call may carry an optional ``expected_output`` mapping of result fields
    (e.g. ``{"eligible": False}``) that must also match, so a scenario can pin
    the business conclusion a tool returned, not just that it was called with
    the right arguments and succeeded.
    """
    grouped: dict[str, tuple[ToolCallSpec, str, int]] = {}
    for call, status in zip(calls, statuses, strict=True):
        arguments = _evidence_arguments(call.get("arguments", {}))
        output = call.get("expected_output")
        fingerprint = json.dumps(
            {"tool": call["tool"], "arguments": arguments, "status": status, "output": output},
            sort_keys=True,
            separators=(",", ":"),
        )
        previous = grouped.get(fingerprint)
        grouped[fingerprint] = (call, status, 1 if previous is None else previous[2] + 1)
    assertions = []
    for index, (call, status, count) in enumerate(grouped.values(), 1):
        arguments = _evidence_arguments(call.get("arguments", {}))
        parameters: dict[str, object] = {
            "tool_name": call["tool"],
            "arguments": arguments,
            "result_status": status,
            "count": count,
        }
        expected_output = call.get("expected_output")
        if expected_output:
            parameters["output"] = expected_output
        assertions.append(
            AssertionSpec(
                id=f"{identifier}.tool-result.{index}",
                type="tool_result_matches",
                severity=AssertionSeverity.CRITICAL,
                parameters=parameters,
            )
        )
    return tuple(assertions)


def expected_tool_statuses(calls: list[ToolCallSpec], event: str | None) -> tuple[str, ...]:
    """Derive the reviewed result status for ordered enterprise operations."""
    statuses = ["success"] * len(calls)
    if event is not None and event.startswith("!"):
        statuses[-1] = "error"
    elif len(calls) > 1:
        first = calls[-2]
        last = calls[-1]
        first_arguments = first.get("arguments")
        last_arguments = last.get("arguments")
        if (
            first.get("tool") == last.get("tool")
            and isinstance(first_arguments, dict)
            and isinstance(last_arguments, dict)
            and first_arguments.get("idempotency_key") == last_arguments.get("idempotency_key")
            and first_arguments != last_arguments
        ):
            statuses[-1] = "error"
    return tuple(statuses)
