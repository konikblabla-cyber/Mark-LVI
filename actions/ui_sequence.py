"""Sequential UI actions with a verified postcondition after every step.

The sequence is deliberately narrow: it can invoke only exact, visible,
uniquely named Button/Hyperlink/MenuItem controls through the safeguards in
actions.ui_interaction. It cannot type, paste, execute scripts, or infer labels
or expected results. A failed or unverified step stops the remaining sequence.
"""
from __future__ import annotations

import threading
import time
import uuid

from core import outcome
from actions import ui_interaction as ui

_MAX_STEPS = 8
_MAX_SEQUENCE_SECONDS = 600.0
_SEQUENCE_STATE_LOCK = threading.Lock()
_ACTIVE_SEQUENCE_ID: str | None = None


def _claim_sequence() -> str | None:
    global _ACTIVE_SEQUENCE_ID
    with _SEQUENCE_STATE_LOCK:
        if _ACTIVE_SEQUENCE_ID is not None:
            return None
        token = uuid.uuid4().hex
        _ACTIVE_SEQUENCE_ID = token
        return token


def _release_sequence(token: str) -> None:
    global _ACTIVE_SEQUENCE_ID
    with _SEQUENCE_STATE_LOCK:
        if _ACTIVE_SEQUENCE_ID == token:
            _ACTIVE_SEQUENCE_ID = None


def _validate_steps(raw_steps) -> tuple[list[dict] | None, str]:
    if not isinstance(raw_steps, list):
        return None, "steps must be an array"
    if not 2 <= len(raw_steps) <= _MAX_STEPS:
        return None, f"a UI sequence must contain 2 to {_MAX_STEPS} steps"

    validated = []
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            return None, f"step {index} must be an object"
        if set(raw) != {"button_label", "expected_result"}:
            return None, (
                f"step {index} must contain only button_label and expected_result"
            )
        label = raw.get("button_label")
        expected = raw.get("expected_result")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
            return None, f"step {index} has a missing or overlong button_label"
        if (not isinstance(expected, str) or not expected.strip()
                or len(expected.strip()) > 160):
            return None, f"step {index} has a missing or overlong expected_result"
        validated.append({
            "button_label": " ".join(label.split()),
            "expected_result": " ".join(expected.split()),
        })
    return validated, ""


def _prepare_step(step: dict) -> tuple[dict | None, str]:
    label = step["button_label"]
    expected = step["expected_result"]
    try:
        snapshot = ui._snapshot()
    except Exception as exc:
        return None, outcome.failed(
            "run_ui_sequence",
            f"could not inspect the active application before this step ({type(exc).__name__})",
            button=label,
        )

    if ui._has_exact_expected_text(snapshot["records"], expected):
        return None, outcome.failed(
            "run_ui_sequence",
            "the exact expected result is already visible; this step cannot prove a new effect",
            button=label, expected=expected,
        )

    matches = ui._matching_controls(snapshot["clickables"], label)
    if not matches:
        return None, outcome.failed(
            "run_ui_sequence",
            "no visible, enabled control has that exact label in the active window",
            button=label,
        )
    if len(matches) != 1:
        return None, outcome.ask(
            "run_ui_sequence",
            "more than one visible control has that exact label; nothing was clicked",
            button=label, matches=len(matches),
        )

    item = matches[0]
    signature = {
        "role": item["role"],
        "automation_id": item.get("automation_id", ""),
        "runtime_id": item.get("runtime_id", ()),
        "rect": item.get("rect"),
    }
    context_text = " ".join(
        ui._safe_name(record.get("name")) for record in snapshot["records"]
    )
    return {
        "label": label,
        "expected": expected,
        "handle": snapshot["handle"],
        "process_id": snapshot["process_id"],
        "signature": signature,
        "requires_confirmation": ui._requires_confirmation(
            label, expected, context_text,
        ),
    }, ""


def _stop_with_step_result(state: dict, index: int, result: str) -> str:
    completed = state["completed"]
    facts = {
        "sequence_id": state["id"],
        "steps_completed": completed,
        "total_steps": len(state["steps"]),
        "failed_step": index + 1,
        "step_status": result.split(" ", 1)[0],
    }
    _release_sequence(state["id"])
    reason = (
        f"step {index + 1} was not verified; all later steps were skipped. "
        "Inspect the application before deciding whether to retry."
    )
    if result.startswith("status=ask "):
        return outcome.ask(
            "run_ui_sequence",
            reason,
            **facts,
        )
    if result.startswith("status=unverified "):
        return outcome.unverified(
            "run_ui_sequence",
            reason,
            **facts,
        )
    return outcome.failed(
        "run_ui_sequence",
        reason,
        **facts,
    )


