"""Offline tests for exact, verified Windows checkbox state changes."""
import sys
import threading
import types
import unittest
from unittest.mock import patch

from actions import ui_checkbox
from core.action_loader import _validate


class FakeTogglePattern:
    def __init__(self, state=0, fail=False, stuck=False):
        self.CurrentToggleState = state
        self.calls = 0
        self.fail = fail
        self.stuck = stuck

    def Toggle(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider error")
        if not self.stuck:
            self.CurrentToggleState = 1 if self.CurrentToggleState == 0 else 0


class FakeRect:
    def width(self):
        return 100

    def height(self):
        return 24


class FakeControl:
    def __init__(self, name="Remember this device", role="CheckBox",
                 pattern=None, enabled=True):
        self.element_info = types.SimpleNamespace(
            control_type=role, name=name, is_password=False, is_offscreen=False,
        )
        self.iface_toggle = pattern or FakeTogglePattern()
        self._enabled = enabled

    def rectangle(self):
        return FakeRect()

    def is_enabled(self):
        return self._enabled


class FakeWindow:
    def __init__(self, controls=None, handle=111, process_id=222):
        self._controls = list(controls or [])
        self.handle = handle
        self.element_info = types.SimpleNamespace(process_id=process_id)

    def is_visible(self):
        return True

    def descendants(self):
        return iter(self._controls)


def patch_desktop(window):
    module = types.ModuleType("pywinauto")
    module.Desktop = lambda backend="uia": types.SimpleNamespace(
        window=lambda handle: types.SimpleNamespace(wrapper_object=lambda: window)
    )
    return patch.dict(sys.modules, {"pywinauto": module})


class CheckboxTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_checkbox, "ui_checkbox.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "set_checkbox_state")
        self.assertEqual(set(record.parameters["required"]), {"checkbox_label", "checked"})

    def test_off_checkbox_is_checked_once_and_verified(self):
        pattern = FakeTogglePattern(state=0)
        status, _ = ui_checkbox._set_toggle_state(pattern, True, threading.Event())
        self.assertEqual(status, "changed")
        self.assertEqual(pattern.CurrentToggleState, 1)
        self.assertEqual(pattern.calls, 1)

    def test_on_checkbox_is_unchecked_once_and_verified(self):
        pattern = FakeTogglePattern(state=1)
        status, _ = ui_checkbox._set_toggle_state(pattern, False, threading.Event())
        self.assertEqual(status, "changed")
        self.assertEqual(pattern.CurrentToggleState, 0)
        self.assertEqual(pattern.calls, 1)

    def test_already_correct_state_is_not_toggled(self):
        pattern = FakeTogglePattern(state=1)
        status, _ = ui_checkbox._set_toggle_state(pattern, True, threading.Event())
        self.assertEqual(status, "already_set")
        self.assertEqual(pattern.calls, 0)

    def test_indeterminate_state_fails_closed(self):
        pattern = FakeTogglePattern(state=2)
        status, _ = ui_checkbox._set_toggle_state(pattern, True, threading.Event())
        self.assertEqual(status, "unsupported_state")
        self.assertEqual(pattern.calls, 0)

    def test_provider_error_is_uncertain_and_not_retried(self):
        pattern = FakeTogglePattern(state=0, fail=True)
        status, _ = ui_checkbox._set_toggle_state(pattern, True, threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(pattern.calls, 1)

    def test_failed_postcondition_is_not_reported_as_success(self):
        pattern = FakeTogglePattern(state=0, stuck=True)
        status, _ = ui_checkbox._set_toggle_state(pattern, True, threading.Event())
        self.assertEqual(status, "unverified")
        self.assertEqual(pattern.calls, 1)

    def test_cancelled_operation_does_not_toggle(self):
        pattern = FakeTogglePattern(state=0)
        cancelled = threading.Event()
        cancelled.set()
        status, _ = ui_checkbox._set_toggle_state(pattern, True, cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.calls, 0)

    def test_worker_targets_exact_checkbox_and_ignores_other_roles(self):
        pattern = FakeTogglePattern()
        window = FakeWindow([
            FakeControl("Remember this device", role="Button"),
            FakeControl("Remember this device", role="CheckBox", pattern=pattern),
        ])
        with patch_desktop(window):
            status, _ = ui_checkbox._set_on_window(
                111, 222, "Remember this device", True, threading.Event(),
            )
        self.assertEqual(status, "changed")
        self.assertEqual(pattern.CurrentToggleState, 1)

    def test_wrong_process_is_rejected(self):
        pattern = FakeTogglePattern()
        window = FakeWindow([FakeControl(pattern=pattern)], process_id=999)
        with patch_desktop(window):
            status, _ = ui_checkbox._set_on_window(
                111, 222, "Remember this device", True, threading.Event(),
            )
        self.assertEqual(status, "changed_window")
        self.assertEqual(pattern.calls, 0)

    def test_duplicate_checkbox_labels_are_rejected(self):
        one, two = FakeTogglePattern(), FakeTogglePattern()
        window = FakeWindow([
            FakeControl(pattern=one), FakeControl(pattern=two),
        ])
        with patch_desktop(window):
            status, _ = ui_checkbox._set_on_window(
                111, 222, "Remember this device", True, threading.Event(),
            )
        self.assertEqual(status, "ambiguous_checkbox")
        self.assertEqual(one.calls, 0)
        self.assertEqual(two.calls, 0)

    def test_sensitive_checkbox_is_not_selected(self):
        pattern = FakeTogglePattern()
        window = FakeWindow([FakeControl("PIN", pattern=pattern)])
        with patch_desktop(window):
            status, _ = ui_checkbox._set_on_window(
                111, 222, "PIN", True, threading.Event(),
            )
        self.assertEqual(status, "missing_checkbox")
        self.assertEqual(pattern.calls, 0)

    def test_consent_and_privacy_labels_require_confirmation(self):
        for label in ("Agree to terms", "Allow notifications", "Share location",
                      "Zgadzam się na regulamin", "Przetwarzanie danych",
                      "Udostępnianie lokalizacji", "Powiadomienia",
                      "Accept all cookies", "Reject analytics"):
            with self.subTest(label=label):
                self.assertTrue(ui_checkbox._needs_confirmation(label))
        self.assertFalse(ui_checkbox._needs_confirmation("Remember this device"))

    def test_invalid_parameters_fail_before_desktop_access(self):
        with patch.object(ui_checkbox.ui, "_snapshot") as snapshot:
            for params in (
                {"checkbox_label": "PIN", "checked": True},
                {"checkbox_label": "Remember", "checked": "true"},
                {"checkbox_label": "x" * 121, "checked": False},
                {"checkbox_label": "Remember"},
            ):
                with self.subTest(params=params):
                    self.assertIn("status=failed", ui_checkbox.set_checkbox_state(params))
        snapshot.assert_not_called()

 
    def test_consent_checkbox_invokes_confirmation_gate(self):
        with patch.object(ui_checkbox.ui, "_snapshot",
                          return_value={"handle": 111, "process_id": 222}), \
             patch("core.confirm.pending_title", return_value="") as pending, \
             patch("core.confirm.request", return_value="confirmation-pending") as request, \
             patch.object(ui_checkbox.ui, "_run_uia") as run_uia:
            result = ui_checkbox.set_checkbox_state({
                "checkbox_label": "Accept all cookies", "checked": True,
            })
        self.assertEqual(result, "confirmation-pending")
        pending.assert_called_once()
        request.assert_called_once()
        run_uia.assert_not_called()

    def test_existing_confirmation_prevents_second_consent_action(self):
        with patch.object(ui_checkbox.ui, "_snapshot",
                          return_value={"handle": 111, "process_id": 222}), \
             patch("core.confirm.pending_title", return_value="Existing confirmation"), \
             patch("core.confirm.request") as request, \
             patch.object(ui_checkbox.ui, "_run_uia") as run_uia:
            result = ui_checkbox.set_checkbox_state({
                "checkbox_label": "Zgadzam się na regulamin", "checked": True,
            })
        self.assertIn("status=ask", result)
        request.assert_not_called()
        run_uia.assert_not_called()

    def test_handler_verifies_state_and_returns_no_field_contents(self):
        pattern = FakeTogglePattern()
        window = FakeWindow([FakeControl(pattern=pattern)])
        with patch_desktop(window), \
             patch.object(ui_checkbox.ui, "_snapshot", return_value={"handle": 111, "process_id": 222}), \
             patch.object(ui_checkbox.ui, "_run_uia",
                          side_effect=lambda callback, *args, **kwargs:
                              (True, callback(threading.Event()), None)):
            result = ui_checkbox.set_checkbox_state({
                "checkbox_label": "Remember this device", "checked": True,
            })
        self.assertIn("status=done", result)
        self.assertIn("checked=true", result)
        self.assertEqual(pattern.CurrentToggleState, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
