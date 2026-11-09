"""Local UI Automation clicks with a checked, exact postcondition.

All COM work is isolated to short-lived, explicitly initialized worker threads.
No COM element is passed between threads. A delayed confirmation is bound to
the original window handle and control identity, not whichever window the HUD
happens to have in the foreground when the user confirms.
"""
from __future__ import annotations

import re
import sys
import threading
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
    "purchase", "buy", "pay", "payment", "checkout", "order", "transfer",
    "unsubscribe", "revoke", "cancel", "confirm", "upload", "share", "grant",
    "erase", "destroy", "wipe", "reset", "format", "authorize", "sign",
    "deleted", "removed", "sent", "submitted", "published", "transferred",
    "purchased", "paid", "uploaded", "shared", "granted", "posted", "created",
    "closed", "cancelled", "canceled", "permanent", "permanently",
    "irreversible", "irreversibly", "usuń", "usun", "usunąć", "usunac",
    "usuwanie", "usunięcie", "usuniecie", "usunięcia", "usuniecia",
    "wyślij", "wyslij", "wysłać", "wyslac", "wysyłanie", "wysylanie",
    "opublikuj", "opublikować", "opublikowac", "kup", "kupić", "kupic",
    "zakup", "zakupić", "zakupic", "płatność", "platnosc", "płatności",
    "platnosci", "zapłać", "zaplac", "zapłacić", "zaplacic", "zamów",
    "zamow", "zamówienie", "zamowienie", "przelew", "przelewu", "przelać",
    "przelac", "anuluj", "anulować", "anulowac", "anulowanie", "potwierdź",
    "potwierdz", "potwierdzić", "potwierdzic", "zatwierdź", "zatwierdz",
    "zatwierdzić", "zatwierdzic", "udostępnij", "udostepnij",
    "udostępnianie", "udostepnianie", "usunięto", "usunieto", "wysłano",
    "wyslano", "opublikowano", "przelano",
)
_MAX_CONTROLS = 240
_MAX_SCANNED_CONTROLS = 1200
_SNAPSHOT_TIMEOUT_SECONDS = 4.0
_INVOKE_TIMEOUT_SECONDS = 4.0
_VERIFY_TIMEOUT_SECONDS = 6.0
_VERIFY_ATTEMPTS = 8
_VERIFY_INTERVAL = 0.25
_RECT_TOLERANCE = 8
_MAX_INPUT_CHARS = 4000
_SENSITIVE_INPUT_MARKERS = (
    "password", "passwd", "passcode", "pin code", "security code",
    "one-time code", "otp", "recovery code", "verification code",
    "authentication code", "credit card", "card number", "cvv", "cvc",
    "iban", "bank account", "routing number", "private key", "api key",
    "access token", "secret key", "security token",
)
_COMTYPES_IMPORT_LOCK = threading.Lock()
_SNAPSHOT_LOCK = threading.Lock()
_INVOKE_LOCK = threading.Lock()


