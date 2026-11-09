"""Select one exact Windows RadioButton through a verified SelectionItemPattern."""
from __future__ import annotations

import time

from core import outcome
from actions import ui_interaction as ui
from actions import ui_checkbox


def _select_pattern(pattern, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before radio selection"
    try:
        if bool(pattern.CurrentIsSelected):
            return "already_selected", ""
    except Exception:
        return "unsupported", "the radio option does not expose a readable SelectionItemPattern state"
    if cancelled.is_set():
        return "cancelled", "the operation expired before radio selection"
    try:
        pattern.Select()
    except Exception:
        return "uncertain", "the radio option may have been selected but the provider raised an error"

    deadline = time.monotonic() + min(1.5, ui._VERIFY_TIMEOUT_SECONDS)
    while True:
        if cancelled.is_set():
            return "uncertain", "the operation expired after Select; inspect before retrying"
        try:
            selected = bool(pattern.CurrentIsSelected)
        except Exception:
            return "uncertain", "the option may have been selected but its state could not be verified"
        if selected:
            return "selected", ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unverified", "the radio option did not report selected after the operation"
        time.sleep(min(ui._VERIFY_INTERVAL, remaining))


def _select_on_window(window_handle, process_id, radio_label, cancelled):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if int(getattr(window, "handle", 0) or 0) != int(window_handle):
        return "changed_window", "the original window handle changed"
    actual_pid = getattr(window.element_info, "process_id", 0)
    if not ui._valid_window_process_id(actual_pid, process_id):
        return "changed_window", "the original window now belongs to another process"

    wanted = ui._normalise(radio_label)
    matches = []
    for index, control in enumerate(window.descendants()):
        if cancelled.is_set():
            return "cancelled", "the operation expired before radio option selection"
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "RadioButton":
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
        return "missing_option", "no visible, enabled, non-sensitive RadioButton has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_option", "more than one RadioButton has that exact accessible label"
    if cancelled.is_set():
        return "cancelled", "the operation expired before radio selection"
    try:
        pattern = matches[0].iface_selection_item
    except Exception:
        return "unsupported", "the RadioButton does not expose SelectionItemPattern"
    return _select_pattern(pattern, cancelled)


def select_radio_option(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    label = " ".join(str(params.get("radio_label") or "").split())
    if not label or len(label) > 120:
        return outcome.failed("select_radio_option", "radio_label must contain 1 to 120 characters")
    if ui._is_sensitive_input_label(label):
        return outcome.failed("select_radio_option", "credential, verification-code and payment controls are not supported", option=label)
    try:
        snapshot = ui._snapshot()
        handle = int(snapshot["handle"])
        process_id = int(snapshot["process_id"])
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
    except Exception as exc:
        return outcome.failed("select_radio_option", f"could not identify the active application ({type(exc).__name__})")

    def run():
        completed, result, error = ui._run_uia(
            lambda cancelled: _select_on_window(handle, process_id, label, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS,
            exclusive_lock=ui._INVOKE_LOCK,
            pass_cancel_event=True,
        )
        if not completed or error is not None:
            return outcome.unverified("select_radio_option", "the radio option may have changed; inspect it before retrying", option=label)
        if isinstance(result, tuple):
            status, reason = result
            if status in {"busy", "cancelled"}:
                return outcome.ask("select_radio_option", reason, option=label)
        else:
            status, reason = "", "UI Automation returned an invalid result"
        if status in {"selected", "already_selected"}:
            return outcome.done("select_radio_option", option=label,
                                 selected=True, verified=True,
                                 changed=status == "selected")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("select_radio_option", reason, option=label)
        if status in {"ambiguous_option"}:
            return outcome.ask("select_radio_option", reason, option=label)
        return outcome.failed("select_radio_option", reason or status, option=label)

    if ui_checkbox._needs_confirmation(label):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("select_radio_option", "another confirmation is already waiting on the HUD", option=label)
        return confirm.request(
            key="select_radio_option",
            title=f"Confirm option: {label}",
            detail=f"MARK will select '{label}'. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "select_radio_option",
    "tier": "extra",
    "brief": "Select one exact Windows RadioButton using SelectionItemPattern and verify its selected state.",
    "description": (
        "Use only when the user explicitly asks to select one visible Windows RadioButton. "
        "Supply its exact accessible label. The action requires one unique enabled non-sensitive option, "
        "revalidates the original window handle and process, and uses SelectionItemPattern.Select(). "
        "It verifies IsSelected locally and never selects an already-selected option twice. It does not "
        "submit or save the form. Consent, privacy, permission, subscription and other high-impact labels "
        "require HUD confirmation. An exception or timeout after Select is uncertain; inspect before retrying. "
        "No screenshot, clipboard, simulated keystroke, background polling or extra API call is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "radio_label": {"type": "STRING", "description": "Exact accessible label of one visible Windows RadioButton option."},
        },
        "required": ["radio_label"],
    },
    "handler": select_radio_option,
}
