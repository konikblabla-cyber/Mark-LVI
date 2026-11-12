"""Tests for the live desktop context carried in tool results."""
import unittest

from core.focus_context import append_focus_context_for_tool


class LiveFocusContextTests(unittest.TestCase):
    def test_opt_in_tool_result_contains_only_current_app_metadata(self):
        result = append_focus_context_for_tool(
            "status=done",
            "run_ui_sequence",
            {
                "application": "Code.exe",
                "window_title": "private notes.txt",
                "document": "private notes",
                "clipboard": "secret",
            },
            enabled=True,
        )
        self.assertTrue(result.startswith("status=done"))
        self.assertIn("[LIVE DESKTOP CONTEXT", result)
        self.assertIn("code", result)
        self.assertIn("development", result)
        self.assertNotIn("private notes", result)
        self.assertNotIn("clipboard", result)
        self.assertNotIn("secret", result)

    def test_disabled_desktop_awareness_leaves_tool_result_unchanged(self):
        base = "status=done result=opened"
        self.assertEqual(
            append_focus_context_for_tool(
                base, "select_tab_item", {"application": "Opera.exe"}, enabled=False
            ),
            base,
        )

    def test_unknown_foreground_does_not_add_stale_app_context(self):
        base = "status=done"
        self.assertEqual(
            append_focus_context_for_tool(
                base, "select_list_item", {"application": "unknown-app"}, enabled=True
            ),
            base,
        )

    def test_non_desktop_tools_never_receive_focus_metadata(self):
        base = "status=done old conversation found"
        result = append_focus_context_for_tool(
            base, "search_conversation_history",
            {"application": "Discord.exe"}, enabled=True,
        )
        self.assertEqual(result, base)

    def test_only_registered_desktop_tools_receive_focus_metadata(self):
        for tool in ("select_tab_item", "select_list_item", "run_ui_sequence", "mission_control"):
            result = append_focus_context_for_tool(
                "status=done", tool, {"application": "Code.exe"}, enabled=True,
            )
            self.assertIn("[LIVE DESKTOP CONTEXT", result)

    def test_empty_result_can_still_return_opted_in_focus_metadata(self):
        result = append_live_focus_context_to_tool_result(
            "", {"application": "Discord.exe"}, enabled=True
        )
        self.assertIn("discord", result)
        self.assertIn("metadata only", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