def _normalise(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _safe_name(value: object) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.casefold()
    if not text or any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return ""
    return text[:240]


def _requires_confirmation(*texts: str) -> bool:
    words = set()
    for text in texts:
        words.update(re.findall(r"[\w-]+", _normalise(text)))
    return bool(words.intersection(_HIGH_IMPACT_WORDS))


def _matching_controls(controls: list[dict], label: str) -> list[dict]:
    wanted = _normalise(label)
    if not wanted:
        return []
    return [
        item for item in controls
        if item.get("role") in _INVOKABLE_ROLES
        and _normalise(item.get("name")) == wanted
    ]


def _has_exact_expected_text(records: list[dict], expected: str) -> bool:
    """Match a whole accessible element name, never a substring.

    In particular, "Saved" must not match "Not saved" or "Unsaved".
    """
    wanted = _normalise(expected)
    if not wanted:
        return False
    return any(
        isinstance(item, dict)
        and item.get("role") in _ALLOWED_ROLES
        and not item.get("password")
        and not item.get("offscreen")
        and _normalise(_safe_name(item.get("name"))) == wanted
        for item in records
    )


def _valid_window_process_id(actual, expected=None) -> bool:
    try:
        actual_pid = int(actual)
    except (TypeError, ValueError):
        return False
    if actual_pid <= 0:
        return False
    if expected is None:
        return True
    try:
        return actual_pid == int(expected)
    except (TypeError, ValueError):
        return False


def _control_signature(info, rect) -> dict:
    automation_id = ""
    for attr in ("automation_id", "auto_id"):
        try:
            automation_id = str(getattr(info, attr, "") or "").strip()
        except Exception:
            automation_id = ""
        if automation_id:
            break
    try:
        runtime_id = tuple(getattr(info, "runtime_id", ()) or ())
    except Exception:
        runtime_id = ()
    try:
        bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        bounds = None
    return {
        "automation_id": automation_id,
        "runtime_id": runtime_id,
        "rect": bounds,
    }


def _same_control_identity(candidate: dict, original: dict) -> bool:
    if candidate.get("role") != original.get("role"):
        return False
    old_id = str(original.get("automation_id") or "")
    new_id = str(candidate.get("automation_id") or "")
    if old_id:
        return new_id == old_id
    old_runtime = tuple(original.get("runtime_id") or ())
    new_runtime = tuple(candidate.get("runtime_id") or ())
    if old_runtime:
        return new_runtime == old_runtime
    old_rect, new_rect = original.get("rect"), candidate.get("rect")
    if not old_rect or not new_rect or len(old_rect) != 4 or len(new_rect) != 4:
        return False
    return all(
        abs(int(a) - int(b)) <= _RECT_TOLERANCE
        for a, b in zip(old_rect, new_rect)
    )


def _run_bounded(callback, timeout: float, cancel_event=None):
    """Run a callback once; a timed-out worker may not start later."""
    finished = threading.Event()
    cancelled = cancel_event or threading.Event()
    result = {}

    def worker():
        try:
            # If the scheduler did not start this worker before the caller's
            # deadline, make the operation a no-op rather than a late action.
            if cancelled.is_set():
                return
            result["value"] = callback()
        except Exception as exc:
            result["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(
        target=worker, daemon=True, name="mark-ui-automation",
    )
    thread.start()
    if not finished.wait(max(0.01, float(timeout))):
        cancelled.set()
        return False, None, None
    return True, result.get("value"), result.get("error")


def _run_uia(callback, timeout: float, exclusive_lock=None,
              pass_cancel_event: bool = False):
    """Run fresh UIA objects in their own initialized COM apartment.

    comtypes initializes the importing thread automatically. If this is its
    first import in the worker, that initialization is already in place;
    otherwise each worker initializes COM explicitly. An optional lock is
    acquired before COM setup so a timed-out worker reserves the single
    invocation slot until it truly exits.
    """
    cancelled = threading.Event()

    def with_com():
        acquired = False
        if cancelled.is_set():
            return ("cancelled", "the operation expired before COM was initialized")
        if exclusive_lock is not None:
            acquired = exclusive_lock.acquire(blocking=False)
            if not acquired:
                return ("busy", "another UI Automation invocation is still running")
        try:
            if cancelled.is_set():
                return ("cancelled", "the operation expired before COM setup")
            with _COMTYPES_IMPORT_LOCK:
                # pywinauto chooses the process COM threading mode (MTA by
                # default) before importing comtypes. comtypes initializes
                # the importing thread automatically, using sys.coinit_flags.
                # Importing it first would silently initialize this worker as
                # STA and can conflict with pywinauto's subsequent MTA setup.
                already_loaded = "comtypes" in sys.modules
                import pywinauto  # noqa: F401
                import comtypes
                # If comtypes was loaded before this worker, its import-time
                # initialization happened on a different thread. Initialize
                # this worker explicitly and balance it below.
                if already_loaded:
                    comtypes.CoInitialize()
            try:
                # COM setup itself can be slow. Check the deadline again before
                # crossing the side-effect boundary into InvokePattern.
                if cancelled.is_set():
                    return ("cancelled", "the operation expired before invoking the control")
                return callback(cancelled) if pass_cancel_event else callback()
            finally:
                comtypes.CoUninitialize()
        finally:
            if acquired:
                exclusive_lock.release()

    return _run_bounded(with_com, timeout, cancel_event=cancelled)


def _collect_accessible_controls(window, include_wrappers: bool = False):
    records = []
    clickables = []
    for scanned, control in enumerate(window.descendants()):
        if scanned >= _MAX_SCANNED_CONTROLS or len(records) >= _MAX_CONTROLS:
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
            signature = _control_signature(info, rect)
            record = {"role": role, "name": label}
            records.append(record)
            if role in _INVOKABLE_ROLES and control.is_enabled():
                item = {
                    "role": role, "name": label,
                    "automation_id": signature["automation_id"],
                    "runtime_id": signature["runtime_id"],
                    "rect": signature["rect"],
                }
                if include_wrappers:
                    item["control"] = control
                clickables.append(item)
        except Exception:
            # A stale or inaccessible element must not invalidate the rest.
            continue
    return records, clickables


def _read_window_snapshot(window_handle=None, expected_process_id=None) -> dict:
    """Read one specific window; None means the current active window.

    The handle form is intentionally independent of foreground focus so a
    confirmation button in MARK's HUD cannot redirect the pending operation.
    """
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError(
            "pywinauto is not installed; local UI Automation is unavailable"
        ) from exc

    desktop = Desktop(backend="uia")
    if window_handle is None:
        from actions.screen_understanding import _active_top_level_window
        window = _active_top_level_window(desktop)
    else:
        window = desktop.window(handle=int(window_handle)).wrapper_object()
    if window is None or not window.is_visible():
        raise RuntimeError("the target window is no longer visible")
    handle = getattr(window, "handle", None)
    if handle is None:
        raise RuntimeError("the target window has no stable Windows handle")
    if window_handle is not None and int(handle) != int(window_handle):
        raise RuntimeError("the target window handle changed")

    process_id = getattr(window.element_info, "process_id", 0)
    if not _valid_window_process_id(process_id, expected_process_id):
        raise RuntimeError(
            "the window handle no longer belongs to the original application process"
        )
    records, clickables = _collect_accessible_controls(window)
    return {
        "handle": int(handle),
        "process_id": int(process_id),
        "records": records,
        "clickables": clickables,
    }


def _snapshot(window_handle=None, expected_process_id=None) -> dict:
    completed, value, error = _run_uia(
        lambda: _read_window_snapshot(window_handle, expected_process_id),
        _SNAPSHOT_TIMEOUT_SECONDS,
        exclusive_lock=_SNAPSHOT_LOCK,
    )
    if not completed:
        raise TimeoutError("UI Automation could not inspect the window within 4 seconds")
    if error is not None:
        raise RuntimeError(
            f"UI Automation could not inspect the window ({type(error).__name__})"
        ) from error
    if isinstance(value, tuple) and value and value[0] in {"busy", "cancelled"}:
        raise RuntimeError(value[1])
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise RuntimeError("UI Automation returned an invalid window snapshot")
    return value


def _invoke_on_window(window_handle: int, expected_process_id: int,
                      label: str, original_signature: dict,
                      cancelled: threading.Event) -> tuple[str, str]:
    """Re-find and invoke the same control inside the target window."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = desktop.window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original window is no longer visible"
    process_id = getattr(window.element_info, "process_id", 0)
    if not _valid_window_process_id(process_id, expected_process_id):
        return "changed_window", "the window handle now belongs to another process"

    _records, clickables = _collect_accessible_controls(
        window, include_wrappers=True,
    )
    matches = _matching_controls(clickables, label)
    if not matches:
        return "missing_control", "the original control is no longer available"
    if len(matches) != 1:
        return "ambiguous", "the control label is no longer unique"
    target = matches[0]
    if not _same_control_identity(target, original_signature):
        return "changed_control", "the matching control is not the one originally identified"

    # Timeout cancellation is checked after control discovery and once more
    # after resolving InvokePattern, immediately before the side-effect call.
    if cancelled.is_set():
        return "cancelled", "the operation expired before invoking the control"
    # A missing InvokePattern proves that no action was sent; an exception
    # during Invoke itself is ambiguous.
    try:
        invoke_pattern = target["control"].iface_invoke
    except Exception as exc:
        return (
            "unsupported_pattern",
            f"the control does not expose InvokePattern ({type(exc).__name__})",
        )
    if cancelled.is_set():
        return "cancelled", "the operation expired before invoking the control"
    invoke_pattern.Invoke()
    return "invoked", ""



def _read_checkbox_on_window(window_handle: int, expected_process_id: int,
                               label: str) -> tuple[str, bool | None, dict | None, str]:
    """Read one exact accessible CheckBox state without changing it."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = desktop.window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", None, None, "the original window is no longer visible"
    process_id = getattr(window.element_info, "process_id", 0)
    if not _valid_window_process_id(process_id, expected_process_id):
        return "changed_window", None, None, "the window handle now belongs to another process"

    wanted = _normalise(label)
    matches = []
    for scanned, control in enumerate(window.descendants()):
        if scanned >= _MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "CheckBox":
                continue
            if bool(getattr(info, "is_password", False)) or bool(getattr(info, "is_offscreen", False)):
                continue
            name = str(getattr(info, "name", "") or "")
            if _is_sensitive_input_label(name) or _normalise(name) != wanted:
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0 or not control.is_enabled():
                continue
            signature = _control_signature(info, rect)
            matches.append((control, signature))
        except Exception:
            continue

    if not matches:
        return "missing_checkbox", None, None, "no visible, enabled, non-sensitive CheckBox has that exact label"
    if len(matches) != 1:
        return "ambiguous_checkbox", None, None, "more than one CheckBox has that exact accessible label"

    control, signature = matches[0]
    try:
        pattern = control.iface_toggle
        state = int(pattern.CurrentToggleState)
    except Exception:
        return "unsupported_toggle_pattern", None, None, "the CheckBox does not expose UI Automation TogglePattern"
    if state not in (0, 1):
        return "mixed_state", None, None, "the CheckBox is indeterminate; MARK will not guess how to change it"
    plain_signature = {
        "role": "CheckBox",
        "automation_id": signature["automation_id"],
        "runtime_id": signature["runtime_id"],
        "rect": signature["rect"],
    }
    return "found", bool(state), plain_signature, ""


def _inspect_checkbox(label: str, window_handle: int,
                      expected_process_id: int) -> tuple[str, bool | None, dict | None, str]:
    """Inspect a checkbox in a bounded worker; return only state and identity."""
    completed, result, error = _run_uia(
        lambda: _read_checkbox_on_window(
            window_handle, expected_process_id, label,
        ),
        _SNAPSHOT_TIMEOUT_SECONDS,
        exclusive_lock=_SNAPSHOT_LOCK,
    )
    if not completed:
        return "timeout", None, None, "UI Automation did not inspect the CheckBox within 4 seconds"
    if error is not None:
        return "error", None, None, f"UI Automation could not inspect the CheckBox ({type(error).__name__})"
    if not isinstance(result, tuple) or len(result) != 4:
        return "error", None, None, "UI Automation returned an invalid CheckBox state"
    return result


def _set_checkbox_on_window(window_handle: int, expected_process_id: int,
                             label: str, desired: bool, signature: dict,
                             cancelled: threading.Event) -> tuple[str, str]:
    """Re-find the same CheckBox, toggle once, and verify its exact state."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = desktop.window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original window is no longer visible"
    process_id = getattr(window.element_info, "process_id", 0)
    if not _valid_window_process_id(process_id, expected_process_id):
        return "changed_window", "the window handle now belongs to another process"

    wanted = _normalise(label)
    matches = []
    for scanned, control in enumerate(window.descendants()):
        if scanned >= _MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "CheckBox":
                continue
            if bool(getattr(info, "is_password", False)) or bool(getattr(info, "is_offscreen", False)):
                continue
            name = str(getattr(info, "name", "") or "")
            if _is_sensitive_input_label(name) or _normalise(name) != wanted:
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0 or not control.is_enabled():
                continue
            candidate_signature = _control_signature(info, rect)
            candidate = {
                "role": "CheckBox",
                "automation_id": candidate_signature["automation_id"],
                "runtime_id": candidate_signature["runtime_id"],
                "rect": candidate_signature["rect"],
            }
            matches.append((control, candidate))
        except Exception:
            continue

    if not matches:
        return "missing_checkbox", "the original CheckBox is no longer available"
    if len(matches) != 1:
        return "ambiguous_checkbox", "the CheckBox label is no longer unique"
    control, candidate = matches[0]
    if not _same_control_identity(candidate, signature):
        return "changed_checkbox", "the CheckBox is no longer the one originally identified"

    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the CheckBox"
    try:
        pattern = control.iface_toggle
        current_state = int(pattern.CurrentToggleState)
    except Exception:
        return "unsupported_toggle_pattern", "the CheckBox no longer exposes TogglePattern"
    if current_state not in (0, 1):
        return "mixed_state", "the CheckBox is indeterminate; no change was made"
    if bool(current_state) == desired:
        return "already_set", ""

    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the CheckBox"
    try:
        pattern.Toggle()
    except Exception:
        return "invoke_uncertain", "the CheckBox TogglePattern raised an error; its state is unknown"
    try:
        final_state = int(pattern.CurrentToggleState)
    except Exception:
        return "invoke_uncertain", "the CheckBox was toggled but its state could not be verified"
    if final_state not in (0, 1):
        return "verification_mismatch", "the CheckBox became indeterminate after toggling"
    if bool(final_state) == desired:
        return "toggled", ""
    return "verification_mismatch", "the CheckBox state did not match the requested state after toggling"


def _set_checkbox_and_verify(label: str, desired: bool,
                             window_handle: int,
                             expected_process_id: int,
                             signature: dict) -> str:
    if not isinstance(desired, bool):
        return outcome.failed(
            "set_checkbox_and_verify",
            "checked must be a boolean true or false",
            checkbox=label,
        )
    if _is_sensitive_input_label(label):
        return outcome.failed(
            "set_checkbox_and_verify",
            "credential-related CheckBoxes are not supported",
            checkbox=label,
        )

    completed, result, error = _run_uia(
        lambda cancelled: _set_checkbox_on_window(
            window_handle, expected_process_id, label, desired,
            signature, cancelled,
        ),
        _INVOKE_TIMEOUT_SECONDS,
        exclusive_lock=_INVOKE_LOCK,
        pass_cancel_event=True,
    )
    if not completed:
        return outcome.unverified(
            "set_checkbox_and_verify",
            "UI Automation did not return within 4 seconds; the CheckBox may have changed. Do not retry blindly.",
            checkbox=label, timed_out=True,
        )
    if error is not None:
        return outcome.unverified(
            "set_checkbox_and_verify",
            f"UI Automation raised {type(error).__name__}; the CheckBox state is unknown. Do not retry blindly.",
            checkbox=label,
        )

    status, reason = result
    if status in {"toggled", "already_set"}:
        return outcome.done(
            "set_checkbox_and_verify", checkbox=label,
            checked=desired, verified=True, changed=(status == "toggled"),
        )
    if status in {"invoke_uncertain", "verification_mismatch"}:
        return outcome.unverified(
            "set_checkbox_and_verify", reason, checkbox=label,
        )
    if status == "ambiguous_checkbox":
        return outcome.ask(
            "set_checkbox_and_verify", reason, checkbox=label,
        )
    return outcome.failed(
        "set_checkbox_and_verify", reason or status, checkbox=label,
    )


def _is_sensitive_input_label(label: str) -> bool:
    normal = _normalise(label)
    if not normal:
        return True
    words = set(re.findall(r"[\w-]+", normal))
    if "pin" in words or "otp" in words:
        return True
    return any(marker in normal for marker in _SENSITIVE_INPUT_MARKERS)


def _looks_like_secret_payload(value: str) -> bool:
    text = value.strip()
    upper = text.upper()
    if "-----BEGIN " in upper and "PRIVATE KEY-----" in upper:
        return True
    compact = not any(ch.isspace() for ch in text)
    if compact and len(text) >= 40 and re.fullmatch(r"[A-Za-z0-9_+/=-]+", text):
        return True
    return bool(re.match(
        r"(?i)^(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|"
        r"github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})$",
        text,
    ))


def _set_and_verify_value_pattern(pattern, text: str,
                                  cancelled: threading.Event) -> tuple[str, str]:
    """Set one already-identified ValuePattern and compare its value locally."""
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the field"
    try:
        if bool(pattern.CurrentIsReadOnly):
            return "readonly", "the target field is read-only"
        current_value = str(pattern.CurrentValue or "")
    except Exception:
        return "unsupported_value_pattern", "the field did not expose a readable ValuePattern"
    if current_value == text:
        return "already_set", ""
    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the field"
    try:
        pattern.SetValue(text)
    except Exception:
        # SetValue may have reached the application before the provider failed.
        return "invoke_uncertain", "the field setter raised an error; its result is unknown"
    try:
        actual_value = str(pattern.CurrentValue or "")
    except Exception:
        return "invoke_uncertain", "the field was changed but its value could not be verified"
    if actual_value == text:
        return "filled", ""
    return "verification_mismatch", "the field value did not match the requested text after setting"


def _fill_text_on_window(window_handle: int, expected_process_id: int,
                         field_label: str, text: str,
                         cancelled: threading.Event) -> tuple[str, str]:
    """Find one accessible non-sensitive Edit field and fill it locally."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    window = desktop.window(handle=int(window_handle)).wrapper_object()
    if not window.is_visible():
        return "missing_window", "the original window is no longer visible"
    process_id = getattr(window.element_info, "process_id", 0)
    if not _valid_window_process_id(process_id, expected_process_id):
        return "changed_window", "the window handle now belongs to another process"

    wanted = _normalise(field_label)
    matches = []
    for scanned, control in enumerate(window.descendants()):
        if scanned >= _MAX_SCANNED_CONTROLS:
            break
        try:
            info = control.element_info
            if str(getattr(info, "control_type", "") or "") != "Edit":
                continue
            if bool(getattr(info, "is_password", False)):
                continue
            if bool(getattr(info, "is_offscreen", False)):
                continue
            name = str(getattr(info, "name", "") or "")
            if _is_sensitive_input_label(name):
                continue
            if _normalise(name) != wanted:
                continue
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            if not control.is_enabled():
                continue
            matches.append(control)
        except Exception:
            continue

    if not matches:
        return "missing_field", "no visible, enabled, non-sensitive Edit field has that exact accessible label"
    if len(matches) != 1:
        return "ambiguous_field", "more than one Edit field has that exact accessible label"

    if cancelled.is_set():
        return "cancelled", "the operation expired before changing the field"
    try:
        value_pattern = matches[0].iface_value
    except Exception:
        return "unsupported_value_pattern", "the field does not expose UI Automation ValuePattern"
    return _set_and_verify_value_pattern(value_pattern, text, cancelled)


def _fill_text_and_verify(field_label: str, text: str,
                          expected_window_handle: int,
                          expected_process_id: int) -> str:
    """Fill one exact safe Edit field; the typed value never leaves this function."""
    label = " ".join(str(field_label or "").split())
    value = str(text) if isinstance(text, str) else ""
    if not label or len(label) > 120:
        return outcome.failed(
            "fill_text_and_verify", "field_label is missing or too long",
        )
    if _is_sensitive_input_label(label):
        return outcome.failed(
            "fill_text_and_verify",
            "credential, verification-code and payment fields are not supported",
            field=label,
        )
    if not value or len(value) > _MAX_INPUT_CHARS or "\x00" in value:
        return outcome.failed(
            "fill_text_and_verify",
            f"text must contain 1 to {_MAX_INPUT_CHARS} characters and no NUL bytes",
            field=label,
        )
    if _looks_like_secret_payload(value):
        return outcome.failed(
            "fill_text_and_verify",
            "the text resembles a credential, key or opaque access token; it was not entered",
            field=label,
        )

    completed, result, error = _run_uia(
        lambda cancelled: _fill_text_on_window(
            expected_window_handle, expected_process_id,
            label, value, cancelled,
        ),
        _INVOKE_TIMEOUT_SECONDS,
        exclusive_lock=_INVOKE_LOCK,
        pass_cancel_event=True,
    )
    if not completed:
        return outcome.unverified(
            "fill_text_and_verify",
            "UI Automation did not return within 4 seconds; the field may have changed. Do not retry blindly.",
            field=label, timed_out=True,
        )
    if error is not None:
        return outcome.unverified(
            "fill_text_and_verify",
            f"UI Automation raised {type(error).__name__}; whether the field changed is unknown. Do not retry blindly.",
            field=label,
        )

    status, reason = result
    if status == "filled":
        return outcome.done(
            "fill_text_and_verify", field=label, verified=True, changed=True,
        )
    if status == "already_set":
        return outcome.done(
            "fill_text_and_verify", field=label, verified=True, changed=False,
        )
    if status in {"invoke_uncertain", "verification_mismatch"}:
        return outcome.unverified(
            "fill_text_and_verify", reason, field=label,
        )
    if status == "ambiguous_field":
        return outcome.ask(
            "fill_text_and_verify", reason, field=label,
        )
    return outcome.failed(
        "fill_text_and_verify", reason or status, field=label,
    )


def _wait_for_expected_result(expected: str, snapshot_reader,
                              attempts: int = _VERIFY_ATTEMPTS,
                              interval: float = _VERIFY_INTERVAL,
                              sleep=time.sleep,
                              timeout_seconds: float = _VERIFY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    for attempt in range(max(1, int(attempts))):
        if attempt and time.monotonic() >= deadline:
            break
        try:
            snapshot = snapshot_reader()
            records = snapshot.get("records", []) if isinstance(snapshot, dict) else snapshot
            if _has_exact_expected_text(records, expected):
                return True
        except Exception:
            # UIA can transiently fail while a window is changing. Keep polling
            # only within the shared verification deadline; never infer success.
            pass
        remaining = deadline - time.monotonic()
        if attempt + 1 < attempts and remaining > 0:
            sleep(min(max(0.0, interval), remaining))
    return False


def _click_and_verify(button_label: str, expected_result: str,
                      expected_window_handle: int, expected_process_id: int,
                      original_signature: dict) -> str:
    label = " ".join(str(button_label or "").split())
    expected = " ".join(str(expected_result or "").split())
    if not label or len(label) > 120:
        return outcome.failed("click_and_verify", "button label is missing or too long")
    if not expected or len(expected) > 160:
        return outcome.failed(
            "click_and_verify",
            "an exact accessible result is required (maximum 160 characters)",
            button=label,
        )

    try:
        before = _snapshot(expected_window_handle, expected_process_id)
    except Exception as exc:
        return outcome.failed(
            "click_and_verify",
            f"the original window is unavailable ({type(exc).__name__}); nothing was clicked",
            button=label,
        )

    if _has_exact_expected_text(before["records"], expected):
        return outcome.failed(
            "click_and_verify",
            "the exact expected text is already visible; no click was made",
            button=label, expected=expected,
        )

    matches = _matching_controls(before["clickables"], label)
    if not matches:
        return outcome.failed(
            "click_and_verify",
            "no visible, enabled button with that exact accessible label exists in the original window",
            button=label,
        )
    if len(matches) != 1:
        return outcome.ask(
            "click_and_verify",
            "more than one visible control has that exact label; nothing was clicked",
            button=label, matches=len(matches),
        )
    if not _same_control_identity(matches[0], original_signature):
        return outcome.failed(
            "click_and_verify",
            "the control changed after it was identified; nothing was clicked",
            button=label,
        )

    completed, invocation, error = _run_uia(
        lambda cancelled: _invoke_on_window(
            expected_window_handle, expected_process_id, label,
            original_signature, cancelled,
        ),
        _INVOKE_TIMEOUT_SECONDS,
        exclusive_lock=_INVOKE_LOCK,
        pass_cancel_event=True,
    )
    if not completed:
        return outcome.unverified(
            "click_and_verify",
            "UI Automation did not return within 4 seconds; the invocation may still be running. Do not retry it.",
            button=label, expected=expected, timed_out=True,
        )
    if error is not None:
        return outcome.unverified(
            "click_and_verify",
            f"UI Automation raised {type(error).__name__}; whether the application acted is unknown. Do not retry automatically.",
            button=label, expected=expected,
        )

    status, reason = invocation
    if status in {"busy", "cancelled"}:
        return outcome.failed(
            "click_and_verify",
            reason + "; this click was not started",
            button=label,
        )
    if status == "unsupported_pattern":
        return outcome.failed(
            "click_and_verify", reason + "; nothing was clicked",
            button=label,
        )
    if status != "invoked":
        return outcome.failed(
            "click_and_verify", reason or "the control was not invoked",
            button=label, target_status=status,
        )

    verified = _wait_for_expected_result(
        expected,
        lambda: _snapshot(expected_window_handle, expected_process_id),
    )
    if verified:
        return outcome.done(
            "click_and_verify", button=label, expected=expected, verified=True,
        )
    return outcome.unverified(
        "click_and_verify",
        "the control was invoked, but the exact expected accessible text did not appear in the original window",
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

    # Identify the exact target before asking for confirmation. No UIA object
    # crosses the worker thread boundary; only plain metadata is retained.
    try:
        snapshot = _snapshot()
    except Exception as exc:
        return outcome.failed("click_and_verify", str(exc), button=label)

    if _has_exact_expected_text(snapshot["records"], expected):
        return outcome.failed(
            "click_and_verify",
            "the exact expected text is already visible; no click was made",
            button=label, expected=expected,
        )

    matches = _matching_controls(snapshot["clickables"], label)
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

    handle = snapshot["handle"]
    process_id = snapshot["process_id"]
    signature = {
        "role": matches[0]["role"],
        "automation_id": matches[0].get("automation_id", ""),
        "runtime_id": matches[0].get("runtime_id", ()),
        "rect": matches[0].get("rect"),
    }
    context_text = " ".join(
        _safe_name(item.get("name")) for item in snapshot["records"]
    )
    if _requires_confirmation(label, expected, context_text):
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
                f"MARK will invoke the '{label}' control in the same window "
                f"identified before this prompt, then look for the exact text: "
                f"{expected}. Confirm only if this is the intended action."
            ),
            run=lambda: _click_and_verify(
                label, expected, handle, process_id, signature,
            ),
        )

    return _click_and_verify(label, expected, handle, process_id, signature)


TOOL = {
    "name": "click_and_verify",
    "tier": "core",
    "brief": "Click one exact accessible control locally and verify the exact expected result.",
    "description": (
        "Use only when the user explicitly asks you to click a visible button, "
        "link or menu item AND you know the exact accessible label and exact "
        "accessible text that should appear afterward. Provide both "
        "button_label and expected_result; do not invent the expected result. "
        "This uses local Windows UI Automation, not screenshots, coordinates, "
        "clipboard access or a vision/model call. It clicks only one exact, "
        "unique, visible and enabled accessible control in the original window. "
        "The original window and control identity are rechecked after any HUD "
        "confirmation, even if MARK's confirmation window became foreground. "
        "It reports success only when the exact expected accessible element "
        "name was absent before the click and appears afterward; partial text "
        "matches such as 'Saved' inside 'Not saved' do not count. Missing, "
        "ambiguous or unobservable outcomes are never reported as success. "
        "Potentially high-impact actions inferred from the button, expected "
        "result or visible accessible dialog context require the human HUD "
        "confirmation gate. A UI Automation call is bounded by a timeout and "
        "is never automatically retried. No UIA element is shared between COM "
        "threads. Only the verification status is returned to the active conversation; "
        "the rest of the accessibility tree remains local."
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
                "description": "Exact accessible name of a distinctive UI element that should appear after clicking.",
            },
        },
        "required": ["button_label", "expected_result"],
    },
    "handler": click_and_verify,
}
