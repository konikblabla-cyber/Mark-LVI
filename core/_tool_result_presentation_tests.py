"""Offline tests for presenting successful web-search results in the HUD."""
import unittest

from core import outcome
from core.tool_result_presentation import web_search_panel_payload


class WebSearchPanelPayloadTests(unittest.TestCase):
    def test_successful_search_unwraps_external_content_for_human_display(self):
        result = outcome.untrusted(
            "web", "A useful result\nSource: https://example.test/article"
        )
        payload = web_search_panel_payload(
            "web_search", result, {"mode": "research", "query": "test topic"}
        )
        self.assertEqual(payload, (
            "RESEARCH — test topic",
            "A useful result\nSource: https://example.test/article",
        ))

    def test_compare_label_uses_items_when_query_is_empty(self):
        payload = web_search_panel_payload(
            "web_search", "Comparison results", {"mode": "compare", "items": ["A", "B"]}
        )
        self.assertEqual(payload, ("COMPARE — A, B", "Comparison results"))

    def test_failed_or_empty_search_is_not_shown_as_successful_content(self):
        for result in (
            "", "No results found for: example", "No news found for: example",
            "Search failed: offline",
            outcome.failed("web_search", "all providers are unavailable"),
            outcome.unverified("web_search", "provider timed out"),
        ):
            with self.subTest(result=result[:50]):
                self.assertIsNone(web_search_panel_payload("web_search", result, {}))

    def test_non_search_tools_are_never_mirrored_to_search_panel(self):
        self.assertIsNone(web_search_panel_payload("open_app", "Opened", {"mode": "search"}))

    def test_malformed_arguments_do_not_break_result_presentation(self):
        self.assertEqual(
            web_search_panel_payload("web_search", "Results", {"mode": None, "items": None}),
            ("SEARCH", "Results"),
        )


if __name__ == "__main__":
    unittest.main()
