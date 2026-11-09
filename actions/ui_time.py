"""Strict 24-hour time entry into one exact Windows UIA Edit field."""
from __future__ import annotations

import re

from core import outcome
from actions import ui_interaction as ui

_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def set_time_field(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("field_label") or "").split())
    value = params.get("time")
    if not label or len(label) > 120:
        return outcome.failed("set_time_field", "field_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("set_time_field", "credential, verification-code and payment fields are not supported", field=label)
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        return outcome.failed("set_time_field", "time must use strict 24-hour HH:MM format (00:00–23:59)", field=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("set_time_field", f"could not identify the active application ({type(exc).__name__})")
    # Reuses the existing bounded UIA ValuePattern setter. The current and
    # resulting field values are compared locally and are never returned.
    return ui._fill_text_and_verify(label, value, handle, process_id)


TOOL = {
    "name": "set_time_field",
    "tier": "core",
    "brief": "Set one exact Windows time field to a validated 24-hour HH:MM value and verify it locally.",
    "description": (
        "Use only when the user explicitly asks to enter a time in one visible Windows Edit field. "
        "Supply its exact accessible label and a strict 24-hour HH:MM value (00:00–23:59). "
        "The action identifies the active window, requires one exact visible enabled non-sensitive Edit control, "
        "uses Windows UI Automation ValuePattern, and verifies the resulting value locally without returning field contents. "
        "It does not change dates, select AM/PM, press buttons, submit, save, or confirm a form. "
        "Unsupported or ambiguous controls are rejected. If the setter times out or raises after invocation, the result is uncertain; inspect before retrying. "
        "No screenshots, clipboard, simulated keystrokes, background polling, or extra model/API calls are used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "field_label": {"type": "STRING", "description": "Exact accessible label of the visible, non-sensitive Windows Edit time field."},
            "time": {"type": "STRING", "description": "Time in strict 24-hour HH:MM format, for example 09:30 or 18:05."},
        },
        "required": ["field_label", "time"],
    },
    "handler": set_time_field,
}
