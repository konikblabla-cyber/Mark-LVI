"""Offline tests for multi-step UI sequence validation."""
import unittest

from actions import ui_sequence
from actions import select_tab_item, select_list_item


class UISequenceSelectionTests(unittest.TestCase):
    def test_validator_accepts_and_normalises_tab_and_list_selection(self):
        steps, error = ui_sequence._validate_steps([
            {"action": "select_tab_item", "tab_label": "  Privacy   & Security  "},
            {"action": "select_list_item", "item_label": "  Downloads "},
        ])
        self.assertEqual(error, "")
        self.assertEqual(steps, [
            {"action": "select_tab_item", "tab_label": "Privacy & Security"},
            {"action": "select_list_item", "item_label": "Downloads"},
        ])

    def test_validator_rejects_sensitive_tab_and_list_labels(self):
        for action, key in (("select_tab_item", "tab_label"), ("select_list_item", "item_label")):
            steps, error = ui_sequence._validate_steps([
                {"action": action, key: "Password"},
                {"action": "click", "button_label": "Apply", "expected_result": "Settings"},
            ])
            self.assertIsNone(steps)
            self.assertIn("sensitive", error)

    def test_validator_rejects_missing_or_extra_fields(self):
        steps, error = ui_sequence._validate_steps([
            {"action": "select_tab_item", "tab_label": "Privacy", "extra": "not allowed"},
            {"action": "click", "button_label": "Apply", "expected_result": "Settings"},
        ])
        self.assertIsNone(steps)
        self.assertIn("only action and tab_label", error)

    def test_tool_declarations_match_the_ui_reasoning_planner(self):
        self.assertEqual(select_tab_item.TOOL["name"], "select_tab_item")
        self.assertEqual(select_list_item.TOOL["name"], "select_list_item")
        self.assertIn("tab_label", select_tab_item.TOOL["parameters"]["required"])
        self.assertIn("item_label", select_list_item.TOOL["parameters"]["required"])
        enum = ui_sequence.TOOL["parameters"]["properties"]["steps"]["items"]["properties"]["action"]["enum"]
        self.assertIn("select_tab_item", enum)
        self.assertIn("select_list_item", enum)
        self.assertIn("tab_label", ui_sequence.TOOL["parameters"]["properties"]["steps"]["items"]["properties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
