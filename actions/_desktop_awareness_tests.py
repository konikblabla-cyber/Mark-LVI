"""Offline tests for on-demand desktop awareness."""
import unittest
from unittest.mock import patch

from actions import desktop_awareness


class DesktopAwarenessTests(unittest.TestCase):
    def test_reports_foreground_application_and_activity_only(self):
        context = {
            "available": True,
            "foreground_app": r"C:\Program Files\Microsoft VS Code\Code.exe",
            "idle_seconds": 4.2,
            "fullscreen": False,
            "fullscreen_known": True,
        }
        with patch.object(desktop_awareness, "get_desktop_context", return_value=context):
            snapshot = desktop_awareness.describe_current_desktop()
        self.assertEqual(snapshot["application"], "code")
        self.assertEqual(snapshot["category"], "development")
        self.assertEqual(snapshot["activity"], "programming")
        self.assertEqual(snapshot["idle_seconds"], 4.2)
        self.assertNotIn("window_title", snapshot)
        self.assertNotIn("screenshot", snapshot)

    def test_unavailable_desktop_sensor_is_reported_without_guessing(self):
        with patch.object(desktop_awareness, "get_desktop_context", return_value={
            "available": False,
        }):
            result = desktop_awareness.describe_current_desktop()
        self.assertFalse(result["available"])
        self.assertEqual(result["category"] if "category" in result else None, None)

    def test_action_registers_its_handler(self):
        self.assertTrue(callable(desktop_awareness.TOOL.get("handler")))

    def test_formatter_does_not_claim_unknown_sensor_data(self):
        result = desktop_awareness.format_desktop_context({
            "available": True, "application": "", "activity": "unknown activity",
            "idle_seconds": None, "fullscreen": False,
            "privacy": "No window title was read.",
        })
        self.assertIn("Keyboard/mouse idle: unknown", result)
        self.assertIn("No window title was read", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
