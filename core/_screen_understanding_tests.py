"""Offline tests for local active-window accessibility output."""
import unittest

from actions.screen_understanding import (
    _active_top_level_window,
    format_accessible_elements,
)


class ScreenUnderstandingTests(unittest.TestCase):
    def test_uses_pywinauto_069_active_visible_top_level_windows(self):
        app_window = type("Window", (), {"is_visible": lambda self: True})()

        class FakeDesktop:
            def windows(self, **kwargs):
                self.asserted = kwargs
                return [app_window]

        desktop = FakeDesktop()
        self.assertIs(_active_top_level_window(desktop), app_window)
        self.assertEqual(desktop.asserted, {
            "active_only": True,
            "visible_only": True,
            "top_level_only": True,
        })

    def test_no_active_window_returns_none(self):
        class FakeDesktop:
            def windows(self, **kwargs):
                return []

        self.assertIsNone(_active_top_level_window(FakeDesktop()))

    def test_multiple_active_roots_are_not_guessed(self):
        class FakeWindow:
            def is_visible(self):
                return True

        class FakeDesktop:
            def windows(self, **kwargs):
                return [FakeWindow(), FakeWindow()]

        self.assertIsNone(_active_top_level_window(FakeDesktop()))

    def test_invisible_or_inaccessible_windows_are_ignored(self):
        class VisibleWindow:
            def is_visible(self):
                return True

        class InvisibleWindow:
            def is_visible(self):
                return False

        class BrokenWindow:
            def is_visible(self):
                raise RuntimeError("stale")

        class FakeDesktop:
            def windows(self, **kwargs):
                return [InvisibleWindow(), BrokenWindow(), VisibleWindow()]

        self.assertIsInstance(_active_top_level_window(FakeDesktop()), VisibleWindow)

    def test_missing_windows_api_returns_none(self):
        self.assertIsNone(_active_top_level_window(object()))

    def test_formats_accessible_labels(self):
        result = format_accessible_elements([
            {"role": "Text", "name": "Settings"},
            {"role": "Button", "name": "Save"},
        ])
        self.assertIn("Text: Settings", result)
        self.assertIn("Button: Save", result)

    def test_omits_editable_fields_and_unknown_roles(self):
        result = format_accessible_elements([
            {"role": "Edit", "name": "my private email"},
            {"role": "Document", "name": "private document body"},
            {"role": "Custom", "name": "unsupported control"},
        ])
        self.assertNotIn("private", result)
        self.assertNotIn("unsupported", result)

    def test_omits_password_and_offscreen_controls(self):
        result = format_accessible_elements([
            {"role": "Text", "name": "hidden password", "password": True},
            {"role": "Button", "name": "Invisible", "offscreen": True},
        ])
        self.assertNotIn("password", result.lower())
        self.assertNotIn("Invisible", result)

    def test_deduplicates_repeated_labels(self):
        result = format_accessible_elements([
            {"role": "Button", "name": "OK"},
            {"role": "Button", "name": "OK"},
        ])
        self.assertEqual(result.count("Button: OK"), 1)

    def test_bounds_output_length(self):
        result = format_accessible_elements(
            [{"role": "Text", "name": f"item-{i}-" + "x" * 100} for i in range(20)],
            max_elements=4, max_chars=4000,
        )
        self.assertLessEqual(len(result), 500)
        self.assertIn("output truncated", result)

    def test_sensitive_labels_are_not_returned(self):
        result = format_accessible_elements([
            {"role": "Text", "name": "Enter your one-time code"},
            {"role": "Text", "name": "Dashboard"},
        ])
        self.assertNotIn("one-time code", result.lower())
        self.assertIn("Dashboard", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
