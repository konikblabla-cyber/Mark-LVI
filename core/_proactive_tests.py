"""Offline tests for proactive retry/cooldown behavior."""
import unittest
from unittest.mock import patch
from actions.proactive import ProactiveEngine


class ProactiveRetryTests(unittest.TestCase):
    def test_failed_attempt_gets_short_retry_without_success_cooldown(self):
        engine = ProactiveEngine(min_silence_secs=10, check_cooldown=1200)
        # Start beyond the engine's initial success cooldown (last_triggered=0),
        # so this test isolates the 120-second failed-attempt retry window.
        with patch("actions.proactive.time.monotonic", return_value=2000.0):
            self.assertTrue(engine.should_trigger(0.0))
            engine.mark_attempted()
        with patch("actions.proactive.time.monotonic", return_value=2050.0):
            self.assertFalse(engine.should_trigger(0.0))
        with patch("actions.proactive.time.monotonic", return_value=2121.0):
            self.assertTrue(engine.should_trigger(0.0))

    def test_success_consumes_normal_cooldown_and_rotates_focus(self):
        engine = ProactiveEngine(min_silence_secs=10, check_cooldown=1200)
        with patch("actions.proactive.time.monotonic", return_value=1000.0):
            engine.mark_attempted()
            engine.mark_triggered()
            self.assertEqual(engine._rotation, 1)
        with patch("actions.proactive.time.monotonic", return_value=1300.0):
            self.assertFalse(engine.should_trigger(0.0))
        with patch("actions.proactive.time.monotonic", return_value=2201.0):
            self.assertTrue(engine.should_trigger(0.0))

    def test_user_must_still_be_silent_long_enough(self):
        engine = ProactiveEngine(min_silence_secs=900)
        with patch("actions.proactive.time.monotonic", return_value=1000.0):
            self.assertFalse(engine.should_trigger(500.0))


if __name__ == "__main__":
    unittest.main()
