"""Set one exact Windows UIA Slider value through a verified RangeValuePattern."""
from __future__ import annotations

import time

from core import outcome
from actions import ui_interaction as ui
from actions import ui_spinner

_parse_target = ui_spinner._parse_target
_decimal_property = ui_spinner._decimal_property


def _range_snapshot(pattern):
    try:
        if bool(pattern.CurrentIsReadOnly):
            return "readonly", "the slider is read-only", None, None, None
        current = _decimal_property(pattern.CurrentValue)
        minimum = _decimal_property(pattern.CurrentMinimum)
        maximum = _decimal_property(pattern.CurrentMaximum)
    except Exception:
        return "unsupported", "the Slider does not expose a complete RangeValuePattern", None, None, None
    if None in (current, minimum, maximum):
        return "unsupported", "the Slider returned a non-finite or unreadable numeric range", None, None, None
    if minimum > maximum or current < minimum or current > maximum:
        return "unsupported", "the Slider range or current value is invalid", None, None, None
    return "ok", "", current, minimum, maximum


def _set_and_verify(pattern, target, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the Slider"
    status, reason, current, minimum, maximum = _range_snapshot(pattern)
    if status != "ok":
        return status, reason
    if target < minimum or target > maximum:
        return "out_of_range", "the requested value is outside the Slider's current minimum and maximum"
    if current == target:
        return "already_set", ""
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the Slider"
    try:
        pattern.SetValue(float(target))
    except Exception:
        return "uncertain", "the Slider setter raised an error; its value may have changed"
    deadline = time.monotonic() + 2.0
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after setting; inspect the Slider before retrying"
        try:
            actual = _decimal_property(pattern.CurrentValue)
        except Exception:
            actual = None
        if actual == target:
            return "set", ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unverified", "the Slider did not report the exact requested value after setting"
        time.sleep(min(ui._VERIFY_INTERVAL, remaining))


def _set_on_window(window_handle, process_id, slider_label, target, cancelled):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", "the original window handle changed"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the original window now belongs to another process"

    wanted = ui._normalise(slider_label)
    matches = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", "the operation expired before Slider selection"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "Slider":
                continue
            if bool(getattr(info, "is_password", False)) or bool(getattr(info, "is_offscreen", False)):
                continue
            name = str(getattr(info, "name", "") or "")
            if ui._is_sensitive_input_label(name) or ui._normalise(name) != wanted:
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0 or not control.is_enabled():
                continue
            matches.append(control)
        except Exception:
            continue

    if not matches:
        return "missing_slider", "no visible, enabled, non-sensitive Slider has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_slider", "more than one Slider has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the Slider"
    try:
        pattern = matches[0].iface_range_value
    except Exception:
        return "unsupported", "the Slider does not expose RangeValuePattern"
    return _set_and_verify(pattern, target, cancelled)


def set_slider_value(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("field_label") or "").split())
    if not label or len(label) > 120:
        return outcome.failed("set_slider_value", "field_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("set_slider_value", "credential, verification-code and payment controls are not supported", field=label)
    target = _parse_target(params.get("value"))
    if target is None:
        return outcome.failed("set_slider_value", "value must be a finite decimal string with up to 11 integer and 6 fractional digits", field=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("set_slider_value", f"could not identify the active application ({type(exc).__name__})")

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: _set_on_window(handle, process_id, label, target, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified("set_slider_value", "the Slider may have changed; inspect it before retrying", field=label)
        if isinstance(result, tuple):
            status, reason = result
            if status in {"busy", "cancelled"}:
                return outcome.ask("set_slider_value", reason, field=label)
        else:
            status, reason = "", "UI Automation returned an invalid result"
        if status in {"set", "already_set"}:
            return outcome.done("set_slider_value", field=label, verified=True, changed=status == "set")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("set_slider_value", reason, field=label)
        if status == "ambiguous_slider":
            return outcome.ask("set_slider_value", reason, field=label)
        return outcome.failed("set_slider_value", reason or status, field=label)

    if ui._requires_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("set_slider_value", "another confirmation is already waiting on the HUD", field=label)
        return confirm.request(
            key="set_slider_value",
            title=f"Confirm value for {label}",
            detail=f"MARK will set '{label}' to {params['value']}. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "set_slider_value",
    "tier": "extra",
    "brief": "Set one exact Windows Slider numeric value within its live range and verify the result.",
    "description": (
        "Use only when the user explicitly asks to change one visible Windows UI Automation Slider and "
        "supplies its exact accessible label and a decimal value as a string. The action requires one unique "
        "enabled non-sensitive Slider, rechecks its original window handle and process, and requires "
        "RangeValuePattern to expose a finite current value, minimum and maximum. The requested number must "
        "be inside that live range. It verifies the exact final value locally without returning it. It does "
        "not infer units, adjust by relative increments, type keys, click buttons, submit or save. Unsupported "
        "or ambiguous controls fail closed. If setting times out or raises after invocation, inspect before "
        "retrying. Potentially high-impact labels require HUD confirmation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "field_label": {"type": "STRING", "description": "Exact accessible label of one visible, enabled, non-sensitive Windows Slider control."},
            "value": {"type": "STRING", "description": "Finite decimal number as text; no exponent or locale comma, for example '75' or '0.5'."},
        },
        "required": ["field_label", "value"],
    },
    "handler": set_slider_value,
}
