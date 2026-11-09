"""Bounded, read-only diagnostics for visible Windows date/time picker controls.

Reports UIA roles, safe accessible labels, safe AutomationIds and supported
pattern availability. Never reads ValuePattern values or takes screenshots.
"""
from __future__ import annotations

import re

_MAX_SCANNED = 1200
_MAX_ITEMS = 60
_MAX_OUTPUT_CHARS = 6000
_ALLOWED = {"Calendar", "CalendarItem", "ComboBox", "Spinner", "Edit",
            "Button", "Text", "List", "ListItem", "DatePicker"}
_SENSITIVE_PHRASES = (
    "password", "passwd", "passcode", "security code", "one-time code",
    "recovery code", "verification code", "authentication code", "credit card",
    "card number", "cvv", "cvc", "iban", "bank account", "routing number",
    "private key", "api key", "access token", "secret key", "security token", "credential", "bearer token",
)
_SENSITIVE_WORDS = {"pin", "otp", "cvv", "cvc", "iban", "token", "credential", "secret"}
_PATTERN_NAMES = {
    "value", "range_value", "selection_item", "expand_collapse",
    "invoke", "toggle", "grid",
}
_PATTERNS = (
    ("value", "iface_value"),
    ("range_value", "iface_range_value"),
    ("selection_item", "iface_selection_item"),
    ("expand_collapse", "iface_expand_collapse"),
    ("invoke", "iface_invoke"),
    ("toggle", "iface_toggle"),
    ("grid", "iface_grid"),
)


def _normalise_sensitive_text(value):
    text = " ".join(str(value or "").split())
    # Split common camelCase AutomationIds so "apiKeyField" is filtered too.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).casefold()
    return text


def _is_sensitive(value):
    text = _normalise_sensitive_text(value)
    words = set(re.findall(r"[a-z0-9]+", text))
    if words.intersection(_SENSITIVE_WORDS) or any(
        phrase in text for phrase in _SENSITIVE_PHRASES
    ):
        return True
    # Avoid exposing personal contact details and opaque identifier payloads
    # embedded in otherwise innocuous accessible names or AutomationIds.
    if re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text, re.I):
        return True
    if re.search(r"(?<!\d)(?:\+?\d[\s().-]*){9,}(?!\d)", text):
        return True
    if re.fullmatch(r"[a-z0-9_-]{32,}", text):
        return True
    return False


def _safe_label(value):
    label = " ".join(str(value or "").split())
    if not label or _is_sensitive(label):
        return ""
    return label[:160]


def format_picker_diagnostics(records, limit=_MAX_ITEMS):
    try:
        safe_limit = max(1, min(_MAX_ITEMS, int(limit)))
    except (TypeError, ValueError, OverflowError):
        safe_limit = _MAX_ITEMS
    try:
        iterator = iter(records or ())
    except TypeError:
        iterator = iter(())

    lines = []
    seen = set()
    output_chars = 0
    for scanned, item in enumerate(iterator):
        if scanned >= _MAX_SCANNED:
            lines.append("- [scan limit reached]")
            break
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        label = _safe_label(item.get("name"))
        if role not in _ALLOWED or not label or item.get("offscreen") or item.get("password"):
            continue

        raw_id = str(item.get("automation_id") or "").strip()
        safe_id = _safe_label(raw_id)[:100] if raw_id else ""
        key = (role, label.casefold(), safe_id.casefold())
        if key in seen:
            continue
        seen.add(key)

        raw_patterns = item.get("patterns", ())
        if not isinstance(raw_patterns, (list, tuple, set, frozenset)):
            raw_patterns = ()
        patterns = []
        for value in raw_patterns:
            if isinstance(value, str) and value in _PATTERN_NAMES and value not in patterns:
                patterns.append(value)
        detail = ", ".join(patterns) if patterns else "no supported action pattern detected"
        suffix = f"; AutomationId={safe_id}" if safe_id else ""

        if len(lines) >= safe_limit:
            lines.append("- [item limit reached]")
            break
        lines.append(f"- {role}: {label}{suffix}; patterns: {detail}")

    return "\n".join(lines) if lines else (
        "No safe visible date/time picker controls were exposed by Windows UI Automation."
    )


def _inspect_window(window_handle, process_id, cancelled):
    from pywinauto import Desktop
    from actions import ui_interaction as ui

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", []
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", []
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", []

    records = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", records
        if index >= _MAX_SCANNED or len(records) >= _MAX_ITEMS * 3:
            break
        try:
            info = control.element_info
            role = str(getattr(info, "control_type", "") or "")
            if role not in _ALLOWED:
                continue
            if bool(getattr(info, "is_password", False)) or bool(getattr(info, "is_offscreen", False)):
                continue
            label = _safe_label(getattr(info, "name", ""))
            if not label:
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0 or not control.is_enabled():
                continue

            automation_id = _safe_label(getattr(info, "automation_id", "") or "")
            patterns = []
            # Getting an interface reference checks pattern support only. Do
            # not read CurrentValue, CurrentValueAsString or any field content.
            for pattern_name, attribute in _PATTERNS:
                if cancelled.is_set():
                    return "cancelled", records
                try:
                    getattr(control, attribute)
                    patterns.append(pattern_name)
                except Exception:
                    pass
            records.append({
                "role": role,
                "name": label,
                "automation_id": automation_id,
                "patterns": patterns,
            })
        except Exception:
            # One stale/unsupported element must not abort the whole inspection.
            continue
    return "ok", records


def inspect_picker_controls(parameters: dict | None = None,
                           player=None, session_memory=None) -> str:
    """Inspect the active window with bounded, process-verified UIA access."""
    from actions import ui_interaction as ui

    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            return "The active application process could not be verified; nothing was inspected."
    except Exception as exc:
        return f"Could not identify the active application ({type(exc).__name__}); nothing was changed."

    completed, result, error = ui._run_uia(
        lambda cancelled: _inspect_window(handle, process_id, cancelled),
        ui._SNAPSHOT_TIMEOUT_SECONDS,
        exclusive_lock=ui._SNAPSHOT_LOCK,
        pass_cancel_event=True,
    )
    if not completed:
        return "Picker inspection timed out; nothing was changed. Try again when the application is responsive."
    if error is not None:
        return f"Local picker inspection failed ({type(error).__name__}); no control was changed."
    if isinstance(result, tuple) and result and result[0] in {"busy", "cancelled"}:
        return f"Picker inspection was not run: {result[1]}; no control was changed."
    if not isinstance(result, tuple) or len(result) != 2:
        return "Local picker inspection returned an invalid result; no control was changed."

    status, records = result
    if status != "ok":
        return "The original application window changed or closed; no control was changed."
    return format_picker_diagnostics(records)


TOOL = {
    "name": "inspect_picker_controls",
    "tier": "extra",
    "brief": "Inspect visible Windows date/time picker controls and supported UIA patterns without reading field values.",
    "description": (
        "Use only when the user explicitly asks MARK to inspect or diagnose a visible date/time picker, "
        "especially when standard calendar/time controls are not recognized. The action first identifies the "
        "active window, then inspects that exact window and process in a bounded Windows UI Automation worker. "
        "It reports visible enabled control roles, safe accessible labels, safe AutomationIds and common UIA "
        "pattern availability. Pattern detection never reads ValuePattern.CurrentValue or any field content. "
        "It takes no screenshots, accesses no clipboard, sends no keystrokes, and changes no controls. "
        "A timed-out or changed window is reported as uninspected."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": inspect_picker_controls,
}
