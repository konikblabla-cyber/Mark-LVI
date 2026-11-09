"""Offline tests for exact, verified Windows TabItem selection."""
import sys
import threading
import types
import unittest
from unittest.mock import patch

from actions import ui_tab
from core.action_loader import _validate


class FakeSelectionPattern:
    def __init__(self, selected=False, fail=False, stuck=False):
        self.CurrentIsSelected = selected
        self.calls = 0
        self.fail = fail
        self.stuck = stuck

    def Select(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider error")
        if not self.stuck:
            self.CurrentIsSelected = True


class FakeRect:
    def width(self):
        return 100

    def height(self):
        return 24


class FakeControl:
    def __init__(self, name="General", role="TabItem",
                 pattern=None, enabled=True):
        self.element_info = types.SimpleNamespace(
            control_type=role, name=name, is_password=False, is_offscreen=False,
        )
        self.iface_selection_item = pattern or FakeSelectionPattern()
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


class TabTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_tab, "ui_tab.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "select_tab_item")
        self.assertEqual(set(record.parameters["required"]), {"tab_label"})

    def test_unselected_tab_is_selected_once_and_verified(self):
        pattern = FakeSelectionPattern()
        status, _ = ui_tab._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "selected")
        self.assertTrue(pattern.CurrentIsSelected)
        self.assertEqual(pattern.calls, 1)

    def test_already_selected_tab_is_not_selected_twice(self):
        pattern = FakeSelectionPattern(selected=True)
        status, _ = ui_tab._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "already_selected")
        self.assertEqual(pattern.calls, 0)

    def test_provider_error_is_uncertain_and_not_retried(self):
        pattern = FakeSelectionPattern(fail=True)
        status, _ = ui_tab._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(pattern.calls, 1)

    def test_missing_postcondition_is_not_reported_as_success(self):
        pattern = FakeSelectionPattern(stuck=True)
        status, _ = ui_tab._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "unverified")
        self.assertEqual(pattern.calls, 1)

    def test_cancelled_operation_does_not_select(self):
        pattern = FakeSelectionPattern()
        cancelled = threading.Event()
        cancelled.set()
        status, _ = ui_tab._select_pattern(pattern, cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.calls, 0)

    def test_worker_targets_exact_tab_and_ignores_other_roles(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([
            FakeControl("General", role="Button"),
            FakeControl("General", role="TabItem", pattern=pattern),
        ])
        with patch_desktop(window):
            status, _ = ui_tab._select_on_window(111, 222, "General", threading.Event())
        self.assertEqual(status, "selected")
        self.assertTrue(pattern.CurrentIsSelected)

    def test_wrong_process_is_rejected(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl(pattern=pattern)], process_id=999)
        with patch_desktop(window):
            status, _ = ui_tab._select_on_window(111, 222, "General", threading.Event())
        self.assertEqual(status, "changed_window")
        self.assertEqual(pattern.calls, 0)

    def test_duplicate_tab_labels_are_rejected(self):
        one, two = FakeSelectionPattern(), FakeSelectionPattern()
        window = FakeWindow([FakeControl(pattern=one), FakeControl(pattern=two)])
        with patch_desktop(window):
            status, _ = ui_tab._select_on_window(111, 222, "General", threading.Event())
        self.assertEqual(status, "ambiguous_tab")
        self.assertEqual(one.calls, 0)
        self.assertEqual(two.calls, 0)

    def test_sensitive_tab_label_is_not_selected(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl("PIN", pattern=pattern)])
        with patch_desktop(window):
            status, _ = ui_tab._select_on_window(111, 222, "PIN", threading.Event())
        self.assertEqual(status, "missing_tab")
        self.assertEqual(pattern.calls, 0)

    def test_invalid_or_missing_label_fails_before_desktop_access(self):
        with patch.object(ui_tab.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_tab.select_tab_item({}))
            self.assertIn("status=failed", ui_tab.select_tab_item({"tab_label": "x" * 121}))
            self.assertIn("status=failed", ui_tab.select_tab_item({"tab_label": "PIN"}))
        snapshot.assert_not_called()

    def test_handler_verifies_selected_state(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl(pattern=pattern)])
        with patch_desktop(window), \
             patch.object(ui_tab.ui, "_snapshot", return_value={"handle": 111, "process_id": 222}), \
             patch.object(ui_tab.ui, "_run_uia",
                          side_effect=lambda callback, *args, **kwargs:
                              (True, callback(threading.Event()), None)):
            result = ui_tab.select_tab_item({"tab_label": "General"})
        self.assertIn("status=done", result)
        self.assertIn("selected=true", result)
        self.assertTrue(pattern.CurrentIsSelected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
