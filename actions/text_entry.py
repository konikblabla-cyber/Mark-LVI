"""Safe local text entry into one explicitly named, non-sensitive UIA Edit.

The field's existing value is read only inside the local worker for equality
verification. It is never returned, logged, or copied into conversation data.
Only ValuePattern is used: no clipboard, keyboard injection, screenshots, or
form submission.
"""
from __future__ import annotations

import re
import threading

from actions import ui_interaction as ui
from actions.screen_understanding import _active_top_level_window
from core import outcome

_MAX_TEXT_CHARS = 4000
_MAX_FIELD_LABEL_CHARS = 120
_MAX_SCANNED_CONTROLS = 1200
_SENSITIVE_PHRASES = (
    "password", "passwd", "passcode", "security code", "one-time code",
    "recovery code", "credit card", "card number", "cvv", "cvc",
    "social security", "api key", "access token", "private key",
    "bank account", "account number", "routing number", "iban", "secret",
    "hasło", "haslo", "kod bezpieczeństwa", "kod bezpieczenstwa",
    "kod jednorazowy", "numer karty", "karta płatnicza", "karta platnicza",
)
_SENSITIVE_WORDS = {"pin", "otp", "ssn"}
_ENTRY_LOCK = threading.Lock()
_SNAPSHOT_TIMEOUT_SECONDS = 4.0
_SET_TIMEOUT_SECONDS = 4.0


