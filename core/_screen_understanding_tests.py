"""Offline tests for local active-window accessibility output."""
import unittest

from actions.screen_understanding import format_accessible_elements


class ScreenUnderstandingTests(unittest.TestCase):
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
            max_elements=4, max_chars=400,
        )
        self.assertLessEqual(len(result), 500)
        self.assertIn("element limit", result)

    def test_sensitive_labels_are_not_returned(self):
        result = format_accessible_elements([
            {"role": "Text", "name": "Enter your one-time code"},
            {"role": "Text", "name": "Dashboard"},
        ])
        self.assertNotIn("one-time code", result.lower())
        self.assertIn("Dashboard", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
