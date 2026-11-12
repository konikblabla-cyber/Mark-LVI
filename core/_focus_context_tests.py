"""Offline tests for privacy-limited focus context."""
import unittest
from core.focus_context import (
    FocusContextTracker, classify_application, describe_focus_context,
    format_focus_context_for_prompt, normalise_executable,
)


class FocusContextTests(unittest.TestCase):
    def test_process_path_is_reduced_to_executable_basename(self):
        self.assertEqual(normalise_executable(r"C:\Program Files\Microsoft VS Code\Code.exe"), "code")

    def test_known_apps_are_classified(self):
        self.assertEqual(classify_application("Discord.exe"), "communication")
        self.assertEqual(classify_application("Code.exe"), "development")
        self.assertEqual(classify_application("msedge.exe"), "browser")
        self.assertEqual(classify_application("steam.exe"), "gaming")

    def test_window_titles_and_document_names_are_never_returned(self):
        result = describe_focus_context(r"C:\Apps\Code.exe")
        self.assertEqual(set(result), {"application", "category", "activity"})
        self.assertNotIn("title", result)
        self.assertEqual(result["category"], "development")

    def test_session_prompt_gets_only_opt_in_safe_foreground_metadata(self):
        result = format_focus_context_for_prompt(
            r"C:\Users\person\AppData\Local\Programs\Microsoft VS Code\Code.exe"
        )
        self.assertIn("application was code", result)
        self.assertIn("programming", result)
        self.assertIn("may be stale", result)
        self.assertNotIn("Users", result)
        self.assertNotIn("person", result)
        self.assertNotIn("Code.exe", result)
        self.assertNotIn("window title", result.lower())

    def test_session_prompt_is_empty_for_unknown_foreground(self):
        self.assertEqual(format_focus_context_for_prompt(""), "")
        self.assertEqual(format_focus_context_for_prompt(None), "")

    def test_tracker_ignores_short_focus_changes(self):
        tracker = FocusContextTracker(stable_seconds=8.0)
        self.assertIsNone(tracker.observe("Code.exe", 0.0))
        self.assertIsNone(tracker.observe("chrome.exe", 3.0))
        self.assertIsNone(tracker.observe("chrome.exe", 10.9))
        self.assertEqual(tracker.observe("chrome.exe", 11.0)["category"], "browser")

    def test_tracker_reports_only_a_new_stable_context(self):
        tracker = FocusContextTracker(stable_seconds=2.0)
        tracker.observe("Code.exe", 0.0)
        first = tracker.observe("Code.exe", 2.0)
        self.assertEqual(first["category"], "development")
        self.assertIsNone(tracker.observe("Code.exe", 5.0))
        self.assertIsNone(tracker.observe("explorer.exe", 6.0))
        self.assertEqual(tracker.observe("explorer.exe", 8.0)["category"], "files")

    def test_invalid_or_backwards_timestamps_are_ignored(self):
        tracker = FocusContextTracker()
        self.assertIsNone(tracker.observe("Code.exe", "bad"))
        tracker.observe("Code.exe", 10.0)
        self.assertIsNone(tracker.observe("Code.exe", 2.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