def _normalise_label(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normalise_value(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _is_sensitive_label(label: str) -> bool:
    normal = _normalise_label(label)
    if any(phrase in normal for phrase in _SENSITIVE_PHRASES):
        return True
    words = set(re.findall(r"[\w-]+", normal))
    return bool(words.intersection(_SENSITIVE_WORDS))


def _validate_request(parameters: dict) -> tuple[str | None, str | None, str]:
    params = parameters if isinstance(parameters, dict) else {}
    label = " ".join(str(params.get("field_label") or "").split())
    value = params.get("text")
    if not label or len(label) > _MAX_FIELD_LABEL_CHARS:
        return None, None, "field_label is required and must be at most 120 characters"
    if _is_sensitive_label(label):
        return None, None, "sensitive fields cannot be filled by this action"
    if not isinstance(value, str) or not value:
        return None, None, "text must be a non-empty string"
    if len(value) > _MAX_TEXT_CHARS:
        return None, None, f"text exceeds the {_MAX_TEXT_CHARS}-character limit"
    if "\x00" in value:
        return None, None, "text cannot contain a NUL character"
    return label, value, ""


def _collect_edit_controls(window, include_wrappers: bool = False) -> list[dict]:
    fields = []
    for scanned, control in enumerate(window.descendants()):
        if scanned >= _MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "Edit":
                continue
            if bool(getattr(info, "is_password", False)):
                continue
            if bool(getattr(info, "is_offscreen", False)):
                continue
            label = " ".join(str(getattr(info, "name", "") or "").split())
            if not label or len(label) > _MAX_FIELD_LABEL_CHARS:
                continue
            if _is_sensitive_label(label):
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0 or not control.is_enabled():
                continue
            pattern = control.iface_value
            if bool(pattern.CurrentIsReadOnly):
                continue
            signature = ui._control_signature(info, rect)
            item = {
                "role": "Edit",
                "name": label,
                "automation_id": signature["automation_id"],
                "runtime_id": signature["runtime_id"],
                "rect": signature["rect"],
            }
            if include_wrappers:
                item["control"] = control
            fields.append(item)
        except Exception:
            # Unsupported, stale, read-only, or inaccessible controls are skipped.
            continue
    return fields


def _read_active_snapshot() -> dict:
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = _active_top_level_window(desktop)
    if window is None or not window.is_visible():
        raise RuntimeError("no unique visible active application window")
    handle = getattr(window, "handle", None)
    process_id = getattr(window.element_info, "process_id", 0)
    if handle is None or not ui._valid_window_process_id(process_id):
        raise RuntimeError("active application window has no stable identity")
    return {
        "handle": int(handle),
        "process_id": int(process_id),
        "fields": _collect_edit_controls(window),
    }


def _snapshot() -> dict:
    completed, value, error = ui._run_uia(
        _read_active_snapshot,
        _SNAPSHOT_TIMEOUT_SECONDS,
        exclusive_lock=_ENTRY_LOCK,
    )
    if not completed:
        raise TimeoutError("UI Automation did not inspect editable controls within 4 seconds")
    if error is not None:
        raise RuntimeError(
            f"editable controls could not be inspected ({type(error).__name__})"
        ) from error
    if not isinstance(value, dict) or not isinstance(value.get("fields"), list):
        raise RuntimeError("UI Automation returned an invalid editable-field snapshot")
    return value


def _matching_fields(fields: list[dict], label: str) -> list[dict]:
    wanted = _normalise_label(label)
    if not wanted:
        return []
    return [
        item for item in fields
        if item.get("role") == "Edit"
        and _normalise_label(item.get("name")) == wanted
    ]


def _set_value_on_window(handle: int, process_id: int, label: str,
                         expected_value: str, original_signature: dict,
                         cancelled: threading.Event) -> tuple[str, str]:
    from pywinauto import Desktop

    if cancelled.is_set():
        return "cancelled", "operation expired before opening the target window"
    desktop = Desktop(backend="uia")
    window = desktop.window(handle=int(handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the window handle now belongs to another process"

    fields = _collect_edit_controls(window, include_wrappers=True)
    matches = _matching_fields(fields, label)
    if not matches:
        return "missing_field", "the original editable field is no longer available"
    if len(matches) != 1:
        return "ambiguous", "the editable field label is no longer unique"
    target = matches[0]
    if not ui._same_control_identity(target, original_signature):
        return "changed_field", "the matching field is not the one originally identified"

    try:
        pattern = target["control"].iface_value
        if bool(pattern.CurrentIsReadOnly):
            return "read_only", "the original field is now read-only"
        current_value = _normalise_value(pattern.CurrentValue)
    except Exception as exc:
        return "unsupported_pattern", f"the field does not expose a readable ValuePattern ({type(exc).__name__})"

    if current_value == expected_value:
        return "already_set", "the field already contains the requested value"
    if cancelled.is_set():
        return "cancelled", "operation expired before setting the field value"

    # SetValue is the side-effect boundary. Any exception from here onward is
    # treated as uncertain; callers must not automatically repeat the action.
    try:
        pattern.SetValue(expected_value)
    except Exception as exc:
        return "uncertain", f"ValuePattern.SetValue raised {type(exc).__name__}"

    try:
        observed = _normalise_value(pattern.CurrentValue)
    except Exception as exc:
        return "unverified", f"the value was set but read-back failed ({type(exc).__name__})"
    if observed == expected_value:
        return "verified", ""
    return "unverified", "the field value did not match after setting"


def _fill_text_field(label: str, value: str, snapshot: dict) -> str:
    matches = _matching_fields(snapshot["fields"], label)
    if not matches:
        return outcome.failed(
            "fill_text_field",
            "no visible, enabled, non-sensitive editable field has that exact accessible label",
            field=label,
        )
    if len(matches) != 1:
        return outcome.ask(
            "fill_text_field",
            "more than one editable field has that exact label; nothing was changed",
            field=label, matches=len(matches),
        )

    item = matches[0]
    signature = {
        "role": item["role"],
        "automation_id": item.get("automation_id", ""),
        "runtime_id": item.get("runtime_id", ()),
        "rect": item.get("rect"),
    }
    completed, result, error = ui._run_uia(
        lambda cancelled: _set_value_on_window(
            snapshot["handle"], snapshot["process_id"], label, value,
            signature, cancelled,
        ),
        _SET_TIMEOUT_SECONDS,
        exclusive_lock=_ENTRY_LOCK,
        pass_cancel_event=True,
    )
    if not completed:
        return outcome.unverified(
            "fill_text_field",
            "UI Automation timed out; the field may have changed. Do not retry automatically.",
            field=label, timed_out=True,
        )
    if error is not None:
        return outcome.unverified(
            "fill_text_field",
            f"UI Automation raised {type(error).__name__}; the field may have changed. Do not retry automatically.",
            field=label,
        )

    status, reason = result
    if status == "verified":
        return outcome.done(
            "fill_text_field", field=label, verified=True,
        )
    if status == "already_set":
        return outcome.done(
            "fill_text_field", field=label, verified=True, already_set=True,
        )
    if status in {"uncertain", "unverified"}:
        return outcome.unverified(
            "fill_text_field",
            reason + ". Do not retry automatically.",
            field=label,
        )
    if status in {"busy", "cancelled"}:
        return outcome.failed(
            "fill_text_field", reason + "; no value was set",
            field=label,
        )
    return outcome.failed(
        "fill_text_field", reason or status,
        field=label,
    )


def fill_text_field(parameters: dict, player=None, session_memory=None) -> str:
    label, value, error = _validate_request(parameters)
    if error:
        return outcome.failed("fill_text_field", error)
    try:
        snapshot = _snapshot()
    except Exception as exc:
        return outcome.failed(
            "fill_text_field",
            f"could not inspect the active application's editable fields ({type(exc).__name__})",
            field=label,
        )
    return _fill_text_field(label, value, snapshot)


TOOL = {
    "name": "fill_text_field",
    "tier": "core",
    "brief": "Fill one exact non-sensitive text field locally and verify without returning its value.",
    "description": (
        "Use only when the user explicitly asks you to enter supplied text into "
        "a currently visible field in the active Windows application. Require "
        "the exact accessible field label and the exact text to enter. This "
        "action targets one unique, visible, enabled, non-password, non-sensitive "
        "Edit control that supports UI Automation ValuePattern. It rechecks the "
        "original window, process and field identity before setting the value, "
        "then verifies the value locally. The entered text and existing field "
        "value are never returned to the conversation or logged. It does not "
        "read or fill password, PIN, OTP, recovery, payment-card, bank-account, "
        "API-key, token or other sensitive fields. It does not press Enter, "
        "submit forms, click buttons, use the clipboard, inject keystrokes, take "
        "screenshots, poll in the background, or make additional model/API calls. "
        "If the result is uncertain, it reports unverified and must not be retried "
        "automatically. It cannot guarantee that an application will not autosave "
        "text as it is entered."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "field_label": {
                "type": "STRING",
                "description": "Exact accessible label of the visible editable field.",
            },
            "text": {
                "type": "STRING",
                "description": "Exact text explicitly supplied by the user to enter; never infer or invent it.",
            },
        },
        "required": ["field_label", "text"],
    },
    "handler": fill_text_field,
}
