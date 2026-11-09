"""Expand or collapse one exact visible Windows UIA TreeItem and verify its state."""
from __future__ import annotations

import time

from core import outcome
from actions import ui_interaction as ui
from actions import ui_checkbox

_COLLAPSED = 0
_EXPANDED = 1
_PARTIALLY_EXPANDED = 2
_LEAF_NODE = 3


def _is_expanded(state):
    return state in (_EXPANDED, _PARTIALLY_EXPANDED)


def _set_expand_collapse(pattern, desired, cancelled):
    """Call Expand/Collapse at most once and verify locally; never blindly retry."""
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the tree item"
    try:
        state = int(pattern.CurrentExpandCollapseState)
    except Exception:
        return "unsupported", "the TreeItem does not expose a readable ExpandCollapsePattern state"

    if state not in (_COLLAPSED, _EXPANDED, _PARTIALLY_EXPANDED, _LEAF_NODE):
        return "unsupported", "the TreeItem reported an unknown expand/collapse state"
    if state == _LEAF_NODE:
        return "not_expandable", "the selected TreeItem is a leaf and has no expandable children"

    if (desired and _is_expanded(state)) or (not desired and state == _COLLAPSED):
        return "already_set", ""

    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the tree item"
    try:
        if desired:
            pattern.Expand()
        else:
            pattern.Collapse()
    except Exception:
        return "uncertain", "the tree item may have changed but the provider raised an error"

    deadline = time.monotonic() + min(1.5, ui._VERIFY_TIMEOUT_SECONDS)
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after the tree action; inspect before retrying"
        try:
            actual = int(pattern.CurrentExpandCollapseState)
        except Exception:
            return "uncertain", "the tree item may have changed but its state could not be verified"
        if actual == _LEAF_NODE:
            return "unverified", "the provider reported a leaf after the tree action"
        if actual not in (_COLLAPSED, _EXPANDED, _PARTIALLY_EXPANDED):
            return "unverified", "the provider reported an unknown state after the tree action"
        if (desired and _is_expanded(actual)) or (not desired and actual == _COLLAPSED):
            return "expanded" if desired else "collapsed", ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unverified", "the TreeItem did not report the requested state after the operation"
        time.sleep(min(ui._VERIFY_INTERVAL, remaining))


def _set_on_window(window_handle, process_id, item_label, desired, cancelled):
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
            return "cancelled", "the operation expired before tree navigation"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "TreeItem":
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
        return "missing_item", "no visible, enabled, non-sensitive TreeItem has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_item", "more than one TreeItem has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before tree navigation"
    try:
        pattern = matches[0].iface_expand_collapse
    except Exception:
        return "unsupported", "the TreeItem does not expose ExpandCollapsePattern"
    return _set_expand_collapse(pattern, desired, cancelled)


def _requires_confirmation(label: str) -> bool:
    return ui_checkbox._needs_confirmation(label) or ui._requires_confirmation(label)


def _set_tree_item_expanded_in_window(item_label: str, desired: bool,
                                     window_handle: int, process_id: int) -> str:
    """Run the verified change against a previously captured HWND/PID."""
    if not ui._valid_window_process_id(process_id):
        return outcome.failed(
            "set_tree_item_expanded",
            "the original application process could not be verified",
            item=item_label,
        )
    completed, result, error = ui._run_uia(
        lambda cancelled: _set_on_window(
            window_handle, process_id, item_label, desired, cancelled,
        ),
        ui._INVOKE_TIMEOUT_SECONDS,
        exclusive_lock=ui._INVOKE_LOCK,
        pass_cancel_event=True,
    )
    if not completed or error is not None:
        return outcome.unverified(
            "set_tree_item_expanded",
            "the tree item may have changed; inspect it before retrying",
            item=item_label,
        )
    if isinstance(result, tuple):
        status, reason = result
        if status in {"busy", "cancelled"}:
            return outcome.ask("set_tree_item_expanded", reason, item=item_label)
    else:
        status, reason = "", "UI Automation returned an invalid result"
    if status in {"expanded", "collapsed", "already_set"}:
        return outcome.done(
            "set_tree_item_expanded",
            item=item_label,
            expanded=desired,
            verified=True,
            changed=status != "already_set",
        )
    if status in {"uncertain", "unverified"}:
        return outcome.unverified(
            "set_tree_item_expanded", reason, item=item_label,
        )
    if status == "ambiguous_item":
        return outcome.ask("set_tree_item_expanded", reason, item=item_label)
    return outcome.failed(
        "set_tree_item_expanded", reason or status, item=item_label,
    )


def set_tree_item_expanded(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("item_label") or "").split())
    desired = params.get("expanded")
    if not label or len(label) > 120:
        return outcome.failed("set_tree_item_expanded", "item_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("set_tree_item_expanded", "credential, verification-code and payment controls are not supported", item=label)
    if not isinstance(desired, bool):
        return outcome.failed("set_tree_item_expanded", "expanded must be a boolean true or false", item=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("set_tree_item_expanded", f"could not identify the active application ({type(exc).__name__})")

    run = lambda: _set_tree_item_expanded_in_window(label, desired, handle, process_id)
    if _requires_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("set_tree_item_expanded", "another confirmation is already waiting on the HUD", item=label)
        verb = "expand" if desired else "collapse"
        return confirm.request(
            key="set_tree_item_expanded",
            title=f"Confirm {verb}: {label}",
            detail=f"MARK will {verb} the visible tree item '{label}'. It will not activate child items or submit a form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "set_tree_item_expanded",
    "tier": "extra",
    "brief": "Expand or collapse one exact visible Windows TreeItem and verify its ExpandCollapsePattern state.",
    "description": (
        "Use only when the user explicitly asks to expand or collapse one visible Windows tree item, such as "
        "a folder in a navigation tree or a section in settings. Supply its exact accessible TreeItem label and "
        "expanded=true or false. The action requires one unique visible enabled non-sensitive TreeItem, "
        "revalidates the original window handle and process, and uses ExpandCollapsePattern. It treats a "
        "partially expanded node as expanded, skips already-correct states, and verifies the resulting state "
        "locally. A leaf item, unsupported pattern or ambiguous label is never modified. It does not activate "
        "a child item, open a file, submit or save a form. Provider errors or timeouts after Expand/Collapse "
        "are uncertain; inspect before retrying. Potentially high-impact labels require HUD confirmation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "item_label": {"type": "STRING", "description": "Exact accessible label of one visible, enabled, non-sensitive Windows TreeItem."},
            "expanded": {"type": "BOOLEAN", "description": "True to expand the tree item; false to collapse it."},
        },
        "required": ["item_label", "expanded"],
    },
    "handler": set_tree_item_expanded,
}
