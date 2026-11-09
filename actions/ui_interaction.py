"""Local, accessibility-based clicks with an observable postcondition.

This action deliberately does not use screenshots, coordinates, the clipboard,
or a vision/model call. It invokes one uniquely named control in the active
window and reports success only when the requested accessible text appears
afterward. High-impact controls require the on-screen human confirmation gate.
"""
from __future__ import annotations

import re
import time

from core import outcome

_ALLOWED_ROLES = {
    "Text", "Button", "Hyperlink", "MenuItem", "TabItem", "CheckBox",
    "RadioButton", "ListItem", "TreeItem", "Group", "Window", "ComboBox",
}
_INVOKABLE_ROLES = {"Button", "Hyperlink", "MenuItem"}
_SENSITIVE_MARKERS = (
    "password", "passwd", "passcode", "pin code", "security code",
    "one-time code", "otp", "recovery code",
)
_HIGH_IMPACT_WORDS = (
    "delete", "remove", "discard", "send", "submit", "publish", "post",
    "purchase", "buy", "pay", "checkout", "order", "transfer", "unsubscribe",
    "revoke", "cancel", "confirm", "upload", "share", "grant", "erase",
    "destroy", "wipe", "reset", "format", "authorize", "sign", "deleted",
    "removed", "sent", "submitted", "published", "transferred", "purchased",
    "paid", "payment", "uploaded", "shared", "granted", "posted",
    "created", "closed", "cancelled", "canceled", "usuń", "usun", "wyślij",
    "wyslij", "opublikuj", "kup", "zapłać", "zaplac", "zamów", "zamow",
    "przelew", "anuluj", "potwierdź", "potwierdz", "usunięto", "usunieto",
    "wysłano", "wyslano", "opublikowano", "przelano",
)
_MAX_CONTROLS = 240
_VERIFY_ATTEMPTS = 10
_VERIFY_INTERVAL = 0.2


