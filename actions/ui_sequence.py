"""Sequential UI actions with a verified postcondition after every step.

The sequence is deliberately narrow: it can invoke exact, visible,
uniquely named Button/Hyperlink/MenuItem controls, fill explicitly named safe
fields, expand tree items, or send one validated key chord to the pinned
application window. Every step needs an observable postcondition. It cannot
paste, execute scripts, or infer labels or expected results. A failed or
unverified step stops the remaining sequence.
"""
from __future__ import annotations

import threading
import time
import uuid

from core import outcome
from actions import ui_interaction as ui
from actions import ui_tree

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

        if action == "set_tree_item_expanded":
            allowed = {"action", "item_label", "expanded"}
            if set(raw) != allowed:
                return None, (
                    f"tree expansion step {index} must contain only action, item_label and expanded"
                )
            label = raw.get("item_label")
            desired = raw.get("expanded")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                return None, f"step {index} has a missing or overlong item_label"
            if ui._is_sensitive_input_label(label):
                return None, f"step {index} targets a credential, verification-code or payment control"
            if not isinstance(desired, bool):
                return None, f"step {index} expanded must be a boolean true or false"
            validated.append({
                "action": "set_tree_item_expanded",
                "item_label": " ".join(label.split()),
                "expanded": desired,
            })
            continue

        if action == "set_checkbox_state":
            allowed = {"action", "checkbox_label", "checked"}
            label, desired = raw.get("checkbox_label"), raw.get("checked")
            if set(raw) != allowed:
                return None, f"checkbox step {index} must contain only action, checkbox_label and checked"
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                return None, f"step {index} has a missing or overlong checkbox_label"
            if ui._is_sensitive_input_label(label) or not isinstance(desired, bool):
                return None, f"step {index} has an unsupported checkbox label or non-boolean checked state"
            validated.append({"action": action, "checkbox_label": " ".join(label.split()), "checked": desired})
            continue

        if action == "select_radio_option":
            allowed = {"action", "radio_label"}
            label = raw.get("radio_label")
            if set(raw) != allowed:
                return None, f"radio step {index} must contain only action and radio_label"
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                return None, f"step {index} has a missing or overlong radio_label"
            if ui._is_sensitive_input_label(label):
                return None, f"step {index} targets a sensitive radio option"
            validated.append({"action": action, "radio_label": " ".join(label.split())})
            continue

        if action == "select_dropdown_option":
            allowed = {"action", "combo_label", "option_label"}
            combo, option = raw.get("combo_label"), raw.get("option_label")
            if set(raw) != allowed:
                return None, f"dropdown step {index} must contain only action, combo_label and option_label"
            if (not isinstance(combo, str) or not combo.strip() or len(combo.strip()) > 120
                    or not isinstance(option, str) or not option.strip() or len(option.strip()) > 120):
                return None, f"step {index} needs exact combo_label and option_label values"
            if ui._is_sensitive_input_label(combo) or ui._is_sensitive_input_label(option):
                return None, f"step {index} targets a sensitive dropdown field or option"
            validated.append({"action": action, "combo_label": " ".join(combo.split()), "option_label": " ".join(option.split())})
            continue

        if action == "press_keys":
            allowed = {"action", "keys", "expected_result"}
            if set(raw) != allowed:
                return None, f"press_keys step {index} must contain only action, keys and expected_result"
            rendered, problem = _normalise_key_combo(raw.get("keys"))
            expected = raw.get("expected_result")
            if rendered is None:
                return None, f"step {index}: {problem}"
            if not isinstance(expected, str) or not expected.strip() or len(expected.strip()) > 160:
                return None, f"step {index} expected_result must contain 1 to 160 characters"
            validated.append({
                "action": "press_keys",
                "keys": rendered,
                "key_combo": "+".join("ctrl" if part.strip().lower() == "control" else part.strip().lower() for part in str(raw.get("keys")).split("+")),
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

        return None, f"step {index} action must be click, fill_text, press_keys or set_tree_item_expanded"

    return validated, ""


_KEY_NAMES = {
    "tab": "{TAB}", "enter": "{ENTER}", "return": "{ENTER}",
    "escape": "{ESC}", "esc": "{ESC}", "space": " ",
    "backspace": "{BACKSPACE}", "delete": "{DELETE}",
    "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
    "home": "{HOME}", "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}",
}
_DANGEROUS_KEY_COMBOS = {
    frozenset(("alt", "f4")), frozenset(("ctrl", "w")),
    frozenset(("ctrl", "q")), frozenset(("ctrl", "shift", "delete")),
    frozenset(("shift", "delete")), frozenset(("win", "l")),
}


def _normalise_key_combo(value: object) -> tuple[str | None, str]:
    """Accept one explicit key or modifier chord; never accept arbitrary text."""
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        return None, "keys must be one key or a short modifier combination"
    parts = [part.strip().lower() for part in value.split("+")]
    if not parts or any(not part for part in parts):
        return None, "key combination is malformed"
    modifiers = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}
    if len(parts) > 1:
        if any(part not in modifiers for part in parts[:-1]):
            return None, "only Ctrl, Alt and Shift modifiers are supported"
        canonical_modifiers = ["ctrl" if part == "control" else part for part in parts[:-1]]
        if len(set(canonical_modifiers)) != len(canonical_modifiers):
            return None, "a modifier cannot be repeated"
    key = parts[-1]
    if key in modifiers or not (
        key in _KEY_NAMES or len(key) == 1 and key.isalnum()
        or key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12
    ):
        return None, "unsupported key; use a named key, letter, digit or F1–F12"
    rendered_key = _KEY_NAMES.get(key, "{" + key.upper() + "}" if key.startswith("f") else key)
    prefix = "".join(modifiers[item] for item in parts[:-1])
    return prefix + rendered_key, ""


def _press_keys_and_verify(keys: str, expected: str,
                           window_handle: int, process_id: int) -> str:
    """Send one key chord only to the original foreground process, then verify."""
    try:
        from pywinauto import Desktop, keyboard
        import ctypes
        window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
        if not window.is_visible():
            return outcome.failed("run_ui_sequence", "the original application window is no longer visible")
        if int(getattr(window.element_info, "process_id", 0)) != int(process_id):
            return outcome.failed("run_ui_sequence", "the original window now belongs to another process")
        before = ui._snapshot(window_handle, process_id)
        if ui._has_exact_expected_text(before["records"], expected):
            return outcome.failed(
                "run_ui_sequence", "the exact expected result is already visible; no key was sent",
                expected=expected,
            )
        window.set_focus()
        time.sleep(0.12)
        foreground = int(ctypes.windll.user32.GetForegroundWindow())
        foreground_pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
        if int(foreground_pid.value) != int(process_id):
            return outcome.failed(
                "run_ui_sequence", "Windows did not focus the original application; no key was sent",
            )
        keyboard.send_keys(keys, with_spaces=True, pause=0.02)
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            snapshot = ui._snapshot(window_handle, process_id)
            if ui._has_exact_expected_text(snapshot["records"], expected):
                return outcome.done(
                    "run_ui_sequence", keys=keys, expected=expected, verified=True,
                )
            time.sleep(0.2)
        return outcome.unverified(
            "run_ui_sequence",
            "the key was sent to the original application but its expected result was not observed",
            keys=keys, expected=expected,
        )
    except Exception as exc:
        return outcome.failed(
            "run_ui_sequence",
            f"keyboard action failed ({type(exc).__name__}); verify the application before retrying",
        )


def _prepare_step(step: dict, snapshot: dict,
                  user_request: str = "") -> tuple[dict | None, str]:
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
    from core.autonomy_policy import requires_ui_confirmation
    return {
        "label": label,
        "expected": expected,
        "handle": snapshot["handle"],
        "process_id": snapshot["process_id"],
        "signature": signature,
        "requires_confirmation": requires_ui_confirmation(label, user_request=user_request),
    }, ""


def _run_pattern_step(action: str, worker, args: tuple,
                       success_statuses: set[str]) -> str:
    """Run one pinned-window UIA pattern operation with the shared bounded worker."""
    try:
        completed, result, error = ui._run_uia(
            lambda cancelled: worker(*args, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified(action, "the control may have changed; inspect it before retrying")
        if not isinstance(result, tuple) or len(result) != 2:
            return outcome.failed(action, "UI Automation returned an invalid result")
        status, reason = result
        if status in success_statuses:
            return outcome.done(action, verified=True, changed=status not in {"already_set", "already_selected"})
        if status in {"uncertain", "unverified"}:
            return outcome.unverified(action, reason)
        if status in {"busy", "cancelled", "ambiguous_checkbox", "ambiguous_option",
                      "ambiguous_combo", "ambiguous_item", "missing_option"}:
            return outcome.ask(action, reason or status)
        return outcome.failed(action, reason or status)
    except Exception as exc:
        return outcome.failed(action, f"the UI Automation operation failed ({type(exc).__name__})")


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
    # An ASK result is only accurate when no earlier step has happened.
    # Once any prior step was verified, the whole sequence is partial and must
    # be reported as unverified, never as "nothing was done".
    if completed:
        return outcome.unverified(
            "run_ui_sequence",
            f"the sequence stopped after {completed} verified step(s); {reason}",
            **facts,
        )
    if result.startswith("status=ask "):
        return outcome.ask("run_ui_sequence", reason, **facts)
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
                        "another action is waiting for HUD confirmation; this sequence paused without starting another UI action",
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
            if step["action"] in {"set_checkbox_state", "select_radio_option", "select_dropdown_option"}:
                action = step["action"]
                if action == "set_checkbox_state":
                    from actions import ui_checkbox
                    label = step["checkbox_label"]
                    worker = ui_checkbox._set_on_window
                    args = (state["window_handle"], state["process_id"], label, step["checked"])
                    needs_confirmation = ui_checkbox._needs_confirmation(label, step["checked"], state.get("session_memory"))
                    success = {"changed", "already_set"}
                    progress_label = label
                    title = f"Confirm checkbox: {label}"
                    detail = f"MARK will set '{label}' to {'checked' if step['checked'] else 'unchecked'}. It will not submit or save the form."
                elif action == "select_radio_option":
                    from actions import ui_radio, ui_checkbox
                    label = step["radio_label"]
                    worker = ui_radio._select_on_window
                    args = (state["window_handle"], state["process_id"], label)
                    needs_confirmation = ui_checkbox._needs_confirmation(label)
                    success = {"selected", "already_selected"}
                    progress_label = label
                    title = f"Confirm option: {label}"
                    detail = f"MARK will select '{label}'. It will not submit or save the form."
                else:
                    from actions import ui_dropdown
                    combo, option = step["combo_label"], step["option_label"]
                    worker = ui_dropdown._select_worker
                    args = (state["window_handle"], state["process_id"], combo, option)
                    needs_confirmation = ui._requires_confirmation(combo, option)
                    success = {"selected", "already_selected"}
                    progress_label = f"{combo}: {option}"
                    title = f"Confirm selection: {option}"
                    detail = f"MARK will select '{option}' in '{combo}'. It will not submit or save the form."
                def perform_pattern_step():
                    result = _run_pattern_step(action, worker, args, success)
                    if not result.startswith("status=done "):
                        return _stop_with_step_result(state, index, result)
                    state["completed"] += 1
                    state["completed_labels"].append(progress_label)
                    return _continue_sequence(state, index + 1)
                if needs_confirmation:
                    return confirm.request(
                        key=f"run_ui_sequence.{action}", title=title, detail=detail,
                        run=perform_pattern_step,
                        on_cancel=lambda: _release_sequence(state["id"]),
                    )
                result = _run_pattern_step(action, worker, args, success)
                if not result.startswith("status=done "):
                    return _stop_with_step_result(state, index, result)
                state["completed"] += 1
                state["completed_labels"].append(progress_label)
                continue

            if step["action"] == "press_keys":
                dangerous = frozenset(step["key_combo"].split("+")) in _DANGEROUS_KEY_COMBOS
                if dangerous:
                    return confirm.request(
                        key="run_ui_sequence.press_keys",
                        title=f"Confirm keyboard shortcut: {step['key_combo']}",
                        detail=(
                            f"MARK will send {step['key_combo']} to the original application window. "
                            f"It may close a tab or app, delete data, or lock Windows. "
                            f"Expected visible result: {step['expected_result']}. Confirm only if intended."
                        ),
                        run=lambda: _resume_after_key_confirmation(state, index, step),
                        on_cancel=lambda: _release_sequence(state["id"]),
                    )
                result = _press_keys_and_verify(
                    step["keys"], step["expected_result"],
                    state["window_handle"], state["process_id"],
                )
                if not result.startswith("status=done "):
                    return _stop_with_step_result(state, index, result)
                state["completed"] += 1
                state["completed_labels"].append(step["key_combo"])
                continue

            if step["action"] == "set_tree_item_expanded":
                label = step["item_label"]
                # Expanding/collapsing a tree node is reversible view navigation.
                # It never activates the item and therefore does not need a prompt.
                result = ui_tree._set_tree_item_expanded_in_window(
                    label, step["expanded"],
                    state["window_handle"], state["process_id"],
                )
                if not result.startswith("status=done "):
                    return _stop_with_step_result(state, index, result)
                state["completed"] += 1
                state["completed_labels"].append(label)
                if index == 0:
                    state.pop("initial_snapshot", None)
                continue

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
            prepared, problem = _prepare_step(
                step, snapshot, state.get("user_request", ""),
            )
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
            verified=True,
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


def _resume_after_key_confirmation(state: dict, index: int,
                                    step: dict) -> str:
    """Run an approved key chord against the pinned window, then continue."""
    try:
        result = _press_keys_and_verify(
            step["keys"], step["expected_result"],
            state["window_handle"], state["process_id"],
        )
        if not result.startswith("status=done "):
            return _stop_with_step_result(state, index, result)
        state["completed"] += 1
        state["completed_labels"].append(step["key_combo"])
        return _continue_sequence(state, index + 1)
    except Exception as exc:
        _release_sequence(state["id"])
        return outcome.failed(
            "run_ui_sequence",
            f"confirmed keyboard action failed ({type(exc).__name__}); later steps were not started",
            sequence_id=state["id"], failed_step=index + 1,
        )


def _resume_tree_after_confirmation(state: dict, index: int,
                                    step: dict) -> str:
    """Run an approved tree action against the original HWND/PID, then continue."""
    try:
        result = ui_tree._set_tree_item_expanded_in_window(
            step["item_label"], step["expanded"],
            state["window_handle"], state["process_id"],
        )
        if not result.startswith("status=done "):
            return _stop_with_step_result(state, index, result)
        state["completed"] += 1
        state["completed_labels"].append(step["item_label"])
        if index == 0:
            state.pop("initial_snapshot", None)
        return _continue_sequence(state, index + 1)
    except Exception as exc:
        _release_sequence(state["id"])
        return outcome.failed(
            "run_ui_sequence",
            f"the confirmed tree action failed internally ({type(exc).__name__}); later steps were not started",
            sequence_id=state["id"],
            steps_completed=state["completed"],
            total_steps=len(state["steps"]),
            failed_step=index + 1,
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


def _capture_active_window() -> dict:
    return ui._snapshot()


def _start_sequence(steps: list[dict], initial_snapshot: dict,
                   user_request: str = "") -> str:
    token = _claim_sequence()
    if token is None:
        return outcome.ask(
            "run_ui_sequence",
            "another UI sequence is already running or waiting for confirmation; wait for it to finish",
        )
    state = {
        "id": token,
        "steps": steps,
        "session_memory": session_memory,
        "started_at": time.monotonic(),
        "completed": 0,
        "completed_labels": [],
        "initial_snapshot": initial_snapshot,
        "window_handle": initial_snapshot["handle"],
        "process_id": initial_snapshot["process_id"],
        "user_request": user_request[:4000],
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
    from core.autonomy_policy import scope_request
    return _start_sequence(steps, snapshot, scope_request(session_memory))


TOOL = {
    "name": "run_ui_sequence",
    "tier": "core",
    "brief": "Run verified 2–8 step Windows workflows with exact clicks, text entry, checkboxes, radio/dropdown choices, tree navigation and keyboard shortcuts.",
    "description": (
        "Use only when the user explicitly asks MARK to complete a multi-step "
        "workflow in the currently active Windows application. Supply 2 to 8 "
        "ordered steps. Checkbox steps set an exact visible checkbox to a requested boolean state; radio steps select one exact visible option; dropdown steps select one exact visible option from an already-open ComboBox. These use native UI Automation state patterns and verify the result. They never submit or save a form, and existing high-impact confirmation rules still apply. A press_keys step must supply one key or modifier chord and an exact visible postcondition. A set_tree_item_expanded step must supply one exact visible "
        "TreeItem label and a boolean expanded value; MARK verifies the ExpandCollapsePattern "
        "state in the original window before starting the next step. It expands or collapses "
        "only the node and never activates a child item. A press_keys step is refocused to the original "
        "window and sends only one validated key or Ctrl/Alt/Shift chord; it proceeds only after the exact "
        "expected accessible text appears. Dangerous close/delete/lock shortcuts require HUD confirmation. "
        "A click step must name one exact visible Button, "
        "Hyperlink or MenuItem label and the exact distinctive accessible "
        "element text that should newly appear after that click. A fill_text "
        "step must name one exact accessible, non-sensitive Edit field and "
        "the literal text to enter; MARK verifies it locally with UI Automation "
        "ValuePattern without returning the field value to the model. Never "
        "fill credentials, verification codes, payment details, keys or tokens. "
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
                "description": "Two to eight ordered steps. Use click, fill_text, press_keys with one key/chord and expected_result, set_checkbox_state with an exact label and boolean, select_radio_option with an exact label, select_dropdown_option with exact ComboBox and visible option labels, or set_tree_item_expanded.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "enum": ["click", "fill_text", "press_keys", "set_tree_item_expanded", "set_checkbox_state", "select_radio_option", "select_dropdown_option"],
                            "description": "Use click, fill_text, press_keys, set_tree_item_expanded, set_checkbox_state, select_radio_option or select_dropdown_option with the exact fields required by that action.",
                        },
                        "button_label": {
                            "type": "STRING",
                            "description": "For click: exact accessible label of one visible button, link or menu item.",
                        },
                        "expected_result": {
                            "type": "STRING",
                            "description": "For click: exact accessible name of a distinctive UI element that should newly appear. For press_keys: exact distinctive accessible text that must appear after the key is sent.",
                        },
                        "keys": {
                            "type": "STRING",
                            "description": "For press_keys: one key such as Tab/Enter or one Ctrl/Alt/Shift chord such as Ctrl+S; no arbitrary text.",
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
                            "description": "For set_checkbox_state: exact accessible label of one visible non-sensitive CheckBox.",
                        },
                        "checked": {
                            "type": "BOOLEAN",
                            "description": "For set_checkbox_state: true to check it, false to uncheck it.",
                        },
                        "radio_label": {
                            "type": "STRING",
                            "description": "For select_radio_option: exact accessible label of one visible non-sensitive RadioButton.",
                        },
                        "combo_label": {
                            "type": "STRING",
                            "description": "For select_dropdown_option: exact accessible ComboBox label.",
                        },
                        "option_label": {
                            "type": "STRING",
                            "description": "For select_dropdown_option: exact visible ListItem option in the already-open dropdown.",
                        },
                        "item_label": {
                            "type": "STRING",
                            "description": "For set_tree_item_expanded: exact accessible label of one visible, enabled, non-sensitive Windows TreeItem.",
                        },
                        "expanded": {
                            "type": "BOOLEAN",
                            "description": "For set_tree_item_expanded: true to expand the item; false to collapse it.",
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
