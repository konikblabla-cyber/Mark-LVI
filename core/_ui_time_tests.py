"""Offline tests for strict, verified time-field entry."""
import unittest
from unittest.mock import patch

from actions import ui_time
from core.action_loader import _validate


class TimeFieldTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_time, "ui_time.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "set_time_field")
        self.assertEqual(set(record.parameters["required"]), {"field_label", "time"})

    def test_valid_midnight_and_end_of_day_are_accepted(self):
        for value in ("00:00", "09:30", "18:05", "23:59"):
            with self.subTest(value=value), patch.object(ui_time.ui, "_snapshot", return_value={"handle": 10, "process_id": 20}), patch.object(ui_time.ui, "_fill_text_and_verify", return_value="status=done verified=true") as fill:
                result = ui_time.set_time_field({"field_label": "Start time", "time": value})
                self.assertIn("status=done", result)
                fill.assert_called_once_with("Start time", value, 10, 20)

    def test_invalid_or_noncanonical_times_are_rejected_before_desktop_access(self):
        for value in ("24:00", "12:60", "9:30", "09:3", "12 PM", "2026-11-09T09:30", "", None):
            with self.subTest(value=value), patch.object(ui_time.ui, "_snapshot") as snapshot:
                result = ui_time.set_time_field({"field_label": "Start time", "time": value})
                self.assertIn("status=failed", result)
                snapshot.assert_not_called()

    def test_sensitive_label_is_rejected_before_desktop_access(self):
        with patch.object(ui_time.ui, "_snapshot") as snapshot:
            result = ui_time.set_time_field({"field_label": "Password", "time": "09:30"})
        self.assertIn("status=failed", result)
        snapshot.assert_not_called()

    def test_missing_or_long_label_is_rejected(self):
        with patch.object(ui_time.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_time.set_time_field({"time": "09:30"}))
            self.assertIn("status=failed", ui_time.set_time_field({"field_label": "x" * 121, "time": "09:30"}))
        snapshot.assert_not_called()

    def test_missing_process_identity_fails_closed(self):
        with patch.object(ui_time.ui, "_snapshot", return_value={"handle": 10, "process_id": 0}), patch.object(ui_time.ui, "_fill_text_and_verify") as fill:
            result = ui_time.set_time_field({"field_label": "Start time", "time": "09:30"})
        self.assertIn("status=failed", result)
        fill.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