def _continue_sequence(state: dict, start_index: int) -> str:
    try:
        for index in range(start_index, len(state["steps"])):
            if time.monotonic() - state["started_at"] > _MAX_SEQUENCE_SECONDS:
                _release_sequence(state["id"])
                return outcome.unverified(
                    "run_ui_sequence",
                    "the sequence exceeded its 10-minute safety limit; remaining steps were not started",
                    sequence_id=state["id"],
                    steps_completed=state["completed"],
                    total_steps=len(state["steps"]),
                )

            step = state["steps"][index]
            prepared, problem = _prepare_step(step)
            if prepared is None:
                return _stop_with_step_result(state, index, problem)

            if prepared["requires_confirmation"]:
                from core import confirm
                pending = confirm.pending_title()
                if pending:
                    return _stop_with_step_result(
                        state, index,
                        outcome.ask(
                            "run_ui_sequence",
                            "another action is already waiting for HUD confirmation",
                            pending_title=pending,
                        ),
                    )

                label = prepared["label"]
                expected = prepared["expected"]
                return confirm.request(
                    key="run_ui_sequence",
                    title=f"MARK sequence {index + 1}/{len(state['steps'])}: {label}",
                    detail=(
                        f"This is step {index + 1} of {len(state['steps'])}. "
                        f"MARK will invoke '{label}' in the application window "
                        f"identified now, then verify the exact accessible text "
                        f"'{expected}'. Later steps will run only if this step "
                        "is verified. Confirm only if this action is intended."
                    ),
                    run=lambda: _resume_after_confirmation(
                        state, index, prepared,
                    ),
                    on_cancel=lambda: _release_sequence(state["id"]),
                )

            result = ui._click_and_verify(
                prepared["label"], prepared["expected"],
                prepared["handle"], prepared["process_id"],
                prepared["signature"],
            )
            if not result.startswith("status=done "):
                return _stop_with_step_result(state, index, result)

            state["completed"] += 1
            state["completed_labels"].append(prepared["label"])

        _release_sequence(state["id"])
        return outcome.done(
            "run_ui_sequence",
            sequence_id=state["id"],
            steps_completed=state["completed"],
            total_steps=len(state["steps"]),
        )
    except Exception as exc:
        _release_sequence(state["id"])
        return outcome.failed(
            "run_ui_sequence",
            f"the sequence stopped after an internal error ({type(exc).__name__}); later steps were not started",
            sequence_id=state["id"],
            steps_completed=state["completed"],
            total_steps=len(state["steps"]),
        )


def _resume_after_confirmation(state: dict, index: int,
                               prepared: dict) -> str:
    """Run the approved step, then continue only after verified success."""
    try:
        result = ui._click_and_verify(
            prepared["label"], prepared["expected"],
            prepared["handle"], prepared["process_id"],
            prepared["signature"],
        )
        if not result.startswith("status=done "):
            return _stop_with_step_result(state, index, result)
        state["completed"] += 1
        state["completed_labels"].append(prepared["label"])
        return _continue_sequence(state, index + 1)
    except Exception as exc:
        _release_sequence(state["id"])
        return outcome.failed(
            "run_ui_sequence",
            f"the approved step failed internally ({type(exc).__name__}); later steps were not started",
            sequence_id=state["id"],
            steps_completed=state["completed"],
            total_steps=len(state["steps"]),
            failed_step=index + 1,
        )


def run_ui_sequence(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    steps, error = _validate_steps(params.get("steps"))
    if steps is None:
        return outcome.failed("run_ui_sequence", error)

    from core import confirm
    pending = confirm.pending_title()
    if pending:
        return outcome.ask(
            "run_ui_sequence",
            "another action is waiting for HUD confirmation; finish or cancel it first",
            pending_title=pending,
        )

    token = _claim_sequence()
    if token is None:
        return outcome.ask(
            "run_ui_sequence",
            "another UI sequence is already running or waiting for confirmation; wait for it to finish",
        )

    state = {
        "id": token,
        "steps": steps,
        "started_at": time.monotonic(),
        "completed": 0,
        "completed_labels": [],
    }
    return _continue_sequence(state, 0)


TOOL = {
    "name": "run_ui_sequence",
    "tier": "core",
    "brief": "Run 2–8 exact UI clicks sequentially, verifying each before continuing.",
    "description": (
        "Use only when the user explicitly asks MARK to complete a multi-step "
        "workflow in the currently active Windows application. Supply 2 to 8 "
        "ordered steps. Each step must name one exact visible Button, Hyperlink "
        "or MenuItem label and the exact distinctive accessible element text "
        "that should newly appear after that click. Do not invent labels or "
        "expected results; inspect the UI first when needed. The sequence "
        "re-inspects the active application before every step, invokes one "
        "unique control using local Windows UI Automation, and verifies that "
        "step's exact postcondition before starting the next. It stops on the "
        "first failed, ambiguous, unverified or unobservable step; it never "
        "blindly continues. Potentially high-impact steps pause for the human "
        "HUD confirmation, and remaining steps resume only after that step is "
        "verified. Cancellation or expired confirmation releases the sequence. "
        "This action does not type text, read editable fields, use screenshots, "
        "coordinates, clipboard, scripts, background polling or extra model/API "
        "calls. Only progress/status is returned to the conversation; the UIA "
        "tree remains local. Progress is held in memory only and is not resumed "
        "after MARK restarts. Never use for payments, purchases, account deletion "
        "or other critical operations unless the user explicitly requested that "
        "specific action; human confirmation is still required."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "steps": {
                "type": "ARRAY",
                "description": "Two to eight ordered button-click steps; every step requires an exact label and a newly appearing exact result.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "button_label": {
                            "type": "STRING",
                            "description": "Exact accessible label of one visible button, link or menu item.",
                        },
                        "expected_result": {
                            "type": "STRING",
                            "description": "Exact accessible name of a distinctive UI element that should newly appear after this click.",
                        },
                    },
                    "required": ["button_label", "expected_result"],
                },
            },
        },
        "required": ["steps"],
    },
    "handler": run_ui_sequence,
}
