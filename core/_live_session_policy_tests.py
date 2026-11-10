"""Offline tests for Gemini Live GoAway session-resumption decisions."""
import unittest
from types import SimpleNamespace

from core.live_session_policy import go_away_notice, go_away_time_left


class GoAwayPolicyTests(unittest.TestCase):
    def test_absent_go_away_notice_does_not_request_reconnect(self):
        self.assertIsNone(go_away_notice(SimpleNamespace(server_content=object())))
        self.assertIsNone(go_away_time_left(SimpleNamespace(go_away=None)))

    def test_sdk_go_away_notice_is_detected_with_time_left(self):
        notice = SimpleNamespace(time_left="60s")
        response = SimpleNamespace(go_away=notice)
        self.assertIs(go_away_notice(response), notice)
        self.assertEqual(go_away_time_left(response), "60s")

    def test_dictionary_go_away_notice_is_supported(self):
        notice = {"time_left": "60s"}
        self.assertIs(go_away_notice({"go_away": notice}), notice)
        self.assertIs(go_away_notice({"goAway": notice}), notice)

    def test_missing_time_left_is_not_an_error(self):
        self.assertIsNone(go_away_time_left(SimpleNamespace(go_away=object())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
