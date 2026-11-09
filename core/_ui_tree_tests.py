"""Offline tests for verified Windows UIA TreeItem expansion and collapse."""
import sys
import threading
import types
import unittest
from unittest.mock import patch

from actions import ui_tree
from core.action_loader import _validate


class FakeExpandCollapsePattern:
    def __init__(self, state=0, fail_expand=False, fail_collapse=False, stuck=False):
        self.CurrentExpandCollapseState = state
        self.expand_calls = 0
        self.collapse_calls = 0
        self.fail_expand = fail_expand
        self.fail_collapse = fail_collapse
        self.stuck = stuck

    def Expand(self):
        self.expand_calls += 1
        if self.fail_expand:
            raise RuntimeError("provider error")
        if not self.stuck:
            self.CurrentExpandCollapseState = 1

    def Collapse(self):
        self.collapse_calls += 1
        if self.fail_collapse:
            raise RuntimeError("provider error")
        if not self.stuck:
            self.CurrentExpandCollapseState = 0


class FakeRect:
    def width(self):
        return 100

    def height(self):
        return 24


class FakeControl:
    def __init__(self, name="Projects", role="TreeItem", pattern=None,
                 enabled=True, offscreen=False, password=False):
        self.element_info = types.SimpleNamespace(
            control_type=role, name=name, is_password=password, is_offscreen=offscreen,
        )
        self.iface_expand_collapse = pattern or FakeExpandCollapsePattern()
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


