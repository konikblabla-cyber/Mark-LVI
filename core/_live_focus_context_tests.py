"""Tests for the live desktop context carried in tool results."""
import unittest

from core.focus_context import append_live_focus_context_to_tool_result


class LiveFocusContextTests(unittest.TestCase):
    def test_opt_in_tool_result_contains_only_current_app_metadata(self):
        result = append_live_focus_context_to_tool_result(
            "status=done",
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
            append_live_focus_context_to_tool_result(
                base, {"application": "Opera.exe"}, enabled=False
            ),
            base,
        )

    def test_unknown_foreground_does_not_add_stale_app_context(self):
        base = "status=done"
        self.assertEqual(
            append_live_focus_context_to_tool_result(
                base, {"application": "unknown-app"}, enabled=True
            ),
            base,
        )

    def test_empty_result_can_still_return_opted_in_focus_metadata(self):
        result = append_live_focus_context_to_tool_result(
            "", {"application": "Discord.exe"}, enabled=True
        )
        self.assertIn("discord", result)
        self.assertIn("metadata only", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
