"""Offline tests for safe Windows picker diagnostics."""
import unittest
from actions import ui_picker_diagnostics as diag
from core.action_loader import _validate


class PickerDiagnosticsTests(unittest.TestCase):
    def test_action_is_discoverable(self):
        record = _validate(diag, "ui_picker_diagnostics.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "inspect_picker_controls")

    def test_formats_safe_roles_and_pattern_names(self):
        result = diag.format_picker_diagnostics([
            {"role": "Spinner", "name": "Start time", "automation_id": "timeStart",
             "patterns": ["range_value", "value"]},
            {"role": "Calendar", "name": "Choose date", "patterns": ["grid"]},
        ])
        self.assertIn("Spinner: Start time", result)
        self.assertIn("range_value, value", result)
        self.assertIn("Calendar: Choose date", result)

    def test_never_displays_sensitive_or_edit_values(self):
        result = diag.format_picker_diagnostics([
            {"role": "Edit", "name": "Password", "patterns": ["value"]},
            {"role": "Edit", "name": "Verification code", "patterns": ["value"]},
        ])
        self.assertIn("No safe visible", result)
        self.assertNotIn("Password", result)

    def test_filters_unknown_roles_and_patterns(self):
        result = diag.format_picker_diagnostics([
            {"role": "Document", "name": "Document", "patterns": ["value"]},
            {"role": "Spinner", "name": "Time", "patterns": ["invoke", "unknown"]},
        ])
        self.assertIn("Spinner: Time", result)
        self.assertIn("invoke", result)
        self.assertNotIn("unknown", result)
        self.assertNotIn("Document", result)

    def test_duplicate_controls_are_deduplicated(self):
        record = {"role": "Calendar", "name": "Date", "automation_id": "cal1"}
        result = diag.format_picker_diagnostics([record, record])
        self.assertEqual(result.count("Calendar: Date"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
