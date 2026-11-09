"""Offline tests for verified numeric Windows Slider input."""
import sys
import threading
import types
import unittest
from decimal import Decimal
from unittest.mock import patch

from actions import ui_slider
from core.action_loader import _validate


class FakeRangePattern:
    def __init__(self, current=0, minimum=0, maximum=100,
                 readonly=False, fail_set=False, stuck=False):
        self.CurrentValue = float(current)
        self.CurrentMinimum = float(minimum)
        self.CurrentMaximum = float(maximum)
        self.CurrentIsReadOnly = readonly
        self.fail_set = fail_set
        self.stuck = stuck
        self.set_calls = []

    def SetValue(self, value):
        self.set_calls.append(value)
        if self.fail_set:
            raise RuntimeError("provider error")
        if not self.stuck:
            self.CurrentValue = float(value)


class FakeRect:
    def width(self):
        return 100

    def height(self):
        return 24


class FakeControl:
    def __init__(self, name="Volume", role="Slider", pattern=None, enabled=True):
        self.element_info = types.SimpleNamespace(
            control_type=role, name=name, is_password=False, is_offscreen=False,
        )
        self.iface_range_value = pattern or FakeRangePattern()
        self._enabled = enabled

    def rectangle(self):
        return FakeRect()

    def is_enabled(self):
        return self._enabled


class FakeWindow:
    def __init__(self, controls=None, handle=111, process_id=222):
        self._controls = list(controls or [])
        self.handle = handle
        self.element_info = types.SimpleNamespace(process_id=process_id)

    def is_visible(self):
        return True

    def descendants(self):
        return iter(self._controls)


def patch_desktop(window):
    module = types.ModuleType("pywinauto")
    module.Desktop = lambda backend="uia": types.SimpleNamespace(
        window=lambda handle: types.SimpleNamespace(wrapper_object=lambda: window)
    )
    return patch.dict(sys.modules, {"pywinauto": module})


class SliderTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_slider, "ui_slider.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "set_slider_value")
        self.assertEqual(set(record.parameters["required"]), {"field_label", "value"})

    def test_parser_accepts_canonical_decimal_strings_only(self):
        for value, expected in (("0", Decimal("0")), ("75", Decimal("75")),
                                ("0.5", Decimal("0.5")), ("-2.5", Decimal("-2.5"))):
            with self.subTest(value=value):
                self.assertEqual(ui_slider._parse_target(value), expected)
        for value in ("", "01", "+1", "1e3", "NaN", "Infinity", "1,5",
                      "1.1234567", "100000000001", None, 1):
            with self.subTest(value=value):
                self.assertIsNone(ui_slider._parse_target(value))

    def test_readonly_slider_is_not_changed(self):
        pattern = FakeRangePattern(readonly=True)
        status, _ = ui_slider._set_and_verify(pattern, Decimal("50"), threading.Event())
        self.assertEqual(status, "readonly")
        self.assertEqual(pattern.set_calls, [])

    def test_target_outside_live_range_is_rejected(self):
        pattern = FakeRangePattern(current=40, minimum=10, maximum=90)
        for value in (Decimal("9"), Decimal("91")):
            with self.subTest(value=value):
                status, _ = ui_slider._set_and_verify(pattern, value, threading.Event())
                self.assertEqual(status, "out_of_range")
        self.assertEqual(pattern.set_calls, [])

    def test_invalid_or_unreadable_range_fails_closed(self):
        pattern = FakeRangePattern(current=101, minimum=0, maximum=100)
        status, _ = ui_slider._set_and_verify(pattern, Decimal("50"), threading.Event())
        self.assertEqual(status, "unsupported")
        self.assertEqual(pattern.set_calls, [])

    def test_already_set_value_is_not_set_twice(self):
        pattern = FakeRangePattern(current=40)
        status, _ = ui_slider._set_and_verify(pattern, Decimal("40"), threading.Event())
        self.assertEqual(status, "already_set")
        self.assertEqual(pattern.set_calls, [])

    def test_slider_value_setter_is_verified_locally(self):
        pattern = FakeRangePattern(current=20)
        status, _ = ui_slider._set_and_verify(pattern, Decimal("75"), threading.Event())
        self.assertEqual(status, "set")
        self.assertEqual(pattern.set_calls, [75.0])
        self.assertEqual(pattern.CurrentValue, 75.0)

    def test_provider_error_after_set_is_uncertain_and_not_retried(self):
        pattern = FakeRangePattern(current=20, fail_set=True)
        status, _ = ui_slider._set_and_verify(pattern, Decimal("75"), threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(len(pattern.set_calls), 1)

    def test_missing_postcondition_is_unverified_without_retry(self):
        pattern = FakeRangePattern(current=20, stuck=True)
        status, _ = ui_slider._set_and_verify(pattern, Decimal("75"), threading.Event())
        self.assertEqual(status, "unverified")
        self.assertEqual(len(pattern.set_calls), 1)

    def test_cancelled_operation_does_not_set(self):
        pattern = FakeRangePattern(current=20)
        cancelled = threading.Event()
        cancelled.set()
        status, _ = ui_slider._set_and_verify(pattern, Decimal("75"), cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.set_calls, [])

    def test_worker_targets_only_one_exact_slider(self):
        pattern = FakeRangePattern(current=20)
        window = FakeWindow([
            FakeControl("Brightness", pattern=FakeRangePattern()),
            FakeControl("Volume", role="Edit", pattern=FakeRangePattern()),
            FakeControl("Volume", pattern=pattern),
        ])
        with patch_desktop(window):
            status, _ = ui_slider._set_on_window(
                111, 222, "Volume", Decimal("75"), threading.Event(),
            )
        self.assertEqual(status, "set")
        self.assertEqual(pattern.CurrentValue, 75)
        self.assertEqual(len(pattern.set_calls), 1)

    def test_wrong_process_is_rejected_before_slider_set(self):
        pattern = FakeRangePattern(current=20)
        window = FakeWindow([FakeControl(pattern=pattern)], process_id=999)
        with patch_desktop(window):
            status, _ = ui_slider._set_on_window(
                111, 222, "Volume", Decimal("75"), threading.Event(),
            )
        self.assertEqual(status, "changed_window")
        self.assertEqual(pattern.set_calls, [])

    def test_duplicate_slider_labels_are_rejected(self):
        one, two = FakeRangePattern(), FakeRangePattern()
        window = FakeWindow([
            FakeControl("Volume", pattern=one),
            FakeControl("Volume", pattern=two),
        ])
        with patch_desktop(window):
            status, _ = ui_slider._set_on_window(
                111, 222, "Volume", Decimal("75"), threading.Event(),
            )
        self.assertEqual(status, "ambiguous_slider")
        self.assertEqual(one.set_calls, [])
        self.assertEqual(two.set_calls, [])

    def test_sensitive_slider_label_is_not_selected(self):
        pattern = FakeRangePattern(current=20)
        window = FakeWindow([FakeControl("PIN", pattern=pattern)])
        with patch_desktop(window):
            status, _ = ui_slider._set_on_window(
                111, 222, "PIN", Decimal("75"), threading.Event(),
            )
        self.assertEqual(status, "missing_slider")
        self.assertEqual(pattern.set_calls, [])

    def test_sensitive_label_and_invalid_value_fail_before_desktop_access(self):
        with patch.object(ui_slider.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_slider.set_slider_value({
                "field_label": "PIN", "value": "50",
            }))
            self.assertIn("status=failed", ui_slider.set_slider_value({
                "field_label": "Volume", "value": "1e3",
            }))
        snapshot.assert_not_called()

    def test_invalid_or_missing_label_fails_before_desktop_access(self):
        with patch.object(ui_slider.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_slider.set_slider_value({"value": "50"}))
            self.assertIn("status=failed", ui_slider.set_slider_value({
                "field_label": "x" * 121, "value": "50",
            }))
        snapshot.assert_not_called()

    def test_handler_does_not_return_the_numeric_value(self):
        pattern = FakeRangePattern(current=20)
        window = FakeWindow([FakeControl("Volume", pattern=pattern)])
        with patch_desktop(window), \
             patch.object(ui_slider.ui, "_snapshot", return_value={"handle": 111, "process_id": 222}), \
             patch.object(ui_slider.ui, "_run_uia",
                          side_effect=lambda callback, *args, **kwargs:
                              (True, callback(threading.Event()), None)):
            result = ui_slider.set_slider_value({"field_label": "Volume", "value": "75"})
        self.assertIn("status=done", result)
        self.assertNotIn("75", result)
        self.assertEqual(pattern.CurrentValue, 75)


if __name__ == "__main__":
    unittest.main(verbosity=2)