class TreeItemTests(unittest.TestCase):
    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_tree, "ui_tree.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "set_tree_item_expanded")
        self.assertEqual(set(record.parameters["required"]), {"item_label", "expanded"})

    def test_collapsed_item_expands_once_and_is_verified(self):
        pattern = FakeExpandCollapsePattern(state=0)
        status, _ = ui_tree._set_expand_collapse(pattern, True, threading.Event())
        self.assertEqual(status, "expanded")
        self.assertEqual(pattern.CurrentExpandCollapseState, 1)
        self.assertEqual(pattern.expand_calls, 1)

    def test_expanded_item_collapses_once_and_is_verified(self):
        pattern = FakeExpandCollapsePattern(state=1)
        status, _ = ui_tree._set_expand_collapse(pattern, False, threading.Event())
        self.assertEqual(status, "collapsed")
        self.assertEqual(pattern.CurrentExpandCollapseState, 0)
        self.assertEqual(pattern.collapse_calls, 1)

    def test_partially_expanded_counts_as_expanded(self):
        pattern = FakeExpandCollapsePattern(state=2)
        status, _ = ui_tree._set_expand_collapse(pattern, True, threading.Event())
        self.assertEqual(status, "already_set")
        self.assertEqual(pattern.expand_calls, 0)

    def test_already_correct_state_is_idempotent(self):
        for state, desired in ((0, False), (1, True), (2, True)):
            with self.subTest(state=state, desired=desired):
                pattern = FakeExpandCollapsePattern(state=state)
                status, _ = ui_tree._set_expand_collapse(pattern, desired, threading.Event())
                self.assertEqual(status, "already_set")
                self.assertEqual(pattern.expand_calls, 0)
                self.assertEqual(pattern.collapse_calls, 0)

    def test_leaf_node_is_never_changed(self):
        pattern = FakeExpandCollapsePattern(state=3)
        for desired in (True, False):
            with self.subTest(desired=desired):
                status, _ = ui_tree._set_expand_collapse(pattern, desired, threading.Event())
                self.assertEqual(status, "not_expandable")
        self.assertEqual(pattern.expand_calls, 0)
        self.assertEqual(pattern.collapse_calls, 0)

    def test_provider_error_after_expand_is_uncertain_and_not_retried(self):
        pattern = FakeExpandCollapsePattern(state=0, fail_expand=True)
        status, _ = ui_tree._set_expand_collapse(pattern, True, threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(pattern.expand_calls, 1)

    def test_provider_error_after_collapse_is_uncertain_and_not_retried(self):
        pattern = FakeExpandCollapsePattern(state=1, fail_collapse=True)
        status, _ = ui_tree._set_expand_collapse(pattern, False, threading.Event())
        self.assertEqual(status, "uncertain")
        self.assertEqual(pattern.collapse_calls, 1)

    def test_missing_postcondition_is_unverified_without_retry(self):
        pattern = FakeExpandCollapsePattern(state=0, stuck=True)
        status, _ = ui_tree._set_expand_collapse(pattern, True, threading.Event())
        self.assertEqual(status, "unverified")
        self.assertEqual(pattern.expand_calls, 1)

    def test_cancelled_operation_does_not_call_provider(self):
        pattern = FakeExpandCollapsePattern(state=0)
        cancelled = threading.Event()
        cancelled.set()
        status, _ = ui_tree._set_expand_collapse(pattern, True, cancelled)
        self.assertEqual(status, "cancelled")
        self.assertEqual(pattern.expand_calls, 0)

    def test_worker_targets_one_exact_visible_tree_item(self):
        pattern = FakeExpandCollapsePattern()
        window = FakeWindow([
            FakeControl("Documents", pattern=FakeExpandCollapsePattern()),
            FakeControl("Projects", pattern=pattern),
        ])
        with patch_desktop(window):
            status, _ = ui_tree._set_on_window(111, 222, "Projects", True, threading.Event())
        self.assertEqual(status, "expanded")
        self.assertEqual(pattern.expand_calls, 1)

    def test_wrong_process_is_rejected_before_expand(self):
        pattern = FakeExpandCollapsePattern()
        window = FakeWindow([FakeControl(pattern=pattern)], process_id=999)
        with patch_desktop(window):
            status, _ = ui_tree._set_on_window(111, 222, "Projects", True, threading.Event())
        self.assertEqual(status, "changed_window")
        self.assertEqual(pattern.expand_calls, 0)

    def test_duplicate_labels_are_rejected(self):
        one, two = FakeExpandCollapsePattern(), FakeExpandCollapsePattern()
        window = FakeWindow([
            FakeControl("Projects", pattern=one),
            FakeControl("Projects", pattern=two),
        ])
        with patch_desktop(window):
            status, _ = ui_tree._set_on_window(111, 222, "Projects", True, threading.Event())
        self.assertEqual(status, "ambiguous_item")
        self.assertEqual(one.expand_calls, 0)
        self.assertEqual(two.expand_calls, 0)

    def test_wrong_role_offscreen_and_disabled_items_are_ignored(self):
        wrong = FakeExpandCollapsePattern()
        offscreen = FakeExpandCollapsePattern()
        disabled = FakeExpandCollapsePattern()
        real = FakeExpandCollapsePattern()
        window = FakeWindow([
            FakeControl("Projects", role="ListItem", pattern=wrong),
            FakeControl("Projects", pattern=offscreen, offscreen=True),
            FakeControl("Projects", pattern=disabled, enabled=False),
            FakeControl("Projects", pattern=real),
        ])
        with patch_desktop(window):
            status, _ = ui_tree._set_on_window(111, 222, "Projects", True, threading.Event())
        self.assertEqual(status, "expanded")
        self.assertEqual(wrong.expand_calls, 0)
        self.assertEqual(offscreen.expand_calls, 0)
        self.assertEqual(disabled.expand_calls, 0)
        self.assertEqual(real.expand_calls, 1)

    def test_sensitive_tree_item_is_not_selected(self):
        pattern = FakeExpandCollapsePattern()
        window = FakeWindow([FakeControl("PIN", pattern=pattern)])
        with patch_desktop(window):
            status, _ = ui_tree._set_on_window(111, 222, "PIN", True, threading.Event())
        self.assertEqual(status, "missing_item")
        self.assertEqual(pattern.expand_calls, 0)

    def test_invalid_inputs_fail_before_desktop_access(self):
        with patch.object(ui_tree.ui, "_snapshot") as snapshot:
            for params in (
                {}, {"item_label": "x", "expanded": "true"},
                {"item_label": "PIN", "expanded": True},
                {"item_label": "x" * 121, "expanded": False},
            ):
                with self.subTest(params=params):
                    self.assertIn("status=failed", ui_tree.set_tree_item_expanded(params))
        snapshot.assert_not_called()

    def test_handler_verifies_and_does_not_return_tree_contents(self):
        pattern = FakeExpandCollapsePattern()
        window = FakeWindow([FakeControl(pattern=pattern)])
        with patch_desktop(window), \
             patch.object(ui_tree.ui, "_snapshot", return_value={"handle": 111, "process_id": 222}), \
             patch.object(ui_tree.ui, "_run_uia",
                          side_effect=lambda callback, *args, **kwargs:
                              (True, callback(threading.Event()), None)):
            result = ui_tree.set_tree_item_expanded({
                "item_label": "Projects", "expanded": True,
            })
        self.assertIn("status=done", result)
        self.assertIn("expanded=true", result)
        self.assertNotIn("child", result.lower())
        self.assertEqual(pattern.CurrentExpandCollapseState, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
