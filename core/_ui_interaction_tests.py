"""Offline tests for safe, exact local UI Automation interactions."""
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from actions.ui_interaction import (
    _has_exact_expected_text,
    _matching_controls,
    _normalise,
    _requires_confirmation,
    _run_bounded,
    _run_uia,
    _same_control_identity,
    _wait_for_expected_result,
)


class UIInteractionTests(unittest.TestCase):
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

        with patch.dict(sys.modules, {"comtypes": fake_comtypes}):
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
