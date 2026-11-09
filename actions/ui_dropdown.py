"""Exact, verified selection in an already-expanded Windows ComboBox."""
from __future__ import annotations
import threading
from core import outcome
from actions import ui_interaction as ui

def _label(value, field):
    text = " ".join(str(value or "").split())
    if not text or len(text) > 120:
        return "", f"{field} must contain 1 to 120 characters"
    if ui._is_sensitive_input_label(text):
        return "", f"{field} looks like a credential or sensitive field"
    return text, ""

def _select_pattern(pattern, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before selection"
    try:
        if bool(pattern.CurrentIsSelected):
            return "already_selected", ""
    except Exception:
        return "unsupported", "the option does not expose SelectionItemPattern"
    if cancelled.is_set():
        return "cancelled", "the operation expired before selection"
    try:
        pattern.Select()
    except Exception:
        return "uncertain", "selection may have occurred but the provider raised an error"
    try:
        if bool(pattern.CurrentIsSelected):
            return "selected", ""
    except Exception:
        return "uncertain", "the option was selected but its state could not be verified"
    return "unverified", "the exact option was not reported as selected"

def _find_combo(window, label):
    matches = []
    for index, control in enumerate(window.descendants()):
        if index >= ui._MAX_SCANNED_CONTROLS: break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "ComboBox": continue
            if bool(getattr(info, "is_offscreen", False)) or not control.is_enabled(): continue
            name = ui._safe_name(getattr(info, "name", ""))
            rect = control.rectangle()
            if ui._normalise(name) == ui._normalise(label) and rect.width() > 0 and rect.height() > 0:
                matches.append(control)
        except Exception: continue
    return matches

def _find_option(combo, label):
    matches = []
    for index, control in enumerate(combo.descendants()):
        if index >= ui._MAX_SCANNED_CONTROLS: break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "ListItem": continue
            if bool(getattr(info, "is_offscreen", False)) or not control.is_enabled(): continue
            name = ui._safe_name(getattr(info, "name", ""))
            rect = control.rectangle()
            if name and ui._normalise(name) == ui._normalise(label) and rect.width() > 0 and rect.height() > 0:
                matches.append(control)
        except Exception: continue
    return matches

def _select_worker(handle, process_id, combo_label, option_label, cancelled):
    from pywinauto import Desktop
    window = Desktop(backend="uia").window(handle=int(handle)).wrapper_object()
    if not window.is_visible(): return "missing_window", "the original application window is no longer visible"
    if not ui._valid_window_process_id(getattr(window.element_info, "process_id", 0), process_id):
        return "changed_window", "the original window now belongs to another process"
    combos = _find_combo(window, combo_label)
    if len(combos) != 1:
        return ("missing_combo" if not combos else "ambiguous_combo", "the exact ComboBox is not uniquely available")
    options = _find_option(combos[0], option_label)
    if len(options) != 1:
        return ("missing_option" if not options else "ambiguous_option", "the exact visible option is not uniquely available; dropdown must already be open")
    if cancelled.is_set(): return "cancelled", "the operation expired before selection"
    try: pattern = options[0].iface_selection_item
    except Exception: return "unsupported", "the option does not expose SelectionItemPattern"
    return _select_pattern(pattern, cancelled)

def select_dropdown_option(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    combo_label, error = _label(params.get("combo_label"), "combo_label")
    if error: return outcome.failed("select_dropdown_option", error)
    option_label, error = _label(params.get("option_label"), "option_label")
    if error: return outcome.failed("select_dropdown_option", error)
    try:
        from pywinauto import Desktop
        from actions.screen_understanding import _active_top_level_window
        window = _active_top_level_window(Desktop(backend="uia"))
        if window is None: raise RuntimeError("no active application window")
        handle, process_id = int(window.handle), int(window.element_info.process_id)
        if not ui._valid_window_process_id(process_id): raise RuntimeError("active process could not be identified")
        combos = _find_combo(window, combo_label)
        if len(combos) != 1:
            return outcome.ask("select_dropdown_option", "the exact ComboBox label is missing or ambiguous; nothing was selected", combo=combo_label)
        options = _find_option(combos[0], option_label)
        if len(options) != 1:
            return outcome.ask("select_dropdown_option", "the exact option is not uniquely visible; open the dropdown first", combo=combo_label, option=option_label)
        context, _ = ui._collect_accessible_controls(window)
        context_text = " ".join(ui._safe_name(x.get("name")) for x in context)
    except Exception as exc:
        return outcome.failed("select_dropdown_option", f"could not inspect the active application ({type(exc).__name__})")
    def run():
        completed, result, worker_error = ui._run_uia(
            lambda cancelled: _select_worker(handle, process_id, combo_label, option_label, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS, exclusive_lock=ui._INVOKE_LOCK, pass_cancel_event=True,
        )
        if not completed or worker_error is not None:
            return outcome.unverified("select_dropdown_option", "selection may have occurred; inspect the dropdown before retrying", combo=combo_label, option=option_label)
        status, reason = result
        if status in {"selected", "already_selected"}:
            return outcome.done("select_dropdown_option", combo=combo_label, option=option_label, verified=True, changed=status == "selected")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("select_dropdown_option", reason, combo=combo_label, option=option_label)
        if status in {"ambiguous_combo", "ambiguous_option"}:
            return outcome.ask("select_dropdown_option", reason, combo=combo_label, option=option_label)
        return outcome.failed("select_dropdown_option", reason or status, combo=combo_label, option=option_label)
    if ui._requires_confirmation(combo_label, option_label, context_text):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("select_dropdown_option", "another confirmation is already waiting on the HUD", combo=combo_label)
        return confirm.request(key="select_dropdown_option", title=f"Confirm selection: {option_label}",
            detail=f"MARK will select '{option_label}' in '{combo_label}'. It will not submit or save the form.", run=run)
    return run()

TOOL = {
    "name": "select_dropdown_option", "tier": "core",
    "brief": "Select one exact visible option in an already-open Windows dropdown and verify it.",
    "description": ("Use only when the user explicitly asks to choose an exact option from a currently open Windows ComboBox. "
        "Supply the exact accessible ComboBox label and exact visible ListItem option label. The option must already be visible; "
        "this action does not expand dropdowns, type, submit or save. It requires unique visible enabled controls and verifies "
        "SelectionItemPattern locally. Ambiguous or missing options are not guessed. Potentially high-impact choices require HUD "
        "confirmation. No screenshot, clipboard, keystroke simulation, background polling or extra model/API call is used."),
    "parameters": {"type":"OBJECT","properties":{
        "combo_label":{"type":"STRING","description":"Exact accessible label of the visible ComboBox."},
        "option_label":{"type":"STRING","description":"Exact accessible label of one visible ListItem option in the already-open dropdown."}},
        "required":["combo_label","option_label"]},
    "handler": select_dropdown_option,
}
