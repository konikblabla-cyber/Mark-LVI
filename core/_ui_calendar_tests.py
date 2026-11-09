"""Offline tests for exact, verified Windows calendar date selection."""
import threading
import unittest
from unittest.mock import patch

from actions import ui_calendar
from core.action_loader import _validate


class Pattern:
    def __init__(self, selected=False, error=False):
        self.CurrentIsSelected = selected
        self.error = error
        self.calls = 0

    def Select(self):
        self.calls += 1
        if self.error:
            raise RuntimeError("provider error")
        self.CurrentIsSelected = True


class CalendarTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_calendar, "ui_calendar.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "select_calendar_date")
        self.assertEqual(set(record.parameters["required"]), {"calendar_label", "date_label"})

    def test_date_selection_is_verified(self):
        pattern = Pattern()
        self.assertEqual(ui_calendar._select_pattern(pattern, threading.Event()), ("selected", ""))
        self.assertEqual(pattern.calls, 1)

    def test_already_selected_date_is_not_selected_twice(self):
        pattern = Pattern(selected=True)
        self.assertEqual(ui_calendar._select_pattern(pattern, threading.Event()), ("already_selected", ""))
        self.assertEqual(pattern.calls, 0)

    def test_provider_error_is_uncertain(self):
        pattern = Pattern(error=True)
        self.assertEqual(ui_calendar._select_pattern(pattern, threading.Event())[0], "uncertain")
        self.assertEqual(pattern.calls, 1)

    def test_expired_operation_does_not_select(self):
        cancelled = threading.Event()
        cancelled.set()
        pattern = Pattern()
        self.assertEqual(ui_calendar._select_pattern(pattern, cancelled)[0], "cancelled")
        self.assertEqual(pattern.calls, 0)

    def test_sensitive_labels_are_rejected_before_desktop_access(self):
        with patch.object(ui_calendar.ui, "_run_uia") as run:
            result = ui_calendar.select_calendar_date({"calendar_label": "Password", "date_label": "Monday"})
        self.assertIn("status=failed", result)
        run.assert_not_called()

    def test_missing_labels_are_rejected(self):
        self.assertIn("status=failed", ui_calendar.select_calendar_date({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
