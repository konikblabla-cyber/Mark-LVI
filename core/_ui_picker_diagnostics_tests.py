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

    def test_filters_sensitive_labels_and_camel_case_automation_ids(self):
        result = diag.format_picker_diagnostics([
            {"role": "Edit", "name": "Password", "patterns": ["value"]},
            {"role": "Edit", "name": "Verification code", "patterns": ["value"]},
            {"role": "Spinner", "name": "Time", "automation_id": "apiKeyField"},
            {"role": "Edit", "name": "PIN", "patterns": ["value"]},
            {"role": "Edit", "name": "Access token", "patterns": ["value"]},
        ])
        self.assertNotIn("Password", result)
        self.assertNotIn("Verification code", result)
        self.assertNotIn("apiKeyField", result)
        self.assertNotIn("PIN", result)
        self.assertNotIn("Access token", result)
        self.assertIn("Spinner: Time", result)
        self.assertNotIn("AutomationId=", result)

    def test_filters_unknown_roles_and_malformed_patterns(self):
        result = diag.format_picker_diagnostics([
            {"role": "Document", "name": "Document", "patterns": ["value"]},
            {"role": "Spinner", "name": "Time", "patterns": "value"},
            {"role": "Calendar", "name": "Date", "patterns": [[], "grid", "grid", None]},
        ])
        self.assertIn("Spinner: Time", result)
        self.assertIn("Calendar: Date", result)
        self.assertIn("patterns: grid", result)
        self.assertNotIn("unknown", result)
        self.assertNotIn("Document", result)

    def test_duplicate_controls_are_deduplicated(self):
        record = {"role": "Calendar", "name": "Date", "automation_id": "cal1"}
        result = diag.format_picker_diagnostics([record, record])
        self.assertEqual(result.count("Calendar: Date"), 1)

    def test_limit_is_bounded_and_marker_only_appears_when_truncated(self):
        exactly_two = [
            {"role": "Calendar", "name": "Date 1"},
            {"role": "Calendar", "name": "Date 2"},
        ]
        result = diag.format_picker_diagnostics(exactly_two, limit=2)
        self.assertNotIn("item limit reached", result)
        many = [{"role": "Calendar", "name": f"Date {i}"} for i in range(70)]
        limited = diag.format_picker_diagnostics(many, limit=10000)
        self.assertEqual(sum(line.startswith("- Calendar:") for line in limited.splitlines()), 60)
        self.assertIn("item limit reached", limited)
        clamped = diag.format_picker_diagnostics(exactly_two, limit=-5)
        self.assertEqual(sum(line.startswith("- Calendar:") for line in clamped.splitlines()), 1)
        self.assertIn("item limit reached", clamped)


    def test_filters_emails_phone_numbers_and_opaque_tokens(self):
        records = [
            {"role": "Text", "name": "owner@example.com"},
            {"role": "Text", "name": "+48 600 123 456"},
            {"role": "Text", "name": "Bearer token"},
            {"role": "Spinner", "name": "Hour", "automation_id": "a" * 40},
            {"role": "Calendar", "name": "Meeting date"},
        ]
        result = diag.format_picker_diagnostics(records)
        self.assertNotIn("owner@example.com", result)
        self.assertNotIn("+48 600 123 456", result)
        self.assertNotIn("Bearer token", result)
        self.assertNotIn("a" * 40, result)
        self.assertIn("Calendar: Meeting date", result)
        self.assertNotIn("AutomationId=", result)

    def test_output_is_bounded_by_character_limit(self):
        records = [
            {"role": "Text", "name": f"Visible control {index} " + ("x" * 140)}
            for index in range(100)
        ]
        result = diag.format_picker_diagnostics(records)
        self.assertLessEqual(len(result), diag._MAX_OUTPUT_CHARS + 32)
        self.assertTrue(
            "output truncated" in result or "item limit reached" in result,
            result[-100:],
        )

    def test_scan_limit_bounds_untrusted_iterable(self):
        class CountingIterable:
            def __init__(self):
                self.count = 0
            def __iter__(self):
                while True:
                    self.count += 1
                    yield {"role": "Document", "name": "ignored"}
        records = CountingIterable()
        result = diag.format_picker_diagnostics(records)
        self.assertIn("scan limit reached", result)
        self.assertEqual(records.count, diag._MAX_SCANNED + 1)

    def test_non_iterable_input_is_safe(self):
        self.assertIn("No safe visible", diag.format_picker_diagnostics(123))


if __name__ == "__main__":
    unittest.main(verbosity=2)
