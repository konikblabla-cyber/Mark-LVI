"""Local, read-only diagnostics for visible Windows date/time picker controls.

Reports UIA control roles, safe accessible labels, AutomationIds and supported
pattern availability. Never reads ValuePattern values or takes screenshots.
"""
from __future__ import annotations

from core import outcome

_MAX_SCANNED = 1200
_MAX_ITEMS = 60
_ALLOWED = {"Calendar", "CalendarItem", "ComboBox", "Spinner", "Edit",
            "Button", "Text", "List", "ListItem", "DatePicker"}
_SENSITIVE = ("password", "passwd", "passcode", "pin code", "security code",
              "one-time code", "otp", "recovery code", "verification code",
              "credit card", "card number", "cvv", "cvc", "iban", "api key",
              "access token", "secret key")
_PATTERNS = (
    ("value", "iface_value"),
    ("range_value", "iface_range_value"),
    ("selection_item", "iface_selection_item"),
    ("expand_collapse", "iface_expand_collapse"),
    ("invoke", "iface_invoke"),
    ("toggle", "iface_toggle"),
    ("grid", "iface_grid"),
)


def _safe_label(value):
    label = " ".join(str(value or "").split())
    lowered = label.casefold()
    if not label or any(word in lowered for word in _SENSITIVE):
        return ""
    return label[:160]


def format_picker_diagnostics(records, limit=_MAX_ITEMS):
    lines = []
    seen = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        label = _safe_label(item.get("name"))
        if role not in _ALLOWED or not label or item.get("offscreen") or item.get("password"):
            continue
        key = (role, label.casefold(), str(item.get("automation_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        aid = str(item.get("automation_id") or "").strip()[:100]
        patterns = [str(x) for x in item.get("patterns", []) if x in
                    {"value", "range_value", "selection_item", "expand_collapse",
                     "invoke", "toggle", "grid"}]
        detail = ", ".join(patterns) if patterns else "no supported action pattern detected"
        suffix = f"; AutomationId={aid}" if aid else ""
        lines.append(f"- {role}: {label}{suffix}; patterns: {detail}")
        if len(lines) >= limit:
            lines.append("- [item limit reached]")
            break
    return "\n".join(lines) if lines else "No safe visible date/time picker controls were exposed by Windows UI Automation."


def inspect_picker_controls(parameters: dict | None = None,
                           player=None, session_memory=None) -> str:
    try:
        from pywinauto import Desktop
        from actions.screen_understanding import _active_top_level_window
        from actions import ui_interaction as ui
        desktop = Desktop(backend="uia")
        window = _active_top_level_window(desktop)
        if window is None:
            return "No unique visible active application window was available."
        records = []
        for index, control in enumerate(window.descendants()):
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
                patterns = []
                for pattern_name, attribute in _PATTERNS:
                    try:
                        getattr(control, attribute)
                        patterns.append(pattern_name)
                    except Exception:
                        pass
                records.append({
                    "role": role, "name": label,
                    "automation_id": str(getattr(info, "automation_id", "") or ""),
                    "patterns": patterns,
                })
            except Exception:
                continue
        return format_picker_diagnostics(records)
    except ImportError:
        return "Picker diagnostics require the optional Windows dependency pywinauto."
    except Exception as exc:
        return f"Local picker inspection failed ({type(exc).__name__}); no control was changed."


TOOL = {
    "name": "inspect_picker_controls",
    "tier": "extra",
    "brief": "Inspect visible Windows date/time picker controls and supported UIA patterns without reading field values.",
    "description": (
        "Use only when the user explicitly asks MARK to inspect or diagnose a visible date/time picker, "
        "especially when standard calendar/time controls are not recognized. Locally reports visible enabled "
        "Windows UI Automation control roles, safe accessible labels, AutomationIds and whether common UIA "
        "patterns are exposed. It never reads ValuePattern values, screenshots, clipboard or keystrokes, "
        "and never changes a control. Output may include labels and AutomationIds from the active application."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": inspect_picker_controls,
}
