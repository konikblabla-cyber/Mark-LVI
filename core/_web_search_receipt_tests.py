"""Direct tests for the web-search action's execution receipt and trust boundary."""
import unittest
from unittest.mock import patch

from actions import web_search
from core import outcome


class WebSearchReceiptTests(unittest.TestCase):
    @patch("actions.web_search._search", return_value="Grounded search content")
    def test_successful_lookup_returns_receipt_and_wrapped_external_content(self, _search):
        result = web_search.web_search({"query": "a current topic", "mode": "search"})
        self.assertTrue(result.startswith("status=done action=web_search verified=true"))
        self.assertIn("external_data_untrusted=true", result)
        self.assertIn("<<<UNTRUSTED source=web>>>\nGrounded search content\n<<<END UNTRUSTED>>>", result)
        self.assertEqual(outcome.unwrap(result), "Grounded search content")
        _search.assert_called_once_with("a current topic")

    @patch("actions.web_search._search", return_value="No results found for: missing topic")
    def test_empty_lookup_returns_unverified_receipt(self, _search):
        result = web_search.web_search({"query": "missing topic"})
        self.assertTrue(result.startswith("status=unverified action=web_search"))
        self.assertTrue(outcome.is_problem(result))
        self.assertEqual(outcome.unwrap(result), "No results found for: missing topic")

    @patch("actions.web_search._search", side_effect=RuntimeError("provider offline"))
    def test_provider_exception_returns_failed_receipt(self, _search):
        result = web_search.web_search({"query": "topic"})
        self.assertTrue(result.startswith("status=failed action=web_search"))
        self.assertTrue(outcome.is_problem(result))
        self.assertNotIn("<<<UNTRUSTED source=web>>>", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
