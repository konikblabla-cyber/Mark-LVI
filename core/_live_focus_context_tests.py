"""Tests for opt-in foreground metadata attached to desktop tool results."""
import unittest

from core.focus_context import append_focus_context_for_tool


class LiveFocusContextTests(unittest.TestCase):
    def test_opted_in_ui_result_contains_only_stable_app_metadata(self):
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

    def test_disabled_desktop_awareness_leaves_result_unchanged(self):
        base = "status=done result=opened"
        self.assertEqual(
            append_focus_context_for_tool(
                base, "select_tab_item", {"application": "Opera.exe"}, enabled=False
            ),
            base,
        )

    def test_non_desktop_tools_never_receive_foreground_metadata(self):
        base = "status=done old conversation found"
        for tool in ("search_conversation_history", "weather_report", "recall_memory"):
            self.assertEqual(
                append_focus_context_for_tool(
                    base, tool, {"application": "Discord.exe"}, enabled=True
                ),
                base,
            )

    def test_unknown_foreground_never_adds_stale_app_context(self):
        base = "status=done"
        self.assertEqual(
            append_focus_context_for_tool(
                base, "select_list_item", {"application": "unknown-app"}, enabled=True
            ),
            base,
        )

    def test_supported_desktop_tools_receive_focus_metadata(self):
        for tool in (
            "select_tab_item", "select_list_item", "run_ui_sequence",
            "click_and_verify", "mission_control",
        ):
            result = append_focus_context_for_tool(
                "status=done", tool, {"application": "Code.exe"}, enabled=True
            )
            self.assertIn("[LIVE DESKTOP CONTEXT", result)

    def test_empty_result_can_still_return_opted_in_focus_metadata(self):
        result = append_focus_context_for_tool(
            "", "click_and_verify", {"application": "Discord.exe"}, enabled=True
        )
        self.assertIn("discord", result)
        self.assertIn("metadata only", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
