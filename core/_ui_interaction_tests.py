"""Offline tests for exact, local UI Automation click-and-verify logic."""
import unittest

from actions.ui_interaction import (
    _matching_controls,
    _normalise,
    _requires_confirmation,
    _wait_for_expected_result,
)


class UIInteractionTests(unittest.TestCase):
    def test_normalises_whitespace_and_case(self):
        self.assertEqual(_normalise("  Save   Changes "), "save changes")

    def test_requires_confirmation_for_high_impact_labels(self):
        for label in ("Delete account", "Send message", "Usuń plik", "Potwierdź"):
            with self.subTest(label=label):
                self.assertTrue(_requires_confirmation(label))

    def test_high_impact_postconditions_also_require_confirmation(self):
        for result in ("Payment completed", "Account deleted", "Message sent"):
            with self.subTest(result=result):
                self.assertTrue(_requires_confirmation(result))

    def test_ordinary_navigation_does_not_require_confirmation(self):
        self.assertFalse(_requires_confirmation("Settings"))

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

    def test_verification_waits_for_expected_accessible_text(self):
        snapshots = iter(("Loading", "Working", "Saved successfully"))
        self.assertTrue(_wait_for_expected_result(
            "Saved successfully", lambda: next(snapshots),
            attempts=3, interval=0, sleep=lambda _seconds: None,
        ))

    def test_verification_fails_when_expected_text_never_appears(self):
        self.assertFalse(_wait_for_expected_result(
            "Saved", lambda: "Still editing",
            attempts=3, interval=0, sleep=lambda _seconds: None,
        ))

    def test_verification_handles_snapshot_errors_without_claiming_success(self):
        def broken():
            raise RuntimeError("UIA unavailable")
        self.assertFalse(_wait_for_expected_result(
            "Saved", broken, attempts=2, interval=0,
            sleep=lambda _seconds: None,
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
