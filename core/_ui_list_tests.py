"""Offline tests for single-selection-only Windows ListItem actions."""
import sys
import threading
import types
import unittest
from unittest.mock import patch

from actions import ui_list
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


class FakeListSelection:
    def __init__(self, multiple=False):
        self.CurrentCanSelectMultiple = multiple


class FakeRect:
    def width(self):
        return 100

    def height(self):
        return 24


class FakeList:
    def __init__(self, multiple=False):
        self.element_info = types.SimpleNamespace(control_type="List")
        self.iface_selection = FakeListSelection(multiple)


class FakeControl:
    def __init__(self, name="Invoices", role="ListItem", parent=None,
                 pattern=None, enabled=True):
        self.element_info = types.SimpleNamespace(
            control_type=role, name=name, is_password=False, is_offscreen=False,
        )
        self.iface_selection_item = pattern or FakeSelectionPattern()
        self._parent = parent or FakeList()
        self._enabled = enabled

    def rectangle(self):
        return FakeRect()

    def is_enabled(self):
        return self._enabled

    def parent(self):
        return self._parent


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


class ListItemTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_list, "ui_list.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "select_list_item")
        self.assertEqual(set(record.parameters["required"]), {"item_label"})

    def test_single_selection_list_is_supported(self):
        item = FakeControl(pattern=FakeSelectionPattern())
        parent, reason = ui_list._single_selection_parent(item)
        self.assertIsNotNone(parent)
        self.assertEqual(reason, "")

    def test_multi_selection_list_is_rejected(self):
        item = FakeControl(parent=FakeList(multiple=True))
        parent, reason = ui_list._single_selection_parent(item)
        self.assertIsNone(parent)
        self.assertIn("multi-selection", reason)

    def test_unselected_item_is_selected_once_and_verified(self):
        pattern = FakeSelectionPattern()
        status, _ = ui_list._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "selected")
        self.assertTrue(pattern.CurrentIsSelected)
        self.assertEqual(pattern.calls, 1)

    def test_already_selected_item_is_not_selected_twice(self):
        pattern = FakeSelectionPattern(selected=True)
        status, _ = ui_list._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "already_selected")
        self.assertEqual(pattern.calls, 0)

    def test_provider_error_is_uncertain_and_not_retried(self):
        pattern = FakeSelectionPattern(fail=True)
        status, _ = ui_list._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(pattern.calls, 1)

    def test_missing_postcondition_is_unverified(self):
        pattern = FakeSelectionPattern(stuck=True)
        status, _ = ui_list._select_pattern(pattern, threading.Event())
        self.assertEqual(status, "unverified")
        self.assertEqual(pattern.calls, 1)

    def test_cancelled_operation_does_not_select(self):
        pattern = FakeSelectionPattern()
        cancelled = threading.Event()
        cancelled.set()
        status, _ = ui_list._select_pattern(pattern, cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.calls, 0)

    def test_worker_targets_exact_list_item(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl("Invoices", pattern=pattern)])
        with patch_desktop(window):
            status, _ = ui_list._select_on_window(
                111, 222, "Invoices", threading.Event(),
            )
        self.assertEqual(status, "selected")
        self.assertTrue(pattern.CurrentIsSelected)

    def test_wrong_process_is_rejected(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl(pattern=pattern)], process_id=999)
        with patch_desktop(window):
            status, _ = ui_list._select_on_window(
                111, 222, "Invoices", threading.Event(),
            )
        self.assertEqual(status, "changed_window")
        self.assertEqual(pattern.calls, 0)

    def test_duplicate_item_labels_are_rejected(self):
        one, two = FakeSelectionPattern(), FakeSelectionPattern()
        window = FakeWindow([
            FakeControl(pattern=one), FakeControl(pattern=two),
        ])
        with patch_desktop(window):
            status, _ = ui_list._select_on_window(
                111, 222, "Invoices", threading.Event(),
            )
        self.assertEqual(status, "ambiguous_item")
        self.assertEqual(one.calls, 0)
        self.assertEqual(two.calls, 0)

    def test_multi_select_list_is_not_modified(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([
            FakeControl(parent=FakeList(multiple=True), pattern=pattern),
        ])
        with patch_desktop(window):
            status, reason = ui_list._select_on_window(
                111, 222, "Invoices", threading.Event(),
            )
        self.assertEqual(status, "unsupported_list")
        self.assertIn("multi-selection", reason)
        self.assertEqual(pattern.calls, 0)

    def test_sensitive_item_is_not_selected(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl("PIN", pattern=pattern)])
        with patch_desktop(window):
            status, _ = ui_list._select_on_window(
                111, 222, "PIN", threading.Event(),
            )
        self.assertEqual(status, "missing_item")
        self.assertEqual(pattern.calls, 0)

    def test_invalid_label_fails_before_desktop_access(self):
        with patch.object(ui_list.ui, "_snapshot") as snapshot:
            self.assertIn("status=failed", ui_list.select_list_item({}))
            self.assertIn("status=failed", ui_list.select_list_item({
                "item_label": "x" * 121,
            }))
            self.assertIn("status=failed", ui_list.select_list_item({
                "item_label": "PIN",
            }))
        snapshot.assert_not_called()

    def test_handler_verifies_selection_and_returns_no_item_content(self):
        pattern = FakeSelectionPattern()
        window = FakeWindow([FakeControl(pattern=pattern)])
        with patch_desktop(window), \
             patch.object(ui_list.ui, "_snapshot", return_value={"handle": 111, "process_id": 222}), \
             patch.object(ui_list.ui, "_run_uia",
                          side_effect=lambda callback, *args, **kwargs:
                              (True, callback(threading.Event()), None)):
            result = ui_list.select_list_item({"item_label": "Invoices"})
        self.assertIn("status=done", result)
        self.assertIn("selected=true", result)
        self.assertTrue(pattern.CurrentIsSelected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
