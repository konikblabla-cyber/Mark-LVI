"""Explicit, local form-field entry through Windows UI Automation.

Only uniquely named, visible, enabled, non-password Edit controls are eligible.
Values are set with ValuePattern (never simulated keystrokes or clipboard) and
verified locally. Field contents are never returned to the model or HUD.
"""
from __future__ import annotations

import re
import time

from actions import ui_interaction as ui
from core import outcome

_MAX_FIELDS = 8
_MAX_FIELD_TEXT = 2000
_MAX_TOTAL_TEXT = 6000
_FILL_TIMEOUT_SECONDS = 15.0

_SENSITIVE_LABELS = (
    "password", "passcode", "pin", "one time code", "one-time code", "otp",
    "security code", "verification code", "authentication code",
    "recovery code", "recovery phrase", "backup code", "security answer",
    "secret key", "api key", "access token", "social security",
    "national id", "tax id", "credit card", "card number", "cvv", "cvc",
    "iban", "bank account",
    "pesel", "pin code", "haslo", "hasło", "kod jednorazowy",
    "kod zabezpieczajacy", "kod zabezpieczający", "numer karty",
    "konto bankowe", "klucz api", "token dostepowy", "token dostępowy",
)


def _normalise_label(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _field_matches(item: dict, requested: str) -> bool:
    prefix = "automationid:"
    value = str(requested or "").strip()
    if value.casefold().startswith(prefix):
        return _normalise_label(item.get("automation_id", "")) == _normalise_label(
            value[len(prefix):],
        )
    return _normalise_label(item.get("name", "")) == _normalise_label(value)


def _is_sensitive_label(label: str) -> bool:
    normal = _normalise_label(label)
    padded = f" {normal} "
    return any(
        phrase in normal if " " in phrase else f" {phrase} " in padded
        for phrase in _SENSITIVE_LABELS
    )


def _validate_fields(raw_fields) -> tuple[list[dict] | None, str]:
    if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= _MAX_FIELDS:
        return None, f"fields must contain 1 to {_MAX_FIELDS} entries"
    fields = []
    total = 0
    seen = set()
    for index, item in enumerate(raw_fields, start=1):
        if not isinstance(item, dict) or set(item) != {"field_label", "text"}:
            return None, f"field {index} must contain only field_label and text"
        label, value = item.get("field_label"), item.get("text")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
            return None, f"field {index} has a missing or overlong field_label"
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_FIELD_TEXT:
            return None, f"field {index} has empty or overlong text"
        if "\\x00" in value:
            return None, f"field {index} contains an unsupported null character"
        clean_label = " ".join(label.split())
        key = _normalise_label(clean_label)
        if key in seen:
            return None, f"field {index} repeats a field label; each target must be unique"
        seen.add(key)
        sensitive_target = (
            clean_label.split(":", 1)[1]
            if clean_label.casefold().startswith("automationid:")
            else clean_label
        )
        if _is_sensitive_label(sensitive_target):
            return None, f"field {index} targets a credential or sensitive identifier; this action will not fill it"
        total += len(value)
        fields.append({"field_label": clean_label, "text": value})
    if total > _MAX_TOTAL_TEXT:
        return None, f"combined field text exceeds {_MAX_TOTAL_TEXT} characters"
    return fields, ""


def _window_for_handle(desktop, handle: int, process_id: int):
    window = desktop.window(handle=int(handle)).wrapper_object()
    if not window.is_visible():
        raise RuntimeError("the original application window is no longer visible")
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        raise RuntimeError("the original window handle now belongs to another process")
    return window


def _editable_controls(window, include_wrappers=False) -> list[dict]:
    controls = []
    for scanned, control in enumerate(window.descendants()):
        if scanned >= ui._MAX_SCANNED_CONTROLS or len(controls) >= _MAX_FIELDS * 12:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "Edit":
                continue
            if bool(getattr(info, "is_password", False)):
                continue
            if bool(getattr(info, "is_offscreen", False)):
                continue
            name = ui._safe_name(getattr(info, "name", ""))
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0 or not control.is_enabled():
                continue
            signature = ui._control_signature(info, rect)
            automation_id = signature["automation_id"]
            if _is_sensitive_label(name) or _is_sensitive_label(automation_id):
                continue
            label = name or (
                f"AutomationId:{automation_id}" if automation_id else ""
            )
            if not label:
                continue
            item = {
                "role": "Edit",
                "name": label,
                "automation_id": automation_id,
                "runtime_id": signature["runtime_id"],
                "rect": signature["rect"],
            }
            if include_wrappers:
                item["control"] = control
            controls.append(item)
        except Exception:
            continue
    return controls


def _read_form_snapshot(handle=None, expected_process_id=None) -> dict:
    from pywinauto import Desktop
    desktop = Desktop(backend="uia")
    if handle is None:
        from actions.screen_understanding import _active_top_level_window
        window = _active_top_level_window(desktop)
    else:
        window = _window_for_handle(desktop, handle, expected_process_id)
    if window is None or not window.is_visible():
        raise RuntimeError("the active application window is unavailable")
    actual_handle = int(window.handle)
    process_id = int(getattr(window.element_info, "process_id", 0) or 0)
    if not ui._valid_window_process_id(process_id, expected_process_id):
        raise RuntimeError("the active application process changed")
    context, _ = ui._collect_accessible_controls(window)
    fields = _editable_controls(window)
    return {
        "handle": actual_handle,
        "process_id": process_id,
        "context": context,
        "fields": fields,
    }


def _form_snapshot(handle=None, expected_process_id=None) -> dict:
    completed, value, error = ui._run_uia(
        lambda: _read_form_snapshot(handle, expected_process_id),
        ui._SNAPSHOT_TIMEOUT_SECONDS,
        exclusive_lock=ui._SNAPSHOT_LOCK,
    )
    if not completed:
        raise TimeoutError("UI Automation could not inspect form fields within 4 seconds")
    if error is not None:
        raise RuntimeError(f"UI Automation could not inspect form fields ({type(error).__name__})") from error
    if isinstance(value, tuple) and value and value[0] in {"busy", "cancelled"}:
        raise RuntimeError(value[1])
    if not isinstance(value, dict) or not isinstance(value.get("fields"), list):
        raise RuntimeError("UI Automation returned an invalid form snapshot")
    return value


def _fill_fields_on_window(handle: int, process_id: int, fields: list[dict],
                           cancelled) -> str:
    """Preflight every target, then set and locally verify each value once."""
    from pywinauto import Desktop
    desktop = Desktop(backend="uia")
    try:
        window = _window_for_handle(desktop, handle, process_id)
        controls = _editable_controls(window, include_wrappers=True)
        planned = []
        planned_identities = set()
        for field in fields:
            label = field["field_label"]
            matches = [
                item for item in controls
                if _field_matches(item, label)
            ]
            if not matches:
                return outcome.failed(
                    "fill_ui_fields",
                    "a requested visible, enabled, non-sensitive Edit control was not found",
                    field=label, fields_filled=0,
                )
            if len(matches) != 1:
                return outcome.ask(
                    "fill_ui_fields",
                    "more than one eligible Edit control has the exact requested label; nothing was entered",
                    field=label, matches=len(matches), fields_filled=0,
                )
            target = matches[0]
            signature = {
                "role": target["role"],
                "automation_id": target.get("automation_id", ""),
                "runtime_id": target.get("runtime_id", ()),
                "rect": target.get("rect"),
            }
            if signature["automation_id"]:
                identity = ("automation_id", signature["automation_id"])
            elif signature["runtime_id"]:
                identity = ("runtime_id", tuple(signature["runtime_id"]))
            else:
                identity = ("rect", tuple(signature.get("rect") or ()))
            if identity in planned_identities:
                return outcome.failed(
                    "fill_ui_fields",
                    "two requested labels resolve to the same field; nothing was entered",
                    field=label, fields_filled=0,
                )
            planned_identities.add(identity)
            try:
                pattern = target["control"].iface_value
                if bool(pattern.CurrentIsReadOnly):
                    return outcome.failed(
                        "fill_ui_fields", "the requested field is read-only",
                        field=label, fields_filled=0,
                    )
                # Probe ValuePattern access during preflight; never expose the
                # current value outside this local worker.
                _current_value = str(pattern.CurrentValue)
            except Exception as exc:
                return outcome.failed(
                    "fill_ui_fields",
                    f"the requested field does not support a safe writable ValuePattern ({type(exc).__name__})",
                    field=label, fields_filled=0,
                )
            planned.append({
                "label": label,
                "text": field["text"],
                "signature": signature,
            })

        filled = 0
        already_correct = 0
        for item in planned:
            attempted = False
            if cancelled.is_set():
                return outcome.unverified(
                    "fill_ui_fields",
                    "the operation timed out; later fields were not started and current field state must be inspected",
                    fields_filled=filled, fields_already_correct=already_correct,
                    total_fields=len(planned),
                )
            window = _window_for_handle(desktop, handle, process_id)
            current_controls = _editable_controls(window, include_wrappers=True)
            matches = [
                control for control in current_controls
                if _field_matches(control, item["label"])
            ]
            if len(matches) != 1 or not ui._same_control_identity(matches[0], item["signature"]):
                status = outcome.failed if filled == 0 else outcome.unverified
                return status(
                    "fill_ui_fields",
                    "the original field identity changed; remaining fields were not touched",
                    field=item["label"], fields_filled=filled,
                    fields_already_correct=already_correct,
                    total_fields=len(planned),
                )
            control = matches[0]["control"]
            try:
                pattern = control.iface_value
                if bool(pattern.CurrentIsReadOnly):
                    raise RuntimeError("read-only")
                current_value = str(pattern.CurrentValue)
                if current_value == item["text"]:
                    already_correct += 1
                    continue
                if cancelled.is_set():
                    return outcome.unverified(
                        "fill_ui_fields",
                        "the operation timed out before changing this field; inspect current form state",
                        fields_filled=filled, fields_already_correct=already_correct,
                        total_fields=len(planned),
                    )
                # Once SetValue is called, an exception or missing read-back is
                # ambiguous: the application may have accepted the value. Never
                # label that state as a clean failure or retry it automatically.
                attempted = True
                pattern.SetValue(item["text"])
                verified = False
                for _ in range(4):
                    if str(pattern.CurrentValue) == item["text"]:
                        verified = True
                        break
                    if cancelled.is_set():
                        break
                    time.sleep(0.1)
                if not verified:
                    return outcome.unverified(
                        "fill_ui_fields",
                        "SetValue was sent but the exact field value could not be confirmed; inspect the field before retrying",
                        field=item["label"], fields_filled=filled,
                        fields_attempted=filled + 1,
                        fields_already_correct=already_correct,
                        total_fields=len(planned),
                    )
                filled += 1
            except Exception as exc:
                if attempted:
                    return outcome.unverified(
                        "fill_ui_fields",
                        f"the field may have changed but local verification failed ({type(exc).__name__}); inspect it before retrying",
                        field=item["label"], fields_filled=filled,
                        fields_attempted=filled + 1,
                        fields_already_correct=already_correct,
                        total_fields=len(planned),
                    )
                status = outcome.failed if filled == 0 else outcome.unverified
                return status(
                    "fill_ui_fields",
                    f"the field could not be accessed safely ({type(exc).__name__}); remaining fields were not touched",
                    field=item["label"], fields_filled=filled,
                    fields_already_correct=already_correct,
                    total_fields=len(planned),
                )
        return outcome.done(
            "fill_ui_fields",
            fields_filled=filled,
            fields_already_correct=already_correct,
            total_fields=len(planned),
            submitted=False,
        )
    except Exception as exc:
        return outcome.failed(
            "fill_ui_fields",
            f"the original application window could not be safely accessed ({type(exc).__name__})",
            fields_filled=0, total_fields=len(fields),
        )


def _fill_worker(handle: int, process_id: int, fields: list[dict], cancelled=None) -> str:
    # Called only by the UIA worker, where COM and ValuePattern objects belong.
    import threading
    return _fill_fields_on_window(
        handle, process_id, fields, cancelled or threading.Event(),
    )


def _run_fill_with_timeout(handle: int, process_id: int,
                           fields: list[dict]) -> str:
    completed, value, worker_error = ui._run_uia(
        lambda cancelled: _fill_worker(
            handle, process_id, fields, cancelled,
        ),
        _FILL_TIMEOUT_SECONDS,
        exclusive_lock=ui._INVOKE_LOCK,
        pass_cancel_event=True,
    )
    if not completed:
        return outcome.unverified(
            "fill_ui_fields",
            "the operation exceeded its time limit; inspect the form before retrying",
            total_fields=len(fields),
        )
    if worker_error is not None:
        return outcome.failed(
            "fill_ui_fields",
            f"UI Automation could not fill the requested fields ({type(worker_error).__name__})",
            total_fields=len(fields),
        )
    if isinstance(value, tuple) and value and value[0] in {"busy", "cancelled"}:
        return outcome.unverified(
            "fill_ui_fields", value[1], total_fields=len(fields),
        )
    return value if isinstance(value, str) else outcome.unverified(
        "fill_ui_fields",
        "the UI Automation worker returned no verifiable result",
        total_fields=len(fields),
    )


def fill_ui_fields(parameters: dict, player=None, session_memory=None) -> str:
    fields, error = _validate_fields((parameters or {}).get("fields"))
    if fields is None:
        return outcome.failed("fill_ui_fields", error)

    try:
        snapshot = _form_snapshot()
    except Exception as exc:
        return outcome.failed(
            "fill_ui_fields",
            f"could not inspect the active application form ({type(exc).__name__})",
        )

    requested_labels = [item["field_label"] for item in fields]
    context = " ".join(ui._safe_name(item.get("name")) for item in snapshot["context"])
    requires_confirmation = ui._requires_confirmation(*requested_labels, context)
    if requires_confirmation:
        from core import confirm
        return confirm.request(
            key="fill_ui_fields",
            title="MARK: Fill requested application fields",
            detail=(
                "MARK will fill only these visible fields: "
                + ", ".join(requested_labels)
                + ". Field contents are not shown in this prompt or returned to the model. "
                "No submit, send, purchase or save button will be pressed. "
                "Confirm only if this data entry is intended."
            ),
            run=lambda: _run_fill_with_timeout(
                snapshot["handle"], snapshot["process_id"], fields,
            ),
        )

    return _run_fill_with_timeout(
        snapshot["handle"], snapshot["process_id"], fields,
    )


TOOL = {
    "name": "fill_ui_fields",
    "tier": "core",
    "brief": "Fill explicit visible form fields locally and verify each value; never submit.",
    "description": (
        "Use only when the user explicitly asks you to enter supplied text into "
        "specific fields of the currently active Windows application. Supply "
        "one to eight exact accessible Edit field labels (or AutomationId:<id>) and the exact text "
        "for each. The action accepts only unique, visible, enabled, non-password "
        "Edit controls with a writable UI Automation ValuePattern. Credential "
        "and sensitive-identifier fields (passwords, one-time codes, payment "
        "card data, bank identifiers, PESEL, API keys and similar) are refused. "
        "It preflights every requested field, pins the operation to the original "
        "window and process, sets values locally once, and verifies exact values "
        "inside the local worker. Field contents and existing values are never "
        "returned to the model or HUD. It never types simulated keystrokes, "
        "uses the clipboard, submits, sends, purchases, saves, or presses any "
        "other button. Apps may autosave fields themselves; high-impact form "
        "contexts require explicit HUD confirmation. If any field is ambiguous, "
        "unsupported, changed or unverified, it stops and reports partial "
        "progress without retrying. No screenshot, background polling, new "
        "dependency or extra model/API call is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "fields": {
                "type": "ARRAY",
                "description": "One to eight exact field labels and the exact text to enter. Never include passwords, payment credentials or other sensitive identifiers.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "field_label": {
                            "type": "STRING",
                            "description": "Exact accessible label, or AutomationId:<id>, of one visible, non-sensitive Edit field.",
                        },
                        "text": {
                            "type": "STRING",
                            "description": "Exact non-sensitive text the user explicitly asked to enter.",
                        },
                    },
                    "required": ["field_label", "text"],
                },
            },
        },
        "required": ["fields"],
    },
    "handler": fill_ui_fields,
}
