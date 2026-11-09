"""Exact, verified selection of an already-visible Windows calendar date."""
from __future__ import annotations

from core import outcome
from actions import ui_interaction as ui


def _label(value, field):
    text = " ".join(str(value or "").split())
    if not text or len(text) > 120:
        return "", f"{field} must contain 1 to 120 characters"
    if ui._is_sensitive_input_label(text):
        return "", f"{field} looks like a credential or sensitive field"
    return text, ""


def _find_calendar(window, label):
    matches = []
    for index, control in enumerate(window.descendants()):
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "Calendar":
                continue
            if bool(getattr(info, "is_offscreen", False)) or not control.is_enabled():
                continue
            name = ui._safe_name(getattr(info, "name", ""))
            rect = control.rectangle()
            if ui._normalise(name) == ui._normalise(label) and rect.width() > 0 and rect.height() > 0:
                matches.append(control)
        except Exception:
            continue
    return matches


def _find_date(calendar, label):
    matches = []
    for index, control in enumerate(calendar.descendants()):
        if index >= ui._MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "CalendarItem":
                continue
            if bool(getattr(info, "is_offscreen", False)) or not control.is_enabled():
                continue
            name = ui._safe_name(getattr(info, "name", ""))
            rect = control.rectangle()
            if name and ui._normalise(name) == ui._normalise(label) and rect.width() > 0 and rect.height() > 0:
                matches.append(control)
        except Exception:
            continue
    return matches


def _select_pattern(pattern, cancelled):
    if cancelled.is_set():
        return "cancelled", "the operation expired before date selection"
    try:
        if bool(pattern.CurrentIsSelected):
            return "already_selected", ""
    except Exception:
        return "unsupported", "the calendar item does not expose SelectionItemPattern"
    if cancelled.is_set():
        return "cancelled", "the operation expired before date selection"
    try:
        pattern.Select()
    except Exception:
        return "uncertain", "date selection may have occurred but the provider raised an error"
    try:
        if bool(pattern.CurrentIsSelected):
            return "selected", ""
    except Exception:
        return "uncertain", "the date may be selected but its state could not be verified"
    return "unverified", "the exact calendar date was not reported as selected"


def _select_worker(handle, process_id, calendar_label, date_label, cancelled):
    from pywinauto import Desktop
    window = Desktop(backend="uia").window(handle=int(handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original application window is no longer visible"
    if not ui._valid_window_process_id(getattr(window.element_info, "process_id", 0), process_id):
        return "changed_window", "the original window now belongs to another process"
    calendars = _find_calendar(window, calendar_label)
    if len(calendars) != 1:
        return ("missing_calendar" if not calendars else "ambiguous_calendar",
                "the exact Calendar control is not uniquely available")
    dates = _find_date(calendars[0], date_label)
    if len(dates) != 1:
        return ("missing_date" if not dates else "ambiguous_date",
                "the exact visible CalendarItem is not uniquely available; navigate to its month first")
    if cancelled.is_set():
        return "cancelled", "the operation expired before date selection"
    try:
        pattern = dates[0].iface_selection_item
    except Exception:
        return "unsupported", "the date does not expose SelectionItemPattern"
    return _select_pattern(pattern, cancelled)


def select_calendar_date(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    calendar_label, error = _label(params.get("calendar_label"), "calendar_label")
    if error:
        return outcome.failed("select_calendar_date", error)
    date_label, error = _label(params.get("date_label"), "date_label")
    if error:
        return outcome.failed("select_calendar_date", error)
    try:
        from pywinauto import Desktop
        from actions.screen_understanding import _active_top_level_window
        window = _active_top_level_window(Desktop(backend="uia"))
        if window is None:
            raise RuntimeError("no active application window")
        handle, process_id = int(window.handle), int(window.element_info.process_id)
        if not ui._valid_window_process_id(process_id):
            raise RuntimeError("active process could not be identified")
        calendars = _find_calendar(window, calendar_label)
        if len(calendars) != 1:
            return outcome.ask("select_calendar_date", "the exact Calendar label is missing or ambiguous; nothing was selected", calendar=calendar_label)
        dates = _find_date(calendars[0], date_label)
        if len(dates) != 1:
            return outcome.ask("select_calendar_date", "the exact date is not uniquely visible; navigate to its month first", calendar=calendar_label, date=date_label)
        context, _ = ui._collect_accessible_controls(window)
        context_text = " ".join(ui._safe_name(x.get("name")) for x in context)
    except Exception as exc:
        return outcome.failed("select_calendar_date", f"could not inspect the active application ({type(exc).__name__})")

    def run():
        completed, result, worker_error = ui._run_uia(
            lambda cancelled: _select_worker(handle, process_id, calendar_label, date_label, cancelled),
            ui._INVOKE_TIMEOUT_SECONDS, exclusive_lock=ui._INVOKE_LOCK, pass_cancel_event=True,
        )
        if not completed or worker_error is not None:
            return outcome.unverified("select_calendar_date", "date selection may have occurred; inspect the calendar before retrying", calendar=calendar_label, date=date_label)
        status, reason = result
        if status in {"selected", "already_selected"}:
            return outcome.done("select_calendar_date", calendar=calendar_label, date=date_label, verified=True, changed=status == "selected")
        if status in {"uncertain", "unverified"}:
            return outcome.unverified("select_calendar_date", reason, calendar=calendar_label, date=date_label)
        if status in {"ambiguous_calendar", "ambiguous_date"}:
            return outcome.ask("select_calendar_date", reason, calendar=calendar_label, date=date_label)
        return outcome.failed("select_calendar_date", reason or status, calendar=calendar_label, date=date_label)

    if ui._requires_confirmation(calendar_label, date_label, context_text):
        from core import confirm
        if confirm.pending_title():
            return outcome.ask("select_calendar_date", "another confirmation is already waiting on the HUD", calendar=calendar_label)
        return confirm.request(
            key="select_calendar_date",
            title=f"Confirm date: {date_label}",
            detail=f"MARK will select '{date_label}' in '{calendar_label}'. It will not submit or save the form.",
            run=run,
        )
    return run()


TOOL = {
    "name": "select_calendar_date",
    "tier": "core",
    "brief": "Select one exact visible date in an already-open Windows calendar and verify it.",
    "description": (
        "Use only when the user explicitly asks to select an exact date in a currently visible Windows Calendar control. "
        "Supply the exact accessible Calendar label and exact visible CalendarItem label (use the label exposed by Windows UI Automation). "
        "The target date must already be visible; this action does not navigate months, type, submit or save. "
        "It requires unique visible enabled controls and verifies SelectionItemPattern locally. Missing or ambiguous dates are never guessed. "
        "Potentially high-impact choices require HUD confirmation. No screenshot, clipboard, keystroke simulation, background polling or extra model/API call is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "calendar_label": {"type": "STRING", "description": "Exact accessible label of the visible Calendar control."},
            "date_label": {"type": "STRING", "description": "Exact accessible label of one visible CalendarItem date."},
        },
        "required": ["calendar_label", "date_label"],
    },
    "handler": select_calendar_date,
}
