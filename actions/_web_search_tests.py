"""Offline regression tests for successful and empty web-search tool receipts."""
import unittest
from unittest.mock import patch

import actions.web_search as web_search_module


class WebSearchMissionEvidenceTests(unittest.TestCase):
    def test_successful_research_returns_app_receipt_and_untrusted_page_data(self):
        with patch.object(web_search_module, "_research", return_value=(
            "Research result\nSource: https://example.test/article"
        )):
            result = web_search_module.web_search({
                "query": "test research topic", "mode": "research",
            })

        self.assertTrue(result.startswith("status=done action=web_search"))
        self.assertIn("verified=true", result.splitlines()[0])
        self.assertIn("external_data_untrusted=true", result.splitlines()[0])
        self.assertIn("<<<UNTRUSTED source=web>>>", result)
        self.assertIn("Research result", result)

    def test_empty_search_result_is_unverified_not_mission_success(self):
        with patch.object(
            web_search_module, "_search", return_value="No results found for: empty query"
        ):
            result = web_search_module.web_search({"query": "empty query"})

        self.assertTrue(result.startswith("status=unverified action=web_search"))
        self.assertNotIn("status=done action=web_search", result)
        self.assertIn("<<<UNTRUSTED source=web>>>", result)

    def test_provider_exception_is_a_failed_search_receipt(self):
        with patch.object(web_search_module, "_search", side_effect=RuntimeError("offline")):
            result = web_search_module.web_search({"query": "test topic"})
        self.assertTrue(result.startswith("status=failed action=web_search"))
        self.assertNotIn("verified=true", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
