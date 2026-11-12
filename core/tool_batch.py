"""Sequential dispatch for Gemini tool-call batches with fail-closed handoffs."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any


_NOT_EXECUTED = (
    "[NOT_EXECUTED] This tool call was not run because an earlier call in the "
    "same batch reached a confirmation or verification boundary. Re-evaluate "
    "it only after that result has been resolved; do not treat this call as executed."
)


def _result_text(function_response: Any) -> str:
    payload = getattr(function_response, "response", None)
    if isinstance(payload, dict):
        value = payload.get("result", "")
        return value if isinstance(value, str) else str(value or "")
    return ""


async def dispatch_tool_batch(
    function_calls: Iterable[Any],
    execute: Callable[[Any], Awaitable[Any]],
    make_response: Callable[[Any, str], Any],
    should_halt,
    on_halt: Callable[[], None] | None = None,
) -> list[Any]:
    """Execute calls in order; after a hand-off boundary, answer siblings honestly.

    Gemini may return several calls in one model response. Later calls were
    generated before the model saw earlier results, so they must not be run
    after a confirmation, mission hand-off, failure or uncertain outcome.
    The response list still includes every original call ID, as required by the
    function-calling protocol.
    """
    responses = []
    halted = False
    for call in function_calls:
        print(f"[JARVIS] tool call: {getattr(call, 'name', 'tool')}")
        if halted:
            responses.append(make_response(call, _NOT_EXECUTED))
            continue

        response = await execute(call)
        responses.append(response)
        if should_halt(_result_text(response)):
            halted = True
            if on_halt is not None:
                try:
                    on_halt()
                except Exception as exc:
                    # A logging/UI notification must not undo the safety stop.
                    print(f"[ToolBatch] stop notification unavailable: {type(exc).__name__}")
    return responses


__all__ = ["dispatch_tool_batch"]
