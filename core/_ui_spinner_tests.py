"""Offline tests for verified numeric Windows Spinner input."""
import threading
import unittest
from decimal import Decimal
from unittest.mock import patch

from actions import ui_spinner
from core.action_loader import _validate


class FakeRangePattern:
    def __init__(self, current=0, minimum=0, maximum=23, step=1,
                 readonly=False, fail_set=False):
        self.CurrentValue = float(current)
        self.CurrentMinimum = float(minimum)
        self.CurrentMaximum = float(maximum)
        self.CurrentSmallChange = float(step)
        self.CurrentIsReadOnly = readonly
        self.fail_set = fail_set
        self.set_calls = []

    def SetValue(self, value):
        self.set_calls.append(value)
        if self.fail_set:
            raise RuntimeError("provider error")
        self.CurrentValue = float(value)


class SpinnerTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_spinner, "ui_spinner.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "set_spinner_value")
        self.assertEqual(set(record.parameters["required"]), {"field_label", "value"})

    def test_parser_accepts_canonical_decimal_strings_only(self):
        for value, expected in (("0", Decimal("0")), ("14", Decimal("14")),
                                ("23.5", Decimal("23.5")), ("-2.5", Decimal("-2.5"))):
            with self.subTest(value=value):
                self.assertEqual(ui_spinner._parse_target(value), expected)
        for value in ("", "01", "+1", "1e3", "NaN", "Infinity", "1,5",
                      "1.1234567", "100000000001", None, 1):
            with self.subTest(value=value):
                self.assertIsNone(ui_spinner._parse_target(value))

    def test_readonly_spinner_is_not_changed(self):
        pattern = FakeRangePattern(readonly=True)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("10"), threading.Event())
        self.assertEqual(status, "readonly")
        self.assertEqual(pattern.set_calls, [])

    def test_target_outside_range_is_not_changed(self):
        pattern = FakeRangePattern(minimum=0, maximum=23)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("24"), threading.Event())
        self.assertEqual(status, "out_of_range")
        self.assertEqual(pattern.set_calls, [])

    def test_target_must_match_supported_increment(self):
        pattern = FakeRangePattern(minimum=0, maximum=24, step=2)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("3"), threading.Event())
        self.assertEqual(status, "invalid_step")
        self.assertEqual(pattern.set_calls, [])

    def test_already_selected_value_is_not_set_twice(self):
        pattern = FakeRangePattern(current=10)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("10"), threading.Event())
        self.assertEqual(status, "already_set")
        self.assertEqual(pattern.set_calls, [])

    def test_range_value_setter_is_verified_locally(self):
        pattern = FakeRangePattern(current=9)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("12"), threading.Event())
        self.assertEqual(status, "set")
        self.assertEqual(pattern.set_calls, [12.0])
        self.assertEqual(pattern.CurrentValue, 12.0)

    def test_provider_error_after_set_is_uncertain_and_not_retried(self):
        pattern = FakeRangePattern(current=9, fail_set=True)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("12"), threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(len(pattern.set_calls), 1)

    def test_cancelled_operation_does_not_set(self):
        pattern = FakeRangePattern(current=9)
        cancelled = threading.Event()
        cancelled.set()
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("12"), cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.set_calls, [])

    def test_zero_or_invalid_increment_is_rejected(self):
        pattern = FakeRangePattern(current=0, step=0)
        status, _ = ui_spinner._set_and_verify(pattern, Decimal("1"), threading.Event())
        self.assertEqual(status, "unsupported")
        self.assertEqual(pattern.set_calls, [])

    def test_sensitive_label_and_bad_value_fail_before_desktop_access(self):
        with patch.object(ui_spinner.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_spinner.set_spinner_value({
                "field_label": "PIN", "value": "2",
            }))
            self.assertIn("status=failed", ui_spinner.set_spinner_value({
                "field_label": "Hour", "value": "1e3",
            }))
        snapshot.assert_not_called()

    def test_invalid_or_missing_label_fails_before_desktop_access(self):
        with patch.object(ui_spinner.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_spinner.set_spinner_value({"value": "1"}))
            self.assertIn("status=failed", ui_spinner.set_spinner_value({
                "field_label": "x" * 121, "value": "1",
            }))
        snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
