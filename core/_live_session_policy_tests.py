"""Offline tests for Gemini Live GoAway session-resumption decisions."""
import unittest
from types import SimpleNamespace

from core.live_session_policy import go_away_notice, go_away_time_left, live_turn_is_active


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

    def test_idle_session_has_no_live_turn_to_interrupt(self):
        self.assertFalse(live_turn_is_active())

    def test_user_or_assistant_transcript_keeps_current_turn_alive(self):
        self.assertTrue(live_turn_is_active(input_pending=True))
        self.assertTrue(live_turn_is_active(output_pending=True))
        self.assertTrue(live_turn_is_active(response_payload=True))

    def test_queued_audio_speaking_or_tool_call_defers_reconnect(self):
        self.assertTrue(live_turn_is_active(audio_pending=True))
        self.assertTrue(live_turn_is_active(assistant_speaking=True))
        self.assertTrue(live_turn_is_active(tool_call_pending=True))
    def test_missing_time_left_is_not_an_error(self):
        self.assertIsNone(go_away_time_left(SimpleNamespace(go_away=object())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
