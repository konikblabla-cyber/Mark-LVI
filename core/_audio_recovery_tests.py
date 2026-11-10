"""Offline tests for audio stream recovery decisions."""
import unittest
from types import SimpleNamespace

from core.audio_recovery import callback_is_stalled, stream_is_active


class AudioRecoveryTests(unittest.TestCase):
    def test_missing_or_stopped_stream_is_unhealthy(self):
        self.assertFalse(stream_is_active(None))
        self.assertFalse(stream_is_active(SimpleNamespace(active=False)))

    def test_live_stream_is_healthy(self):
        self.assertTrue(stream_is_active(SimpleNamespace(active=True)))

    def test_stream_state_errors_fail_closed(self):
        class BrokenStream:
            @property
            def active(self):
                raise RuntimeError("PortAudio device disappeared")

        self.assertFalse(stream_is_active(BrokenStream()))

    def test_recent_input_callback_is_not_stalled(self):
        self.assertFalse(callback_is_stalled(10.0, 14.9))
        self.assertFalse(callback_is_stalled(10.0, 15.0))

    def test_missing_callback_heartbeat_is_stalled(self):
        self.assertTrue(callback_is_stalled(10.0, 15.01, stall_after=5.0))

    def test_invalid_or_backwards_clock_does_not_trigger_reconnect(self):
        self.assertFalse(callback_is_stalled(None, 20.0))
        self.assertFalse(callback_is_stalled("bad", 20.0))
        self.assertFalse(callback_is_stalled(20.0, 19.0))
        self.assertFalse(callback_is_stalled(float("nan"), 30.0))
        self.assertFalse(callback_is_stalled(10.0, 30.0, stall_after=0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
