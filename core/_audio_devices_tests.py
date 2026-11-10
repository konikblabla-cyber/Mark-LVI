"""Offline regression tests for Windows audio endpoint selection.

Run with: python -m core._audio_devices_tests
All PortAudio streams are faked; no microphone, speaker or audio driver is needed.
"""
import sys
import types
import unittest
from unittest.mock import patch

from core import audio_devices


class AudioDeviceTests(unittest.TestCase):
    def setUp(self):
        audio_devices._probe_results.clear()
        audio_devices._chosen_api.update({"input": None, "output": None})

    def test_output_transport_is_cached_per_endpoint_not_per_host_api(self):
        closed = []

        class FakeOutput:
            def __init__(self, *, device, **_kwargs):
                self.device = device

            def start(self):
                pass

            def write(self, _data):
                pass

            def stop(self):
                closed.append(("stop", self.device))

            def close(self):
                closed.append(("close", self.device))

        fake_sd = types.SimpleNamespace(RawOutputStream=FakeOutput)
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            # Device 1 swallows audio; device 2 consumes it in real time.
            with patch.object(audio_devices.time, "monotonic",
                              side_effect=[0.0, 0.01, 1.0, 1.7]):
                self.assertFalse(audio_devices._transport_works(1, "output", "mme"))
                self.assertTrue(audio_devices._transport_works(2, "output", "mme"))
                # Cached result for device 1 must not be confused with device 2.
                self.assertFalse(audio_devices._transport_works(1, "output", "mme"))

        self.assertEqual(closed, [
            ("stop", 1), ("close", 1), ("stop", 2), ("close", 2),
        ])

    def test_stream_is_closed_when_transport_probe_raises(self):
        closed = []

        class FakeOutput:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

            def write(self, _data):
                raise RuntimeError("device disappeared")

            def stop(self):
                closed.append("stop")

            def close(self):
                closed.append("close")

        fake_sd = types.SimpleNamespace(RawOutputStream=FakeOutput)
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            self.assertFalse(audio_devices._transport_works(7, "output", "mme"))

        self.assertEqual(closed, ["stop", "close"])

    def test_query_skips_a_silent_first_mic_but_keeps_a_working_second_mic(self):
        fake_sd = types.SimpleNamespace(
            query_devices=lambda: [
                {"name": "Silent microphone", "hostapi": 0,
                 "max_input_channels": 1, "max_output_channels": 0},
                {"name": "Working microphone", "hostapi": 0,
                 "max_input_channels": 1, "max_output_channels": 0},
            ],
            query_hostapis=lambda: [{"name": "Windows DirectSound"}],
        )
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            with patch("platform.system", return_value="Windows"):
                with patch.object(audio_devices, "_PREFERRED_APIS",
                                  {"Windows": ("directsound",)}):
                    with patch.object(audio_devices, "_usable", return_value=True):
                        with patch.object(audio_devices, "_transport_works",
                                          side_effect=[False, True]):
                            with patch.object(
                                audio_devices, "_display_name",
                                side_effect=lambda name, _devices: name,
                            ):
                                result = audio_devices._query()

        self.assertEqual(result["input"], ["Working microphone"])
        self.assertEqual(result["output"], [])
        self.assertEqual(audio_devices._chosen_api["input"], "directsound")
        self.assertIsNone(audio_devices._chosen_api["output"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
