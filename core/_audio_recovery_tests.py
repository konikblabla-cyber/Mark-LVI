"""Offline tests for audio stream recovery decisions."""
import asyncio
import unittest
from types import SimpleNamespace

from core.audio_recovery import (
    LIVE_AUDIO_QUEUE_SIZE, callback_is_stalled, offer_latest_audio, stream_is_active,
)


class AudioRecoveryTests(unittest.TestCase):
    def test_live_audio_queue_keeps_recent_frames_when_the_sender_falls_behind(self):
        q = asyncio.Queue(maxsize=2)
        self.assertTrue(offer_latest_audio(q, "first"))
        self.assertTrue(offer_latest_audio(q, "second"))
        self.assertTrue(offer_latest_audio(q, "latest"))
        self.assertEqual(q.qsize(), 2)
        self.assertEqual(q.get_nowait(), "second")
        self.assertEqual(q.get_nowait(), "latest")

    def test_dropped_audio_frame_does_not_leave_unfinished_queue_work(self):
        async def exercise():
            q = asyncio.Queue(maxsize=1)
            offer_latest_audio(q, "old")
            offer_latest_audio(q, "new")  # drops and completes "old"
            self.assertEqual(await q.get(), "new")
            q.task_done()
            await asyncio.wait_for(q.join(), timeout=0.1)

        asyncio.run(exercise())

    def test_live_audio_queue_capacity_is_short_enough_for_low_latency_streaming(self):
        self.assertLessEqual(LIVE_AUDIO_QUEUE_SIZE, 16)
        q = asyncio.Queue(maxsize=LIVE_AUDIO_QUEUE_SIZE)
        for frame in range(LIVE_AUDIO_QUEUE_SIZE * 3):
            self.assertTrue(offer_latest_audio(q, frame))
        self.assertEqual(q.qsize(), LIVE_AUDIO_QUEUE_SIZE)
        self.assertEqual(q.get_nowait(), LIVE_AUDIO_QUEUE_SIZE * 2)

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
