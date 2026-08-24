"""Deterministic public-interface model fake for OpenAI Agents adapter tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)


@dataclass
class ModelStep:
    """One deterministic response or failure for the public SDK Model interface."""

    output: list[Any] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    response_id: str | None = None
    request_id: str | None = None
    error: Exception | None = None
    responder: Callable[[object], Awaitable[ModelStep]] | None = None


class ScriptedModel(Model):
    """Minimal deterministic model fake independent of private SDK test helpers."""

    def __init__(self, steps: list[ModelStep | list[Any]]) -> None:
        """Retain an ordered response script."""
        self._steps = [
            item if isinstance(item, ModelStep) else ModelStep(output=item) for item in steps
        ]

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        """Return the next response without a provider call."""
        del args
        if not self._steps:
            raise AssertionError("scripted model was called after its final step")
        step = self._steps.pop(0)
        if step.responder is not None:
            step = await step.responder(kwargs)
        if step.error is not None:
            raise step.error
        return ModelResponse(
            output=step.output,
            usage=step.usage,
            response_id=step.response_id,
            request_id=step.request_id,
        )

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """Reject streaming because Worker Worlds uses the bounded response path."""
        del args, kwargs

        async def unsupported() -> AsyncIterator[Any]:
            if False:
                yield None
            raise AssertionError("streaming is not used by this scripted model")

        return unsupported()

    def assert_complete(self) -> None:
        """Assert that the caller consumed every scripted response."""
        assert not self._steps


def function_call(name: str, arguments: dict[str, object], *, call_id: str) -> Any:
    """Create one SDK-compatible deterministic function-call item."""
    return ResponseFunctionToolCall(
        arguments=json.dumps(arguments, sort_keys=True),
        call_id=call_id,
        name=name,
        type="function_call",
        id=call_id,
        status="completed",
    )


def assistant_message(message: str) -> Any:
    """Create one SDK-compatible deterministic assistant message."""
    return ResponseOutputMessage(
        id="message_test",
        content=[
            ResponseOutputText(annotations=[], text=message, type="output_text", logprobs=None)
        ],
        role="assistant",
        status="completed",
        type="message",
    )
