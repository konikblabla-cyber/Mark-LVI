"""Offline tests for exact, verified dropdown selection."""
import threading
import unittest
from unittest.mock import patch
from actions import ui_dropdown
from core.action_loader import _validate

class Pattern:
    def __init__(self, selected=False, error=False):
        self.CurrentIsSelected, self.error, self.calls = selected, error, 0
    def Select(self):
        self.calls += 1
        if self.error: raise RuntimeError("provider error")
        self.CurrentIsSelected = True

class DropdownTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_dropdown, "ui_dropdown.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "select_dropdown_option")
        self.assertEqual(set(record.parameters["required"]), {"combo_label", "option_label"})
    def test_exact_option_is_selected_and_verified(self):
        p = Pattern()
        self.assertEqual(ui_dropdown._select_pattern(p, threading.Event()), ("selected", ""))
        self.assertEqual(p.calls, 1)
    def test_already_selected_option_is_not_selected_twice(self):
        p = Pattern(True)
        self.assertEqual(ui_dropdown._select_pattern(p, threading.Event()), ("already_selected", ""))
        self.assertEqual(p.calls, 0)
    def test_provider_error_is_uncertain_not_failed(self):
        p = Pattern(error=True)
        self.assertEqual(ui_dropdown._select_pattern(p, threading.Event())[0], "uncertain")
        self.assertEqual(p.calls, 1)
    def test_timeout_prevents_selection(self):
        e, p = threading.Event(), Pattern()
        e.set()
        self.assertEqual(ui_dropdown._select_pattern(p, e)[0], "cancelled")
        self.assertEqual(p.calls, 0)
    def test_sensitive_labels_are_rejected_before_desktop_access(self):
        with patch.object(ui_dropdown.ui, "_run_uia") as run:
            result = ui_dropdown.select_dropdown_option({"combo_label":"Password","option_label":"Remember me"})
        self.assertIn("status=failed", result)
        run.assert_not_called()
    def test_missing_labels_are_rejected(self):
        self.assertIn("status=failed", ui_dropdown.select_dropdown_option({}))

if __name__ == "__main__": unittest.main(verbosity=2)