def _normalise(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _safe_name(value: object) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.casefold()
    if not text or any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return ""
    return text[:240]


def _requires_confirmation(label: str) -> bool:
    normal = _normalise(label)
    words = re.findall(r"[\w-]+", normal)
    return any(word in words for word in _HIGH_IMPACT_WORDS)


def _matching_controls(controls: list[dict], label: str) -> list[dict]:
    wanted = _normalise(label)
    if not wanted:
        return []
    return [
        item for item in controls
        if item.get("role") in _INVOKABLE_ROLES
        and _normalise(item.get("name")) == wanted
    ]


def _format_snapshot(records: list[dict]) -> str:
    from actions.screen_understanding import format_accessible_elements
    return format_accessible_elements(
        records, max_elements=_MAX_CONTROLS, max_chars=20_000,
    )


def _active_window_snapshot():
    """Return the active UIA window and bounded safe control records."""
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("pywinauto is not installed; local UI Automation is unavailable") from exc

    desktop = Desktop(backend="uia")
    window = desktop.get_active()
    if window is None or not window.is_visible():
        raise RuntimeError("there is no visible active window")

    records = []
    clickables = []
    for control in window.descendants():
        if len(records) >= _MAX_CONTROLS:
            break
        try:
            info = control.element_info
            role = str(getattr(info, "control_type", "") or "")
            if role not in _ALLOWED_ROLES:
                continue
            if bool(getattr(info, "is_password", False)):
                continue
            if bool(getattr(info, "is_offscreen", False)):
                continue
            label = _safe_name(getattr(info, "name", ""))
            if not label:
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            record = {"role": role, "name": label}
            records.append(record)
            if role in _INVOKABLE_ROLES and control.is_enabled():
                clickables.append({
                    "control": control, "role": role, "name": label,
                })
        except Exception:
            # An inaccessible or stale UIA element is not a reason to abort
            # inspection of the rest of the active window.
            continue
    return window, records, clickables


def _wait_for_expected_result(expected: str, snapshot_reader,
                              attempts: int = _VERIFY_ATTEMPTS,
                              interval: float = _VERIFY_INTERVAL,
                              sleep=time.sleep) -> bool:
    wanted = _normalise(expected)
    for attempt in range(max(1, int(attempts))):
        try:
            snapshot = snapshot_reader()
            if wanted and wanted in _normalise(snapshot):
                return True
        except Exception:
            pass
        if attempt + 1 < attempts:
            sleep(max(0.0, interval))
    return False


def _click_and_verify(button_label: str, expected_result: str,
                      expected_window_handle=None) -> str:
    label = " ".join(str(button_label or "").split())
    expected = " ".join(str(expected_result or "").split())
    if not label or len(label) > 120:
        return outcome.failed("click_and_verify", "button label is missing or too long")
    if not expected or len(expected) > 160:
        return outcome.failed(
            "click_and_verify",
            "an observable expected result is required (maximum 160 characters)",
            button=label,
        )

    try:
        window, records, clickables = _active_window_snapshot()
    except Exception as exc:
        return outcome.failed(
            "click_and_verify", str(exc), button=label,
        )

    handle = getattr(window, "handle", None)
    if (expected_window_handle is not None
            and handle != expected_window_handle):
        return outcome.failed(
            "click_and_verify",
            "the active window changed while confirmation was pending; nothing was clicked",
            button=label,
        )

    before = _format_snapshot(records)
    if _normalise(expected) in _normalise(before):
        return outcome.failed(
            "click_and_verify",
            "the expected result is already visible, so it cannot verify a new effect",
            button=label, expected=expected,
        )

    matches = _matching_controls(clickables, label)
    if not matches:
        return outcome.failed(
            "click_and_verify",
            "no visible, enabled button with that exact accessible label exists in the active window",
            button=label,
        )
    if len(matches) != 1:
        return outcome.ask(
            "click_and_verify",
            "more than one visible control has that exact label; nothing was clicked",
            button=label, matches=len(matches),
        )

    control = matches[0]["control"]
    try:
        # InvokePattern is preferred over coordinate clicks: it addresses the
        # actual accessible control and does not guess where it is on screen.
        control.iface_invoke.Invoke()
    except Exception as exc:
        return outcome.unverified(
            "click_and_verify",
            f"the control invocation could not be confirmed ({type(exc).__name__})",
            button=label, expected=expected,
        )

    def read_current_snapshot():
        _current, current_records, _current_clickables = _active_window_snapshot()
        return _format_snapshot(current_records)

    verified = _wait_for_expected_result(expected, read_current_snapshot)
    if verified:
        return outcome.done(
            "click_and_verify", button=label, expected=expected, verified=True,
        )
    return outcome.unverified(
        "click_and_verify",
        "the button was invoked, but the requested accessible result did not appear",
        button=label, expected=expected,
    )


def click_and_verify(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = str(params.get("button_label") or "").strip()
    expected = str(params.get("expected_result") or "").strip()
    if not label or not expected:
        return outcome.failed(
            "click_and_verify",
            "both button_label and expected_result are required",
        )

    # Validate the target and expected state before raising a confirmation
    # banner, so we never ask the user to approve an action we cannot identify.
    try:
        window, records, clickables = _active_window_snapshot()
    except Exception as exc:
        return outcome.failed("click_and_verify", str(exc), button=label)
    if _normalise(expected) in _normalise(_format_snapshot(records)):
        return outcome.failed(
            "click_and_verify",
            "the expected result is already visible; no click was made",
            button=label, expected=expected,
        )
    matches = _matching_controls(clickables, label)
    if not matches:
        return outcome.failed(
            "click_and_verify",
            "no visible, enabled control has that exact label in the active window",
            button=label,
        )
    if len(matches) != 1:
        return outcome.ask(
            "click_and_verify",
            "more than one visible control has that exact label; nothing was clicked",
            button=label, matches=len(matches),
        )

    handle = getattr(window, "handle", None)
    if handle is None:
        return outcome.failed(
            "click_and_verify",
            "the active window could not be identified safely; nothing was clicked",
            button=label,
        )

    if _requires_confirmation(label) or _requires_confirmation(expected):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask(
                "click_and_verify",
                "another confirmation is already waiting on the HUD",
                button=label,
            )
        return confirm.request(
            key="click_and_verify",
            title=f"Confirm click: {label}",
            detail=(
                f"MARK will invoke the '{label}' control in the currently active "
                f"window and then look for: {expected}. Confirm only if this is "
                "the intended action."
            ),
            run=lambda: _click_and_verify(label, expected, handle),
        )

    return _click_and_verify(label, expected, handle)


TOOL = {
    "name": "click_and_verify",
    "tier": "core",
    "brief": "Click one exact accessible control locally and verify the expected visible result.",
    "description": (
        "Use only when the user explicitly asks you to click a visible button, "
        "link or menu item AND you know the exact accessible label and a concrete "
        "piece of text that should appear afterward. Provide both button_label "
        "and expected_result; do not invent the expected result. This uses local "
        "Windows UI Automation, not screenshots, coordinates, clipboard access "
        "or a vision/model call. It clicks only one exact, unique, visible and "
        "enabled accessible control in the active window. It reports success "
        "only if expected_result was absent before the click and appears in the "
        "accessible UI afterward. Missing, ambiguous or unobservable outcomes "
        "are never reported as success. Controls whose labels imply sending, "
        "submitting, publishing, deleting, purchasing, payment, transfer, "
        "cancellation or confirmation require the human HUD confirmation gate. "
        "Never use for a payment, purchase, account deletion or other critical "
        "action unless the user explicitly asked for that specific action; "
        "confirmation is still required. Accessible text is returned to the "
        "active assistant conversation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "button_label": {
                "type": "STRING",
                "description": "Exact accessible label of the visible button, link or menu item.",
            },
            "expected_result": {
                "type": "STRING",
                "description": "Exact distinctive text that should become visible after clicking.",
            },
        },
        "required": ["button_label", "expected_result"],
    },
    "handler": click_and_verify,
}
