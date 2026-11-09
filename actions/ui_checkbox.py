"""Set one exact Windows checkbox to a requested state and verify TogglePattern."""
from __future__ import annotations

import time

from core import outcome
from actions import ui_interaction as ui


def _needs_confirmation(label):
    words = set(ui._normalise(label).replace("-", " ").split())
    consent_terms = {
        "agree", "consent", "permission", "allow", "authorize",
        "authorization", "subscribe", "subscription", "marketing",
        "privacy", "terms", "policy", "location", "notifications",
        "tracking", "personalization", "share",
    }
    return ui._requires_confirmation(label) or bool(words.intersection(consent_terms))


def _set_toggle_state(pattern, desired, cancelled):
    """Change a stable Off/On checkbox state once; never cycle Indeterminate."""
    if cancelled.is_set():
        return "cancelled", "the operation expired before checkbox state change"
    try:
        current = int(pattern.CurrentToggleState)
    except Exception:
        return "unsupported", "the control does not expose a readable TogglePattern state"
    if current not in (0, 1):
        return "unsupported_state", "indeterminate or unknown checkbox states are not changed"
    target = 1 if desired else 0
    if current == target:
        return "already_set", ""
    if cancelled.is_set():
        return "cancelled", "the operation expired before checkbox state change"
    try:
        pattern.Toggle()
    except Exception:
        return "uncertain", "the checkbox may have changed but the provider raised an error"
    # Some UIA providers publish the new toggle state asynchronously.
    # Poll the read-only state briefly, but never issue a second Toggle().
    deadline = time.monotonic() + min(1.5, ui._VERIFY_TIMEOUT_SECONDS)
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after Toggle; inspect before retrying"
        try:
            actual = int(pattern.CurrentToggleState)
        except Exception:
            return "uncertain", "the checkbox may have changed but its state could not be verified"
        if actual == target:
            return "changed", ""
        if actual not in (0, 1):
            return "unverified", "the checkbox reported an unknown state after toggling"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unverified", "the checkbox did not report the requested state after toggling"
        time.sleep(min(ui._VERIFY_INTERVAL, remaining))


def _set_on_window(window_handle, process_id, checkbox_label, desired, cancelled):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", "the original window handle changed"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the original window now belongs to another process"

    wanted = ui._normalise(checkbox_label)
    matches = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", "the operation expired before checkbox selection"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "CheckBox":
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
        return "missing_checkbox", "no visible, enabled, non-sensitive CheckBox has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_checkbox", "more than one CheckBox has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before checkbox state change"
    try:
        pattern = matches[0].iface_toggle
    except Exception:
        return "unsupported", "the CheckBox does not expose TogglePattern"
    return _set_toggle_state(pattern, desired, cancelled)


def set_checkbox_state(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("checkbox_label") or "").split())
    desired = params.get("checked")
    if not label or len(label) > 120:
        return outcome.failed("set_checkbox_state", "checkbox_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("set_checkbox_state", "credential, verification-code and payment controls are not supported", checkbox=label)
    if not isinstance(desired, bool):
        return outcome.failed("set_checkbox_state", "checked must be a boolean true or false", checkbox=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("set_checkbox_state", f"could not identify the active application ({type(exc).__name__})")

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: _set_on_window(handle, process_id, label, desired, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified("set_checkbox_state", "the checkbox may have changed; inspect it before retrying", checkbox=label)
        if isinstance(result, tuple):
            status, reason = result
            if status in {"busy", "cancelled"}:
                return outcome.ask("set_checkbox_state", reason, checkbox=label)
        else:
            status, reason = "", "UI Automation returned an invalid result"
        if status in {"changed", "already_set"}:
            return outcome.done("set_checkbox_state", checkbox=label, checked=desired,
                                verified=True, changed=status == "changed")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("set_checkbox_state", reason, checkbox=label)
        if status in {"ambiguous_checkbox", "unsupported_state"}:
            return outcome.ask("set_checkbox_state", reason, checkbox=label)
        return outcome.failed("set_checkbox_state", reason or status, checkbox=label)

    if _needs_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("set_checkbox_state", "another confirmation is already waiting on the HUD", checkbox=label)
        state_text = "checked" if desired else "unchecked"
        return confirm.request(
            key="set_checkbox_state",
            title=f"Confirm checkbox: {label}",
            detail=f"MARK will set '{label}' to {state_text}. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "set_checkbox_state",
    "tier": "extra",
    "brief": "Set one exact Windows CheckBox to checked or unchecked and verify TogglePattern.",
    "description": (
        "Use only when the user explicitly asks to check or uncheck one visible Windows CheckBox. "
        "Supply its exact accessible label and a boolean checked state. The action requires one unique "
        "enabled non-sensitive CheckBox, revalidates the original window handle and process, and uses "
        "TogglePattern only when the current state is a known Off or On value. It does not toggle an "
        "already-correct checkbox or change Indeterminate states. It verifies the final state locally. "
        "It never submits or saves the form. Consent, privacy, permission, subscription and other "
        "high-impact labels require HUD confirmation. A timeout or provider exception after Toggle is "
        "uncertain; inspect before retrying. No screenshot, clipboard, keystroke or extra API call is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "checkbox_label": {"type": "STRING", "description": "Exact accessible label of one visible Windows CheckBox."},
            "checked": {"type": "BOOLEAN", "description": "True to check it; false to uncheck it."},
        },
        "required": ["checkbox_label", "checked"],
    },
    "handler": set_checkbox_state,
}
