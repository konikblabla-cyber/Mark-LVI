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

        action = raw.get("action", "click")
        if action == "click":
            allowed = {"button_label", "expected_result"}
            if set(raw) not in (allowed, allowed | {"action"}):
                return None, (
                    f"click step {index} must contain only button_label and expected_result"
                )
            label = raw.get("button_label")
            expected = raw.get("expected_result")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                return None, f"step {index} has a missing or overlong button_label"
            if (not isinstance(expected, str) or not expected.strip()
                    or len(expected.strip()) > 160):
                return None, f"step {index} has a missing or overlong expected_result"
            validated.append({
                "action": "click",
                "button_label": " ".join(label.split()),
                "expected_result": " ".join(expected.split()),
            })
            continue

        if action == "fill_text":
            allowed = {"action", "field_label", "text"}
            if set(raw) != allowed:
                return None, (
                    f"fill_text step {index} must contain only action, field_label and text"
                )
            label = raw.get("field_label")
            value = raw.get("text")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                return None, f"step {index} has a missing or overlong field_label"
            if not isinstance(value, str) or not value or len(value) > 4000 or "\x00" in value:
                return None, f"step {index} text must contain 1 to 4000 characters and no NUL bytes"
            validated.append({
                "action": "fill_text",
                "field_label": " ".join(label.split()),
                "text": value,
            })
            continue

        if action == "set_checkbox":
            allowed = {"action", "checkbox_label", "checked"}
            if set(raw) != allowed:
                return None, (
                    f"set_checkbox step {index} must contain only action, checkbox_label and checked"
                )
            label = raw.get("checkbox_label")
            checked = raw.get("checked")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                return None, f"step {index} has a missing or overlong checkbox_label"
            if not isinstance(checked, bool):
                return None, f"step {index} checked must be a boolean true or false"
            if ui._is_sensitive_input_label(label):
                return None, f"step {index} targets a credential-related checkbox, which is not supported"
            validated.append({
                "action": "set_checkbox",
                "checkbox_label": " ".join(label.split()),
                "checked": checked,
            })
            continue

        return None, f"step {index} action must be click, fill_text or set_checkbox"

    return validated, ""


def _prepare_step(step: dict, snapshot: dict) -> tuple[dict | None, str]:
    label = step["button_label"]
    expected = step["expected_result"]

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


def _prepare_checkbox_step(step: dict, state: dict) -> tuple[dict | None, str]:
    label = step["checkbox_label"]
    desired = step["checked"]
    status, current, signature, reason = ui._inspect_checkbox(
        label, state["window_handle"], state["process_id"],
    )
    if status != "found":
        if status == "ambiguous_checkbox":
            return None, outcome.ask(
                "run_ui_sequence", reason, checkbox=label,
            )
        if status == "timeout":
            return None, outcome.unverified(
                "run_ui_sequence", reason, checkbox=label,
            )
        return None, outcome.failed(
            "run_ui_sequence", reason or status, checkbox=label,
        )
    return {
        "label": label,
        "desired": desired,
        "current": current,
        "signature": signature,
        "handle": state["window_handle"],
        "process_id": state["process_id"],
    }, ""


