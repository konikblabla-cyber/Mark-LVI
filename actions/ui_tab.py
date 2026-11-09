"""Select one exact Windows UIA TabItem and verify its selected state."""
from __future__ import annotations

import time

from core import outcome
from actions import ui_interaction as ui
from actions import ui_checkbox


def _select_pattern(pattern, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before tab selection"
    try:
        if bool(pattern.CurrentIsSelected):
            return "already_selected", ""
    except Exception:
        return "unsupported", "the tab does not expose a readable SelectionItemPattern state"
    if cancelled.is_set():
        return "cancelled", "the operation expired before tab selection"
    try:
        pattern.Select()
    except Exception:
        return "uncertain", "the tab may have been selected but the provider raised an error"

    deadline = time.monotonic() + min(1.5, ui._VERIFY_TIMEOUT_SECONDS)
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after Select; inspect before retrying"
        try:
            selected = bool(pattern.CurrentIsSelected)
        except Exception:
            return "uncertain", "the tab may have been selected but its state could not be verified"
        if selected:
            return "selected", ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unverified", "the tab did not report selected after the operation"
        time.sleep(min(ui._VERIFY_INTERVAL, remaining))


def _select_on_window(window_handle, process_id, tab_label, cancelled):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", "the original window handle changed"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the original window now belongs to another process"

    wanted = ui._normalise(tab_label)
    matches = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", "the operation expired before tab selection"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "TabItem":
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
        return "missing_tab", "no visible, enabled, non-sensitive TabItem has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_tab", "more than one TabItem has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before tab selection"
    try:
        pattern = matches[0].iface_selection_item
    except Exception:
        return "unsupported", "the TabItem does not expose SelectionItemPattern"
    return _select_pattern(pattern, cancelled)


def select_tab_item(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("tab_label") or "").split())
    if not label or len(label) > 120:
        return outcome.failed("select_tab_item", "tab_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("select_tab_item", "credential, verification-code and payment controls are not supported", tab=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("select_tab_item", f"could not identify the active application ({type(exc).__name__})")

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: _select_on_window(handle, process_id, label, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified("select_tab_item", "the tab may have changed; inspect it before retrying", tab=label)
        if isinstance(result, tuple):
            status, reason = result
            if status in {"busy", "cancelled"}:
                return outcome.ask("select_tab_item", reason, tab=label)
        else:
            status, reason = "", "UI Automation returned an invalid result"
        if status in {"selected", "already_selected"}:
            return outcome.done("select_tab_item", tab=label, selected=True,
                                 verified=True, changed=status == "selected")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("select_tab_item", reason, tab=label)
        if status == "ambiguous_tab":
            return outcome.ask("select_tab_item", reason, tab=label)
        return outcome.failed("select_tab_item", reason or status, tab=label)

    # Navigating into privacy/security/consent panels is not treated as a
    # consent action itself, but destructive/high-impact tabs still confirm.
    if ui_checkbox._needs_confirmation(label) or ui._requires_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("select_tab_item", "another confirmation is already waiting on the HUD", tab=label)
        return confirm.request(
            key="select_tab_item",
            title=f"Confirm tab: {label}",
            detail=f"MARK will open tab '{label}'. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "select_tab_item",
    "tier": "extra",
    "brief": "Select one exact Windows TabItem through SelectionItemPattern and verify the selected state.",
    "description": (
        "Use only when the user explicitly asks to open one visible Windows UI Automation tab. "
        "Supply its exact accessible TabItem label. The action requires one unique visible enabled "
        "non-sensitive TabItem, revalidates the original window handle and process, and uses "
        "SelectionItemPattern.Select(). It skips an already-selected tab and verifies IsSelected "
        "locally. It does not click coordinates or submit/save the form. Destructive or consent-related "
        "labels require HUD confirmation. Timeout/provider errors after Select are uncertain; inspect "
        "before retrying. No screenshot, clipboard, simulated keystroke, background polling or extra "
        "model/API call is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "tab_label": {"type": "STRING", "description": "Exact accessible label of one visible Windows TabItem."},
        },
        "required": ["tab_label"],
    },
    "handler": select_tab_item,
}
