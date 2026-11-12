"""Offline tests for exact Windows SelectionItemPattern operations."""
import sys
import threading
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from actions import ui_interaction as ui


class _Rect:
    def width(self): return 120
    def height(self): return 28


class _Pattern:
    def __init__(self, selected=False, fail=False):
        self.CurrentIsSelected = selected
        self.calls = 0
        self.fail = fail

    def Select(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failure")
        self.CurrentIsSelected = True


class _Control:
    def __init__(self, role, label, *, selected=False, enabled=True,
                 offscreen=False, password=False, fail=False, parent=None):
        self._parent = parent
        self.element_info = SimpleNamespace(
            control_type=role, name=label, is_password=password,
            is_offscreen=offscreen,
        )
        self.iface_selection_item = _Pattern(selected, fail)
        self._enabled = enabled

    def rectangle(self): return _Rect()
    def is_enabled(self): return self._enabled
    def parent(self): return self._parent


class _Window:
    def __init__(self, controls, handle=55, pid=777):
        self.handle = handle
        self.element_info = SimpleNamespace(process_id=pid)
        self._controls = controls

    def is_visible(self): return True
    def descendants(self): return self._controls


class _Desktop:
    def __init__(self, window):
        self._window = window

    def window(self, handle):
        assert int(handle) == self._window.handle
        return SimpleNamespace(wrapper_object=lambda: self._window)


class UISelectionTests(unittest.TestCase):
    def _run(self, controls, label, role, *, handle=55, expected_pid=777,
             window=None, cancelled=None):
        window = window or _Window(controls, handle=handle)
        fake = types.ModuleType("pywinauto")
        fake.Desktop = lambda backend: _Desktop(window)
        with patch.dict(sys.modules, {"pywinauto": fake}):
            return ui._select_item_on_window(
                handle, expected_pid, label, role,
                cancelled or threading.Event(),
            )

    def test_selects_exact_visible_tab_and_verifies_it(self):
        control = _Control("TabItem", "Privacy")
        self.assertEqual(self._run([control], "Privacy", "TabItem"), ("selected", ""))
        self.assertEqual(control.iface_selection_item.calls, 1)

    def test_selects_exact_visible_list_item_and_verifies_it(self):
        control = _Control("ListItem", "Downloads")
        self.assertEqual(self._run([control], "Downloads", "ListItem"), ("selected", ""))
        self.assertEqual(control.iface_selection_item.calls, 1)

    def test_already_selected_item_is_not_toggled_or_reselected(self):
        control = _Control("TabItem", "General", selected=True)
        self.assertEqual(self._run([control], "General", "TabItem"), ("already_selected", ""))
        self.assertEqual(control.iface_selection_item.calls, 0)

    def test_list_item_inside_combo_box_is_routed_to_dropdown_tool(self):
        combo = _Control("ComboBox", "Language")
        option = _Control("ListItem", "Polish", parent=combo)
        status, reason = self._run([option], "Polish", "ListItem")
        self.assertEqual(status, "dropdown_option")
        self.assertIn("select_dropdown_option", reason)
        self.assertEqual(option.iface_selection_item.calls, 0)

    def test_duplicate_exact_labels_are_rejected_as_ambiguous(self):
        controls = [_Control("TabItem", "General"), _Control("TabItem", "General")]
        status, _ = self._run(controls, "General", "TabItem")
        self.assertEqual(status, "ambiguous_item")
        self.assertTrue(all(c.iface_selection_item.calls == 0 for c in controls))

    def test_missing_hidden_disabled_and_sensitive_items_are_not_selected(self):
        self.assertEqual(self._run([], "Privacy", "TabItem")[0], "missing_item")
        self.assertEqual(self._run([_Control("TabItem", "Privacy", offscreen=True)], "Privacy", "TabItem")[0], "missing_item")
        self.assertEqual(self._run([_Control("TabItem", "Privacy", enabled=False)], "Privacy", "TabItem")[0], "missing_item")
        self.assertEqual(self._run([_Control("ListItem", "Password")], "Password", "ListItem")[0], "missing_item")

    def test_wrong_window_process_is_rejected_before_selection(self):
        control = _Control("ListItem", "Downloads")
        wrong = _Window([control], handle=55, pid=888)
        status, _ = self._run([control], "Downloads", "ListItem", expected_pid=777, window=wrong)
        self.assertEqual(status, "changed_window")
        self.assertEqual(control.iface_selection_item.calls, 0)

    def test_provider_exception_after_select_is_reported_uncertain(self):
        control = _Control("TabItem", "Privacy", fail=True)
        status, _ = self._run([control], "Privacy", "TabItem")
        self.assertEqual(status, "uncertain")
        self.assertEqual(control.iface_selection_item.calls, 1)

    def test_cancellation_prevents_selection(self):
        control = _Control("ListItem", "Downloads")
        event = threading.Event()
        event.set()
        status, _ = self._run([control], "Downloads", "ListItem", cancelled=event)
        self.assertEqual(status, "cancelled")
        self.assertEqual(control.iface_selection_item.calls, 0)

    def test_unknown_control_type_is_rejected(self):
        self.assertEqual(self._run([], "Privacy", "Button")[0], "unsupported")


if __name__ == "__main__":
    unittest.main(verbosity=2)
