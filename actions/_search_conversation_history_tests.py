"""Offline tests for the on-demand local conversation history action."""
import unittest
from unittest.mock import patch

from actions import search_conversation_history as action


class SearchConversationHistoryTests(unittest.TestCase):
    def test_history_disabled_fails_closed_without_search(self):
        with patch.object(action.history, "settings", return_value={"enabled": False}), \
             patch.object(action.history, "search_messages") as search:
            result = action.search_conversation_history({"query": "old plan"})
        self.assertIn("status=failed", result)
        self.assertIn("disabled", result)
        search.assert_not_called()

    def test_query_is_bounded(self):
        result = action.search_conversation_history({"query": "x" * 201})
        self.assertIn("status=failed", result)

    def test_matches_are_returned_as_untrusted_historical_context(self):
        rows = [
            {
                "role": "user",
                "ts": "2026-11-11T20:12:13+01:00",
                "text": "We decided to use Gemini Live for natural voice interaction.",
            },
            {
                "role": "assistant",
                "ts": "2026-11-11T20:12:14+01:00",
                "text": "I will keep Groq optional for coding.",
            },
        ]
        with patch.object(action.history, "settings", return_value={"enabled": True}), \
             patch.object(action.history, "search_messages", return_value=rows) as search:
            result = action.search_conversation_history({"query": "Gemini Live"})
        self.assertIn("status=done", result)
        self.assertIn("[UNTRUSTED HISTORICAL TRANSCRIPT MATCHES]", result)
        self.assertIn("past conversation snippets", result)
        self.assertIn("We decided to use Gemini Live", result)
        self.assertIn("ask the user to confirm", result)
        search.assert_called_once_with("Gemini Live", limit=5)

    def test_no_match_is_a_verified_empty_search_not_an_error(self):
        with patch.object(action.history, "settings", return_value={"enabled": True}), \
             patch.object(action.history, "search_messages", return_value=[]):
            result = action.search_conversation_history({"query": "nonexistent project"})
        self.assertIn("status=done", result)
        self.assertIn("matches=0", result)
        self.assertIn("No matching", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
