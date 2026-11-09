"""Offline tests for the standalone text-entry action."""
import unittest
from unittest.mock import patch

from actions import text_entry
from core.action_loader import _validate


class StandaloneTextEntryTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(text_entry, "text_entry.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "fill_text_field")
        self.assertEqual(set(record.parameters["required"]), {"field_label", "text"})

    def test_invalid_and_sensitive_inputs_are_rejected_before_inspecting_ui(self):
        invalid = [
            {},
            {"field_label": "Search"},
            {"text": "hello"},
            {"field_label": "Password", "text": "not entered"},
            {"field_label": "Search", "text": "A" * 48},
            {"field_label": "Search", "text": "bad\x00text"},
            {"field_label": "Search", "text": "x" * 4001},
        ]
        with patch.object(text_entry.ui, "_snapshot") as snapshot:
            for params in invalid:
                with self.subTest(params=str(params)[:80]):
                    result = text_entry.fill_text_field(params)
                    self.assertTrue(result.startswith("status=failed "), result)
            snapshot.assert_not_called()

    def test_valid_request_pins_to_the_active_window_and_shared_verifier(self):
        snapshot = {"handle": 101, "process_id": 4321}
        with patch.object(text_entry.ui, "_snapshot", return_value=snapshot), \
             patch.object(
                 text_entry.ui, "_fill_text_and_verify",
                 return_value="status=done action=fill_text_and_verify verified=true",
             ) as fill:
            result = text_entry.fill_text_field({
                "field_label": "Project title",
                "text": "Quarterly plan",
            })
        self.assertTrue(result.startswith("status=done "), result)
        fill.assert_called_once_with("Project title", "Quarterly plan", 101, 4321)

    def test_entered_text_is_not_echoed_in_failure_or_success(self):
        value = "a private project draft"
        snapshot = {"handle": 101, "process_id": 4321}
        with patch.object(text_entry.ui, "_snapshot", return_value=snapshot), \
             patch.object(
                 text_entry.ui, "_fill_text_and_verify",
                 return_value="status=unverified action=fill_text_and_verify reason=readback_mismatch",
             ):
            result = text_entry.fill_text_field({
                "field_label": "Project title", "text": value,
            })
        self.assertTrue(result.startswith("status=unverified "), result)
        self.assertNotIn(value, result)

    def test_snapshot_failure_is_reported_without_attempting_to_fill(self):
        with patch.object(
            text_entry.ui, "_snapshot",
            side_effect=TimeoutError("timeout"),
        ), patch.object(text_entry.ui, "_fill_text_and_verify") as fill:
            result = text_entry.fill_text_field({
                "field_label": "Project title", "text": "Quarterly plan",
            })
        self.assertTrue(result.startswith("status=failed "), result)
        fill.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
