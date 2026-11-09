"""Offline tests for safe, exact local UI Automation interactions."""
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from actions import ui_interaction
from core.action_loader import _validate

from actions.ui_interaction import (
    _fill_text_and_verify,
    _has_exact_expected_text,
    _is_sensitive_input_label,
    _looks_like_secret_payload,
    _matching_controls,
    _normalise,
    _requires_confirmation,
    _run_bounded,
    _run_uia,
    _same_control_identity,
    _set_and_verify_value_pattern,
    _wait_for_expected_result,
    _valid_window_process_id,
)


class UIInteractionTests(unittest.TestCase):
    class FakeValuePattern:
        def __init__(self, value="", readonly=False, fail_set=False, readback=None):
            self.CurrentValue = value
            self.CurrentIsReadOnly = readonly
            self.fail_set = fail_set
            self.readback = readback

        def SetValue(self, value):
            if self.fail_set:
                raise RuntimeError("provider error")
            self.CurrentValue = self.readback if self.readback is not None else value

    def test_secret_field_labels_are_rejected_but_ordinary_fields_are_allowed(self):
        for label in (
            "Password", "Security code", "OTP", "Account PIN",
            "Credit card number", "CVV", "Bank account IBAN",
            "API key", "Access token", "Recovery code", "Authentication code",
        ):
            with self.subTest(label=label):
                self.assertTrue(_is_sensitive_input_label(label))
        self.assertFalse(_is_sensitive_input_label("Project title"))
        self.assertFalse(_is_sensitive_input_label("Email address"))

    def test_opaque_secret_payloads_are_rejected_without_blocking_normal_text(self):
        self.assertTrue(_looks_like_secret_payload("sk-" + "A" * 48))
        self.assertTrue(_looks_like_secret_payload("A" * 48))
        self.assertTrue(_looks_like_secret_payload(
            "-----BEGIN PRIVATE KEY-----\\nsecret\\n-----END PRIVATE KEY-----"
        ))
        self.assertFalse(_looks_like_secret_payload("Quarterly project plan"))
        self.assertFalse(_looks_like_secret_payload("Please contact me tomorrow"))

    def test_value_pattern_sets_and_verifies_text_locally(self):
        pattern = self.FakeValuePattern("old value")
        status, reason = _set_and_verify_value_pattern(
            pattern, "new value", threading.Event(),
        )
        self.assertEqual(status, "filled")
        self.assertEqual(reason, "")
        self.assertEqual(pattern.CurrentValue, "new value")

    def test_value_pattern_reports_already_set_without_mutation(self):
        pattern = self.FakeValuePattern("already here")
        status, _ = _set_and_verify_value_pattern(
            pattern, "already here", threading.Event(),
        )
        self.assertEqual(status, "already_set")
        self.assertEqual(pattern.CurrentValue, "already here")

    def test_value_pattern_refuses_read_only_fields(self):
        pattern = self.FakeValuePattern("old", readonly=True)
        status, _ = _set_and_verify_value_pattern(
            pattern, "new", threading.Event(),
        )
        self.assertEqual(status, "readonly")
        self.assertEqual(pattern.CurrentValue, "old")

    def test_value_pattern_reports_unknown_after_provider_error(self):
        pattern = self.FakeValuePattern("old", fail_set=True)
        status, _ = _set_and_verify_value_pattern(
            pattern, "new", threading.Event(),
        )
        self.assertEqual(status, "invoke_uncertain")
        self.assertEqual(pattern.CurrentValue, "old")

    def test_value_pattern_does_not_claim_success_for_mismatched_readback(self):
        pattern = self.FakeValuePattern("old", readback="different")
        status, _ = _set_and_verify_value_pattern(
            pattern, "new", threading.Event(),
        )
        self.assertEqual(status, "verification_mismatch")

    def test_value_pattern_checks_timeout_before_setting(self):
        cancelled = threading.Event()
        cancelled.set()
        pattern = self.FakeValuePattern("old")
        status, _ = _set_and_verify_value_pattern(pattern, "new", cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.CurrentValue, "old")

    def test_fill_text_validation_rejects_secrets_before_starting_uia(self):
        with patch.object(ui_interaction, "_run_uia") as run:
            sensitive = _fill_text_and_verify("Password", "not entered", 100, 1234)
            opaque = _fill_text_and_verify("Search", "A" * 48, 100, 1234)
            empty = _fill_text_and_verify("Project title", "", 100, 1234)
        self.assertIn("status=failed", sensitive)
        self.assertIn("status=failed", opaque)
        self.assertIn("status=failed", empty)
        run.assert_not_called()

    def test_action_is_discoverable_by_the_real_action_loader(self):
        record = _validate(ui_interaction, "ui_interaction.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "click_and_verify")
        self.assertEqual(
            set(record.parameters["required"]),
            {"button_label", "expected_result"},
        )

    def test_handler_rejects_missing_operands_without_touching_desktop(self):
        result = ui_interaction.click_and_verify({})
        self.assertIn("status=failed", result)
        self.assertIn("action=click_and_verify", result)

    def test_high_impact_click_is_parked_behind_the_hud_gate(self):
        from core import confirm
        snapshot = {
            "handle": 100, "process_id": 1234,
            "records": [{"role": "Button", "name": "Delete"}],
            "clickables": [{
                "role": "Button", "name": "Delete",
                "automation_id": "delete", "runtime_id": (1, 2, 3),
                "rect": (10, 10, 100, 40),
            }],
        }
        with (
            patch.object(ui_interaction, "_snapshot", return_value=snapshot),
            patch.object(confirm, "pending_title", return_value=""),
            patch.object(confirm, "request", return_value="[CONFIRMATION_PENDING]") as request,
        ):
            result = ui_interaction.click_and_verify({
                "button_label": "Delete", "expected_result": "Deleted",
            })
        self.assertEqual(result, "[CONFIRMATION_PENDING]")
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["key"], "click_and_verify")
        self.assertTrue(callable(request.call_args.kwargs["run"]))

    def test_existing_hud_confirmation_blocks_a_second_critical_click(self):
        from core import confirm
        snapshot = {
            "handle": 100, "process_id": 1234,
            "records": [{"role": "Button", "name": "Delete"}],
            "clickables": [{
                "role": "Button", "name": "Delete",
                "automation_id": "delete", "runtime_id": (1, 2, 3),
                "rect": (10, 10, 100, 40),
            }],
        }
        with (
            patch.object(ui_interaction, "_snapshot", return_value=snapshot),
            patch.object(confirm, "pending_title", return_value="Another action"),
            patch.object(confirm, "request") as request,
        ):
            result = ui_interaction.click_and_verify({
                "button_label": "Delete", "expected_result": "Deleted",
            })
        self.assertIn("status=ask", result)
        request.assert_not_called()

    def test_normalises_whitespace_and_case(self):
        self.assertEqual(_normalise("  Save   Changes "), "save changes")

    def test_high_impact_button_and_polish_labels_require_confirmation(self):
        for label in (
            "Delete account", "Send message", "Usuń plik", "Potwierdź",
            "Czy na pewno chcesz usunąć konto?",
            "Płatność zakończona", "Wysłać wiadomość",
        ):
            with self.subTest(label=label):
                self.assertTrue(_requires_confirmation(label))

    def test_high_impact_expected_result_requires_confirmation(self):
        for result in ("Payment completed", "Account deleted", "Message sent"):
            with self.subTest(result=result):
                self.assertTrue(_requires_confirmation(result))

    def test_high_impact_visible_context_requires_confirmation(self):
        self.assertTrue(_requires_confirmation(
            "Continue", "Dashboard",
            "Are you sure you want to permanently delete this account?",
        ))

    def test_ordinary_navigation_does_not_require_confirmation(self):
        self.assertFalse(_requires_confirmation("Settings", "Account overview"))

    def test_matches_only_exact_invokable_label(self):
        controls = [
            {"role": "Button", "name": "Save"},
            {"role": "Button", "name": "Save As"},
            {"role": "Text", "name": "Save"},
            {"role": "Hyperlink", "name": "Help"},
        ]
        found = _matching_controls(controls, " save ")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Save")

    def test_duplicate_exact_controls_are_not_silently_ranked(self):
        controls = [
            {"role": "Button", "name": "Continue"},
            {"role": "MenuItem", "name": "Continue"},
        ]
        self.assertEqual(len(_matching_controls(controls, "Continue")), 2)

    def test_empty_target_never_matches(self):
        self.assertEqual(
            _matching_controls([{"role": "Button", "name": "OK"}], ""),
            [],
        )

    def test_expected_result_requires_exact_accessible_element_name(self):
        records = [
            {"role": "Text", "name": "Saved successfully"},
            {"role": "Text", "name": "Not saved"},
            {"role": "Text", "name": "Unsaved"},
        ]
        self.assertTrue(_has_exact_expected_text(records, "  SAVED successfully "))
        self.assertFalse(_has_exact_expected_text(records, "Saved"))
        self.assertFalse(_has_exact_expected_text(records, "Not"))
        self.assertFalse(_has_exact_expected_text(records, "Unsaved data"))

    def test_expected_result_ignores_password_and_offscreen_labels(self):
        records = [
            {"role": "Text", "name": "Payment completed", "password": True},
            {"role": "Text", "name": "Payment completed", "offscreen": True},
        ]
        self.assertFalse(_has_exact_expected_text(records, "Payment completed"))

    def test_original_process_identity_must_match(self):
        self.assertTrue(_valid_window_process_id(1234, 1234))
        self.assertTrue(_valid_window_process_id("1234", None))
        self.assertFalse(_valid_window_process_id(4321, 1234))
        self.assertFalse(_valid_window_process_id(0, None))
        self.assertFalse(_valid_window_process_id(None, 1234))

    def test_control_identity_prefers_automation_id(self):
        original = {
            "role": "Button", "automation_id": "saveButton",
            "rect": (10, 10, 100, 40),
        }
        moved = {
            "role": "Button", "automation_id": "saveButton",
            "rect": (200, 200, 300, 240),
        }
        changed = {
            "role": "Button", "automation_id": "deleteButton",
            "rect": (10, 10, 100, 40),
        }
        self.assertTrue(_same_control_identity(moved, original))
        self.assertFalse(_same_control_identity(changed, original))

    def test_control_identity_prefers_runtime_id_before_geometry(self):
        original = {
            "role": "Button", "automation_id": "", "runtime_id": (1, 2, 3),
            "rect": (10, 10, 100, 40),
        }
        moved_same = {
            "role": "Button", "automation_id": "", "runtime_id": (1, 2, 3),
            "rect": (200, 200, 300, 240),
        }
        replaced = {
            "role": "Button", "automation_id": "", "runtime_id": (9, 8, 7),
            "rect": (10, 10, 100, 40),
        }
        self.assertTrue(_same_control_identity(moved_same, original))
        self.assertFalse(_same_control_identity(replaced, original))

    def test_control_identity_uses_bounded_geometry_when_id_is_missing(self):
        original = {"role": "Button", "automation_id": "", "rect": (10, 10, 100, 40)}
        near = {"role": "Button", "automation_id": "", "rect": (15, 9, 104, 44)}
        far = {"role": "Button", "automation_id": "", "rect": (50, 50, 140, 80)}
        self.assertTrue(_same_control_identity(near, original))
        self.assertFalse(_same_control_identity(far, original))

    def test_verification_waits_for_exact_accessible_element(self):
        snapshots = iter((
            [{"role": "Text", "name": "Loading"}],
            [{"role": "Text", "name": "Working"}],
            [{"role": "Text", "name": "Saved successfully"}],
        ))
        self.assertTrue(_wait_for_expected_result(
            "Saved successfully", lambda: next(snapshots),
            attempts=3, interval=0, sleep=lambda _seconds: None,
        ))

    def test_verification_rejects_partial_and_negative_text(self):
        snapshots = iter((
            [{"role": "Text", "name": "Not saved"}],
            [{"role": "Text", "name": "Unsaved"}],
            [{"role": "Text", "name": "Saved soon"}],
        ))
        self.assertFalse(_wait_for_expected_result(
            "Saved", lambda: next(snapshots),
            attempts=3, interval=0, sleep=lambda _seconds: None,
        ))

    def test_verification_stops_at_shared_deadline(self):
        calls = []
        sleeps = []
        def read():
            calls.append(1)
            return [{"role": "Text", "name": "Still working"}]
        def fake_sleep(seconds):
            sleeps.append(seconds)
        self.assertFalse(_wait_for_expected_result(
            "Saved", read, attempts=20, interval=1,
            sleep=fake_sleep, timeout_seconds=0,
        ))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_verification_fails_when_expected_text_never_appears(self):
        self.assertFalse(_wait_for_expected_result(
            "Saved", lambda: [{"role": "Text", "name": "Still editing"}],
            attempts=3, interval=0, sleep=lambda _seconds: None,
        ))

    def test_verification_handles_snapshot_errors_without_claiming_success(self):
        def broken():
            raise RuntimeError("UIA unavailable")
        self.assertFalse(_wait_for_expected_result(
            "Saved", broken, attempts=2, interval=0,
            sleep=lambda _seconds: None,
        ))

    def test_bounded_call_returns_successful_result(self):
        completed, value, error = _run_bounded(lambda: "ok", 1)
        self.assertTrue(completed)
        self.assertEqual(value, "ok")
        self.assertIsNone(error)

    def test_bounded_call_returns_worker_exception(self):
        def broken():
            raise RuntimeError("expected")
        completed, value, error = _run_bounded(broken, 1)
        self.assertTrue(completed)
        self.assertIsNone(value)
        self.assertIsInstance(error, RuntimeError)

    def test_uia_worker_initializes_and_uninitializes_com_when_module_is_loaded(self):
        calls = []
        fake_comtypes = types.ModuleType("comtypes")
        fake_comtypes.CoInitialize = lambda: calls.append("init")
        fake_comtypes.CoUninitialize = lambda: calls.append("uninit")
        fake_pywinauto = types.ModuleType("pywinauto")

        with patch.dict(sys.modules, {
            "pywinauto": fake_pywinauto,
            "comtypes": fake_comtypes,
        }):
            completed, value, error = _run_uia(lambda: "ui", 1)

        self.assertTrue(completed)
        self.assertEqual(value, "ui")
        self.assertIsNone(error)
        self.assertEqual(calls, ["init", "uninit"])

    def test_exclusive_uia_lock_refuses_a_second_invoke(self):
        lock = threading.Lock()
        self.assertTrue(lock.acquire(blocking=False))
        called = []
        try:
            completed, value, error = _run_uia(
                lambda: called.append("invoked"), 1, exclusive_lock=lock,
            )
        finally:
            lock.release()
        self.assertTrue(completed)
        self.assertEqual(value[0], "busy")
        self.assertEqual(called, [])

    def test_timed_out_call_sets_cancellation_flag_and_is_not_retried(self):
        started = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        calls = []

        def slow():
            calls.append("called")
            started.set()
            release.wait(1)
            return "late"

        completed, value, error = _run_bounded(
            slow, 0.03, cancel_event=cancelled,
        )
        self.assertFalse(completed)
        self.assertIsNone(value)
        self.assertIsNone(error)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(started.wait(0.5))
        self.assertEqual(calls, ["called"])
        release.set()
        # The timed-out operation may finish, but is never invoked a second time.
        time.sleep(0.02)
        self.assertEqual(calls, ["called"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
