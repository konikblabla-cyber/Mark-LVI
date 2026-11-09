"""Set one exact Windows UIA Spinner through a verified RangeValuePattern."""
from __future__ import annotations

import math
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from core import outcome
from actions import ui_interaction as ui

_NUMBER_RE = re.compile(r"^-?(?:0|[1-9][0-9]{0,10})(?:\.[0-9]{1,6})?$")
_MAX_DECIMAL = Decimal("100000000000")


def _parse_target(value):
    if not isinstance(value, str) or len(value) > 32 or not _NUMBER_RE.fullmatch(value):
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    if not parsed.is_finite() or abs(parsed) > _MAX_DECIMAL:
        return None
    try:
        if not math.isfinite(float(parsed)):
            return None
    except (OverflowError, ValueError):
        return None
    return parsed


def _decimal_property(value):
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return Decimal(str(number))
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return None


def _range_snapshot(pattern):
    try:
        if bool(pattern.CurrentIsReadOnly):
            return "readonly", "the spinner is read-only", None, None, None, None
        current = _decimal_property(pattern.CurrentValue)
        minimum = _decimal_property(pattern.CurrentMinimum)
        maximum = _decimal_property(pattern.CurrentMaximum)
        step = _decimal_property(pattern.CurrentSmallChange)
    except Exception:
        return "unsupported", "the spinner does not expose a complete RangeValuePattern", None, None, None, None
    if None in (current, minimum, maximum, step):
        return "unsupported", "the spinner returned a non-finite or unreadable numeric range", None, None, None, None
    if minimum > maximum or step <= 0:
        return "unsupported", "the spinner range or increment is invalid", None, None, None, None
    return "ok", "", current, minimum, maximum, step


def _step_aligned(target, minimum, step):
    units = (target - minimum) / step
    nearest = units.to_integral_value(rounding=ROUND_HALF_UP)
    return abs(units - nearest) <= Decimal("0.000000001")


def _set_and_verify(pattern, target, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the spinner"
    status, reason, current, minimum, maximum, step = _range_snapshot(pattern)
    if status != "ok":
        return status, reason
    if target < minimum or target > maximum:
        return "out_of_range", "the requested number is outside the spinner's current minimum and maximum"
    if not _step_aligned(target, minimum, step):
        return "invalid_step", "the requested number does not align with the spinner's supported increment"
    if current == target:
        return "already_set", ""
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the spinner"
    try:
        pattern.SetValue(float(target))
    except Exception:
        return "uncertain", "the spinner setter raised an error; the value may have changed"
    deadline = time.monotonic() + 2.0
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after setting; inspect the spinner before retrying"
        try:
            actual = _decimal_property(pattern.CurrentValue)
        except Exception:
            actual = None
        if actual == target:
            return "set", ""
        if time.monotonic() >= deadline:
            return "unverified", "the spinner did not report the exact requested value after setting"
        time.sleep(min(ui._VERIFY_INTERVAL, max(0.0, deadline - time.monotonic())))


def _set_on_window(window_handle, process_id, field_label, target, cancelled):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", "the original window handle changed"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the original window now belongs to another process"

    wanted = ui._normalise(field_label)
    matches = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", "the operation expired before spinner selection"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "Spinner":
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
        return "missing_spinner", "no visible, enabled, non-sensitive Spinner has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_spinner", "more than one Spinner has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the spinner"
    try:
        pattern = matches[0].iface_range_value
    except Exception:
        return "unsupported", "the Spinner does not expose RangeValuePattern"
    return _set_and_verify(pattern, target, cancelled)


def set_spinner_value(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("field_label") or "").split())
    if not label or len(label) > 120:
        return outcome.failed("set_spinner_value", "field_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("set_spinner_value", "credential, verification-code and payment fields are not supported", field=label)
    target = _parse_target(params.get("value"))
    if target is None:
        return outcome.failed("set_spinner_value", "value must be a finite decimal string with up to 11 integer and 6 fractional digits", field=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("set_spinner_value", f"could not identify the active application ({type(exc).__name__})")

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: _set_on_window(handle, process_id, label, target, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified("set_spinner_value", "the spinner may have changed; inspect it before retrying", field=label)
        if isinstance(result, tuple):
            status, reason = result
            if status in {"busy", "cancelled"}:
                return outcome.ask("set_spinner_value", reason, field=label)
        else:
            status, reason = "", "UI Automation returned an invalid result"
        if status in {"set", "already_set"}:
            return outcome.done("set_spinner_value", field=label, verified=True, changed=status == "set")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("set_spinner_value", reason, field=label)
        if status in {"ambiguous_spinner"}:
            return outcome.ask("set_spinner_value", reason, field=label)
        return outcome.failed("set_spinner_value", reason or status, field=label)

    if ui._requires_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("set_spinner_value", "another confirmation is already waiting on the HUD", field=label)
        return confirm.request(
            key="set_spinner_value",
            title=f"Confirm value for {label}",
            detail=f"MARK will set '{label}' to {params['value']}. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "set_spinner_value",
    "tier": "extra",
    "brief": "Set one exact Windows Spinner numeric value using RangeValuePattern with range and increment checks.",
    "description": (
        "Use only when the user explicitly asks to change one visible Windows UI Automation Spinner and "
        "supplies its exact accessible label and a decimal value as a string. The action requires one unique "
        "enabled non-sensitive Spinner, rechecks its original window handle and process, and requires "
        "RangeValuePattern to expose a finite current value, minimum, maximum and positive small-change increment. "
        "The requested number must be in range and align with that increment. It verifies the final value locally "
        "without returning it. It does not infer units, navigate a picker, type keys, click buttons, submit or save. "
        "Unsupported or ambiguous controls fail closed. If setting times out or raises after invocation, inspect "
        "before retrying. Potentially high-impact labels require HUD confirmation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "field_label": {"type": "STRING", "description": "Exact accessible label of one visible, enabled, non-sensitive Windows Spinner control."},
            "value": {"type": "STRING", "description": "Finite decimal number as text; no exponent or locale comma, for example '14' or '2.5'."},
        },
        "required": ["field_label", "value"],
    },
    "handler": set_spinner_value,
}
