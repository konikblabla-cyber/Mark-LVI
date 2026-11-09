"""Offline tests for MARK's read-only UI reasoning planner."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from actions import ui_reasoning as ui_reasoning_action
from core.ui_reasoning import plan_ui_task, replan_ui_task


def control(role, name, *, enabled=True, visible=True, parent_name="", parent_path=None):
    item = {"role": role, "name": name, "enabled": enabled, "visible": visible}
    if parent_name:
        item["parent_name"] = parent_name
    if parent_path is not None:
        item["parent_path"] = parent_path
    return item


class UIReasoningTests(unittest.TestCase):
    def test_tool_is_discoverable_by_the_action_loader(self):
        tool = ui_reasoning_action.TOOL
        self.assertEqual(tool["name"], "plan_ui_task")
        self.assertTrue(callable(tool["handler"]))
        self.assertEqual(set(tool["parameters"]["required"]), {"goal"})

    def test_action_wrapper_uses_snapshot_and_never_executes(self):
        snapshot = {"records": [{"role": "TabItem", "name": "Privacy", "visible": True, "enabled": True}], "clickables": []}
        with patch.object(ui_reasoning_action.ui, "_snapshot", return_value=snapshot) as inspect_ui:
            result = json.loads(ui_reasoning_action.plan_current_ui_task({"goal": "Open Privacy"}))
        inspect_ui.assert_called_once_with()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["execution"], "not_started")

    def test_snapshot_field_values_are_never_copied_to_plan(self):
        snapshot = [{"role": "CheckBox", "name": "Notifications", "enabled": True,
                     "visible": True, "value": "private user data"}]
        result = plan_ui_task("Disable Notifications", snapshot)
        self.assertNotIn("value", repr(result))
        self.assertNotIn("private user data", repr(result))

    def test_accepts_the_live_snapshot_shape(self):
        snapshot = {
            "handle": 123,
            "process_id": 456,
            "records": [{"role": "TabItem", "name": "Privacy", "visible": True, "enabled": True}],
            "clickables": [],
        }
        result = plan_ui_task("Open Privacy", snapshot)
        self.assertEqual(result["status"], "planned")

    def test_checkbox_plan_returns_existing_tool_and_boolean(self):
        result = plan_ui_task("Wyłącz opcję Send diagnostics", [control("CheckBox", "Send diagnostics")])
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["steps"][0]["tool"], "set_checkbox_state")
        self.assertEqual(result["steps"][0]["parameters"], {"checkbox_label": "Send diagnostics", "checked": False})
        self.assertEqual(result["execution"], "not_started")

    def test_checkbox_enable(self):
        result = plan_ui_task("Enable Notifications", [control("CheckBox", "Notifications")])
        self.assertEqual(result["steps"][0]["parameters"]["checked"], True)

    def test_conflicting_checkbox_intent_needs_clarification(self):
        result = plan_ui_task("Enable and disable Notifications", [control("CheckBox", "Notifications")])
        self.assertEqual(result["status"], "needs_clarification")

    def test_tab_plan(self):
        result = plan_ui_task("Open the Privacy tab", [control("TabItem", "Privacy")])
        self.assertEqual(result["steps"][0]["tool"], "select_tab_item")
        self.assertEqual(result["steps"][0]["parameters"], {"tab_label": "Privacy"})

    def test_radio_plan(self):
        result = plan_ui_task("Select Dark mode", [control("RadioButton", "Dark mode")])
        self.assertEqual(result["steps"][0]["tool"], "select_radio_option")

    def test_list_plan(self):
        result = plan_ui_task("Select Downloads", [control("ListItem", "Downloads")])
        self.assertEqual(result["steps"][0]["tool"], "select_list_item")

    def test_dropdown_option_uses_exact_parent_combobox(self):
        snapshot = [
            control("ComboBox", "Language"),
            control("ListItem", "Polish", parent_name="Language"),
        ]
        result = plan_ui_task("Select Polish in Language", snapshot)
        self.assertEqual(result["steps"][0]["tool"], "select_dropdown_option")
        self.assertEqual(result["steps"][0]["parameters"],
                         {"combo_label": "Language", "option_label": "Polish"})

    def test_tree_expand_and_collapse(self):
        snapshot = [control("TreeItem", "Documents")]
        self.assertTrue(plan_ui_task("Expand Documents", snapshot)["steps"][0]["parameters"]["expanded"])
        self.assertFalse(plan_ui_task("Collapse Documents", snapshot)["steps"][0]["parameters"]["expanded"])

    def test_click_requires_exact_expected_postcondition(self):
        result = plan_ui_task("Click Settings", [control("Button", "Settings")])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["reason"], "click_postcondition_missing")

    def test_click_with_new_exact_expected_text(self):
        result = plan_ui_task('Click Settings and verify "Privacy panel"', [control("Button", "Settings")])
        self.assertEqual(result["steps"][0]["tool"], "click_and_verify")
        self.assertEqual(result["steps"][0]["parameters"]["expected_result"], "Privacy panel")

    def test_click_postcondition_already_visible_is_rejected(self):
        snapshot = [control("Button", "Settings"), control("Text", "Privacy panel")]
        result = plan_ui_task('Click Settings and verify "Privacy panel"', snapshot)
        self.assertEqual(result["status"], "needs_clarification")

    def test_duplicate_labels_need_parent_context(self):
        snapshot = [
            control("TabItem", "General", parent_name="Account"),
            control("TabItem", "General", parent_name="Network"),
        ]
        result = plan_ui_task("Open General", snapshot)
        self.assertEqual(result["reason"], "duplicate_label")

    def test_duplicate_labels_can_use_unique_parent(self):
        snapshot = [
            control("TabItem", "General", parent_name="Account"),
            control("TabItem", "General", parent_name="Network"),
        ]
        result = plan_ui_task("Open Network General", snapshot)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["steps"][0]["target"]["parent_name"], "Network")

    def test_missing_target_needs_clarification(self):
        result = plan_ui_task("Open Privacy", [control("TabItem", "General")])
        self.assertEqual(result["status"], "needs_clarification")

    def test_disabled_target_is_blocked(self):
        result = plan_ui_task("Open Privacy", [control("TabItem", "Privacy", enabled=False)])
        self.assertEqual(result["status"], "blocked")

    def test_invisible_target_is_not_considered(self):
        result = plan_ui_task("Open Privacy", [control("TabItem", "Privacy", visible=False)])
        self.assertEqual(result["status"], "needs_clarification")

    def test_unknown_enabled_state_needs_refresh(self):
        result = plan_ui_task("Open Privacy", [{"role": "TabItem", "name": "Privacy", "visible": True}])
        self.assertEqual(result["reason"], "enabled_state_unknown")

    def test_sensitive_goal_is_blocked(self):
        result = plan_ui_task("Enter password 12345", [control("Edit", "Password")])
        self.assertEqual(result["status"], "blocked")

    def test_sensitive_labels_are_filtered(self):
        result = plan_ui_task("Open API token", [control("TabItem", "API token")])
        self.assertEqual(result["status"], "blocked")

    def test_email_and_phone_labels_are_filtered(self):
        for label in ("owner@example.com", "+48 123 456 789"):
            with self.subTest(label=label):
                result = plan_ui_task(f"Open {label}", [control("TabItem", label)])
                self.assertEqual(result["status"], "blocked")

    def test_snapshot_size_limit(self):
        snapshot = [control("TabItem", f"Tab {index}") for index in range(241)]
        result = plan_ui_task("Open Tab 1", snapshot)
        self.assertEqual(result["status"], "blocked")

    def test_invalid_goal_and_snapshot(self):
        self.assertEqual(plan_ui_task("", [])["status"], "blocked")
        self.assertEqual(plan_ui_task("Open tab", None)["status"], "blocked")

    def test_replan_after_verified_action_uses_fresh_snapshot(self):
        result = replan_ui_task(
            "Open Privacy", [control("TabItem", "Privacy")], {"status": "done"},
        )
        self.assertEqual(result["status"], "planned")

    def test_replan_never_retries_unverified_action(self):
        result = replan_ui_task(
            "Open Privacy", [control("TabItem", "Privacy")], {"status": "unverified"},
        )
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["reason"], "previous_action_unverified")

    def test_replan_does_not_repeat_failed_same_action(self):
        previous_step = {"tool": "select_tab_item", "parameters": {"tab_label": "Privacy"}}
        result = replan_ui_task(
            "Open Privacy", [control("TabItem", "Privacy")],
            {"status": "failed", "step": previous_step},
        )
        self.assertEqual(result["reason"], "same_action_retry_blocked")

    def test_replan_accepts_a_different_next_step_after_failure(self):
        previous_step = {"tool": "select_tab_item", "parameters": {"tab_label": "General"}}
        result = replan_ui_task(
            "Open Privacy", [control("TabItem", "Privacy")],
            {"status": "failed", "step": previous_step},
        )
        self.assertEqual(result["status"], "planned")

    def test_high_impact_target_is_marked_for_confirmation(self):
        result = plan_ui_task("Open Privacy settings tab", [control("TabItem", "Privacy settings")])
        self.assertTrue(result["steps"][0]["requires_confirmation"])
        self.assertEqual(result["steps"][0]["risk"], "high")


if __name__ == "__main__":
    unittest.main(verbosity=2)
