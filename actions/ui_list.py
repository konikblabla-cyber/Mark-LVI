"""Select one exact visible ListItem in a single-selection Windows UIA List."""
from __future__ import annotations

import time

from core import outcome
from actions import ui_interaction as ui
from actions import ui_checkbox


def _single_selection_parent(item):
    """Find the nearest List ancestor and require it to disallow multi-select."""
    current = item
    for _ in range(10):
        try:
            current = current.parent()
            if current is None:
                return None, "the list item has no accessible List parent"
            role = str(getattr(current.element_info, "control_type", "") or "")
            if role != "List":
                continue
            try:
                selection = current.iface_selection
                if bool(selection.CurrentCanSelectMultiple):
                    return None, "multi-selection lists are not changed by this action"
            except Exception:
                return None, "the parent List does not expose a readable single-selection capability"
            return current, ""
        except Exception:
            return None, "the list item's parent structure could not be verified"
    return None, "no accessible List parent was found within the bounded ancestor scan"


def _select_pattern(pattern, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before list selection"
    try:
        if bool(pattern.CurrentIsSelected):
            return "already_selected", ""
    except Exception:
        return "unsupported", "the list item does not expose a readable SelectionItemPattern state"
    if cancelled.is_set():
        return "cancelled", "the operation expired before list selection"
    try:
        pattern.Select()
    except Exception:
        return "uncertain", "the list item may have been selected but the provider raised an error"

    deadline = time.monotonic() + min(1.5, ui._VERIFY_TIMEOUT_SECONDS)
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after Select; inspect before retrying"
        try:
            selected = bool(pattern.CurrentIsSelected)
        except Exception:
            return "uncertain", "the item may have been selected but its state could not be verified"
        if selected:
            return "selected", ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unverified", "the item did not report selected after the operation"
        time.sleep(min(ui._VERIFY_INTERVAL, remaining))


def _select_on_window(window_handle, process_id, item_label, cancelled):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", "the original window handle changed"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the original window now belongs to another process"

    wanted = ui._normalise(item_label)
    matches = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", "the operation expired before list selection"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "ListItem":
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
        return "missing_item", "no visible, enabled, non-sensitive ListItem has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_item", "more than one ListItem has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before list selection"

    parent, reason = _single_selection_parent(matches[0])
    if parent is None:
        return "unsupported_list", reason
    if cancelled.is_set():
        return "cancelled", "the operation expired before list selection"
    try:
        pattern = matches[0].iface_selection_item
    except Exception:
        return "unsupported", "the ListItem does not expose SelectionItemPattern"
    return _select_pattern(pattern, cancelled)


def select_list_item(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("item_label") or "").split())
    if not label or len(label) > 120:
        return outcome.failed("select_list_item", "item_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("select_list_item", "credential, verification-code and payment controls are not supported", item=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("select_list_item", f"could not identify the active application ({type(exc).__name__})")

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: _select_on_window(handle, process_id, label, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified("select_list_item", "the list item may have changed; inspect before retrying", item=label)
        if isinstance(result, tuple):
            status, reason = result
            if status in {"busy", "cancelled"}:
                return outcome.ask("select_list_item", reason, item=label)
        else:
            status, reason = "", "UI Automation returned an invalid result"
        if status in {"selected", "already_selected"}:
            return outcome.done("select_list_item", item=label, selected=True,
                                 verified=True, changed=status == "selected")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("select_list_item", reason, item=label)
        if status in {"ambiguous_item", "unsupported_list"}:
            return outcome.ask("select_list_item", reason, item=label)
        return outcome.failed("select_list_item", reason or status, item=label)

    if ui_checkbox._needs_confirmation(label) or ui._requires_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("select_list_item", "another confirmation is already waiting on the HUD", item=label)
        return confirm.request(
            key="select_list_item",
            title=f"Confirm list selection: {label}",
            detail=f"MARK will select '{label}' in a single-selection list. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "select_list_item",
    "tier": "extra",
    "brief": "Select one exact Windows ListItem only in a single-selection List and verify SelectionItemPattern.",
    "description": (
        "Use only when the user explicitly asks to select one visible Windows list item. Supply its exact "
        "accessible ListItem label. The action requires one unique enabled non-sensitive ListItem whose "
        "nearest accessible List ancestor exposes SelectionPattern and explicitly reports that multiple "
        "selection is disabled. Multi-select lists, missing parent semantics and unsupported patterns are "
        "never modified. It revalidates the original window handle/process, runs in the bounded serialized "
        "UIA worker, skips already-selected items, and verifies IsSelected locally after one Select call. "
        "It never submits or saves the form. Consent/high-impact labels require HUD confirmation. "
        "Timeout/provider errors after Select are uncertain; inspect before retrying. No screenshot, clipboard, "
        "simulated keystroke, background polling or extra model/API call is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "item_label": {"type": "STRING", "description": "Exact accessible label of one visible Windows ListItem in a single-selection List."},
        },
        "required": ["item_label"],
    },
    "handler": select_list_item,
}
