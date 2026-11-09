"""On-demand, local Windows UI Automation inspection.

Reads accessible labels from the active window. It does not take screenshots,
record keystrokes, inspect the clipboard, or persist the result. Editable
controls are deliberately omitted to avoid exposing typed/private field values.
"""
from __future__ import annotations

_MAX_ELEMENTS = 80
_MAX_CHARS = 6000
_ALLOWED_ROLES = {
    "Text", "Button", "Hyperlink", "MenuItem", "TabItem", "CheckBox",
    "RadioButton", "ListItem", "TreeItem", "Group", "Window", "ComboBox",
}
_SENSITIVE_MARKERS = (
    "password", "passwd", "passcode", "pin code", "security code",
    "one-time code", "otp", "recovery code",
)


def _safe_label(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    lowered = text.casefold()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "[sensitive label hidden]"
    return text[:240]


def format_accessible_elements(elements, max_elements: int = _MAX_ELEMENTS,
                               max_chars: int = _MAX_CHARS) -> str:
    """Format already-filtered UIA records; pure and safe to test offline."""
    lines = []
    seen = set()
    for item in elements:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in _ALLOWED_ROLES:
            continue
        if item.get("password") or item.get("offscreen"):
            continue
        label = _safe_label(item.get("name"))
        if not label or label == "[sensitive label hidden]":
            continue
        key = (role, label.casefold())
        if key in seen:
            continue
        seen.add(key)
        line = f"- {role}: {label}"
        if sum(len(x) + 1 for x in lines) + len(line) > max_chars:
            lines.append("- [output truncated]")
            break
        lines.append(line)
        if len(lines) >= max_elements:
            lines.append("- [element limit reached]")
            break
    if not lines:
        return "I couldn't read any safe, accessible labels in the active window."
    return "\n".join(lines)


def _active_top_level_window(desktop):
    """Return the unique foreground top-level window using pywinauto 0.6.9.

    Desktop.get_active() is not available in the pywinauto 0.6.9 runtime used
    by MARK. Desktop.windows() has existed since 0.6.6 and forwards
    active_only/visible_only to findwindows.find_elements. Keeping this query
    at the top level avoids accidentally scanning only the focused child.
    """
    try:
        windows = desktop.windows(
            active_only=True, visible_only=True, top_level_only=True,
        )
    except Exception:
        return None

    visible = []
    for window in windows or []:
        try:
            if window is not None and window.is_visible():
                visible.append(window)
        except Exception:
            continue

    # Never guess if the provider unexpectedly reports multiple active roots.
    return visible[0] if len(visible) == 1 else None


def _read_active_window() -> str:
    try:
        from pywinauto import Desktop
    except ImportError:
        return ("Local UI inspection needs the optional Windows dependency "
                "pywinauto. No screenshot was taken.")
    try:
        desktop = Desktop(backend="uia")
        window = _active_top_level_window(desktop)
        if window is None or not window.is_visible():
            return "No visible active window was available for UI inspection."

        records = []
        for control in window.descendants():
            if len(records) >= _MAX_ELEMENTS * 3:
                break
            try:
                info = control.element_info
                role = str(getattr(info, "control_type", "") or "")
                # Never read editable controls: their values may contain secrets.
                if role in {"Edit", "Document"} or role not in _ALLOWED_ROLES:
                    continue
                if bool(getattr(info, "is_password", False)):
                    continue
                rect = control.rectangle()
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                name = getattr(info, "name", "") or ""
                records.append({
                    "role": role,
                    "name": name,
                    "password": bool(getattr(info, "is_password", False)),
                    "offscreen": bool(getattr(info, "is_offscreen", False)),
                })
            except Exception:
                continue
        return format_accessible_elements(records)
    except Exception as exc:
        return (f"Local UI inspection failed ({type(exc).__name__}). "
                "The active window may not expose an accessibility tree.")


def inspect_active_window(parameters: dict | None = None,
                          player=None, session_memory=None) -> str:
    """Tool handler. Called only when the user explicitly asks about the screen."""
    return _read_active_window()


TOOL = {
    "name": "inspect_active_window",
    "tier": "core",
    "brief": "Read safe accessible labels from the active window locally; no screenshot.",
    "description": (
        "Inspect what is currently visible in the active application when the user "
        "explicitly asks what is on their screen, asks you to read or explain the "
        "current window, or asks you to inspect the visible interface. This uses "
        "Windows UI Automation locally and returns accessible labels only. It does "
        "not take or upload a screenshot. Editable text fields and password fields "
        "are never read. Call only in response to an explicit screen-inspection "
        "request; never run in the background or merely because the user is silent. "
        "If the user wants a visual screenshot-based interpretation, use the existing "
        "computer_control screenshot flow only after explaining that image content "
        "is sent to the configured live model."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
        "required": [],
    },
    "handler": inspect_active_window,
}