def _stop_with_step_result(state: dict, index: int, result: str) -> str:
    completed = state["completed"]
    facts = {
        "sequence_id": state["id"],
        "steps_completed": completed,
        "total_steps": len(state["steps"]),
        "failed_step": index + 1,
        "step_status": result.split(" ", 1)[0],
        "step_detail": result[:280],
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
        from core import confirm
        for index in range(start_index, len(state["steps"])):
            pending = confirm.pending_title()
            if pending:
                return _stop_with_step_result(
                    state, index,
                    outcome.ask(
                        "run_ui_sequence",
                        "another action is waiting for HUD confirmation; this sequence paused without clicking",
                        pending_title=pending,
                    ),
                )
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
            if step["action"] == "set_checkbox":
                prepared, problem = _prepare_checkbox_step(step, state)
                if prepared is None:
                    return _stop_with_step_result(state, index, problem)
                if prepared["current"] == prepared["desired"]:
                    state["completed"] += 1
                    state["completed_labels"].append(prepared["label"])
                    if index == 0:
                        state.pop("initial_snapshot", None)
                    continue

                # Every checkbox state change requires a human HUD approval.
                # This intentionally covers consent, subscription and privacy
                # switches even when a label-specific heuristic misses them.
                label = prepared["label"]
                desired_text = "checked" if prepared["desired"] else "unchecked"
                return confirm.request(
                    key="run_ui_sequence",
                    title=f"Confirm checkbox: {label}",
                    detail=(
                        f"This is step {index + 1} of {len(state['steps'])}. "
                        f"MARK will set the '{label}' checkbox to {desired_text} "
                        "in the original application window, then verify its "
                        "TogglePattern state. No other steps run unless this "
                        "change is confirmed and verified."
                    ),
                    run=lambda: _resume_after_checkbox(state, index, prepared),
                    on_cancel=lambda: _release_sequence(state["id"]),
                )

            if step["action"] == "fill_text":
                # Filling uses the exact original HWND/PID directly and does
                # not enumerate or return editable-field contents.
                result = ui._fill_text_and_verify(
                    step["field_label"], step["text"],
                    state["window_handle"], state["process_id"],
                )
                if not result.startswith("status=done "):
                    return _stop_with_step_result(state, index, result)
                state["completed"] += 1
                state["completed_labels"].append(step["field_label"])
                if index == 0:
                    state.pop("initial_snapshot", None)
                continue

            if index == 0 and state.get("initial_snapshot") is not None:
                snapshot = state.pop("initial_snapshot")
            else:
                try:
                    # Keep the sequence pinned to the application window
                    # captured at start. A foreground change must not redirect
                    # the remaining clicks into another application.
                    snapshot = ui._snapshot(
                        state["window_handle"], state["process_id"],
                    )
                except Exception as exc:
                    return _stop_with_step_result(
                        state, index,
                        outcome.failed(
                            "run_ui_sequence",
                            f"the original application window could not be inspected ({type(exc).__name__})",
                        ),
                    )
            prepared, problem = _prepare_step(step, snapshot)
            if prepared is None:
                return _stop_with_step_result(state, index, problem)

            if prepared["requires_confirmation"]:
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


def _resume_after_checkbox(state: dict, index: int,
                               prepared: dict) -> str:
    """Apply one approved checkbox state and continue only after readback."""
    try:
        result = ui._set_checkbox_and_verify(
            prepared["label"], prepared["desired"],
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
            f"the approved checkbox step failed internally ({type(exc).__name__}); later steps were not started",
            sequence_id=state["id"],
            steps_completed=state["completed"],
            total_steps=len(state["steps"]),
            failed_step=index + 1,
        )


def _capture_active_window() -> dict:
    return ui._snapshot()


def _start_sequence(steps: list[dict], initial_snapshot: dict) -> str:
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
        "initial_snapshot": initial_snapshot,
        "window_handle": initial_snapshot["handle"],
        "process_id": initial_snapshot["process_id"],
    }
    return _continue_sequence(state, 0)


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

    try:
        snapshot = _capture_active_window()
    except Exception as exc:
        return outcome.failed(
            "run_ui_sequence",
            f"could not identify the active application window ({type(exc).__name__})",
        )
    return _start_sequence(steps, snapshot)


TOOL = {
    "name": "run_ui_sequence",
    "tier": "core",
    "brief": "Fill safe fields, set confirmed checkboxes and click verified controls in a 2–8 step Windows UI workflow.",
    "description": (
        "Use only when the user explicitly asks MARK to complete a multi-step "
        "workflow in the currently active Windows application. Supply 2 to 8 "
        "ordered steps. A click step must name one exact visible Button, "
        "Hyperlink or MenuItem label and the exact distinctive accessible "
        "element text that should newly appear after that click. A fill_text "
        "step must name one exact accessible, non-sensitive Edit field and "
        "the literal text to enter; MARK verifies it locally with UI Automation "
        "ValuePattern without returning the field value to the model. Never "
        "fill credentials, verification codes, payment details, keys or tokens. "
        "A set_checkbox step must name one exact accessible CheckBox and desired "
        "boolean state; every state change pauses for human HUD approval, then "
        "MARK re-reads TogglePattern locally to verify the requested state. "
        "Do not invent labels or expected results; inspect the UI first when "
        "needed. The sequence "
        "captures the active application window once and remains pinned to "
        "that same window and process for every step, invokes one unique "
        "control using local Windows UI Automation, and verifies that "
        "step's exact postcondition before starting the next. It stops on the "
        "first failed, ambiguous, unverified or unobservable step; it never "
        "blindly continues. Potentially high-impact steps pause for the human "
        "HUD confirmation, and remaining steps resume only after that step is "
        "verified. Cancellation or expired confirmation releases the sequence. "
        "Text entry is limited to explicitly requested non-sensitive Edit "
        "fields; it does not read or return existing field contents. It never "
        "uses screenshots, coordinates, clipboard, scripts, background polling "
        "or extra model/API "
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
                "description": "Two to eight ordered steps. Use click, fill_text or set_checkbox with the exact fields required for that action.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "enum": ["click", "fill_text", "set_checkbox"],
                            "description": "Use click for a verified button press, fill_text for one safe text field, or set_checkbox for a human-confirmed checkbox state change.",
                        },
                        "button_label": {
                            "type": "STRING",
                            "description": "For click: exact accessible label of one visible button, link or menu item.",
                        },
                        "expected_result": {
                            "type": "STRING",
                            "description": "For click: exact accessible name of a distinctive UI element that should newly appear after the click.",
                        },
                        "field_label": {
                            "type": "STRING",
                            "description": "For fill_text: exact accessible label of one visible, non-sensitive Edit field.",
                        },
                        "text": {
                            "type": "STRING",
                            "description": "For fill_text: literal non-secret text to enter; never use for credentials or payment details.",
                        },
                        "checkbox_label": {
                            "type": "STRING",
                            "description": "For set_checkbox: exact accessible label of one visible CheckBox.",
                        },
                        "checked": {
                            "type": "BOOLEAN",
                            "description": "For set_checkbox: desired checked state; every change requires HUD confirmation.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["steps"],
    },
    "handler": run_ui_sequence,
}
