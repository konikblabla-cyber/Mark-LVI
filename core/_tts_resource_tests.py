"""Offline tests for local TTS device placement."""
import unittest

from core.tts_resource_policy import select_tts_device


class TTSDevicePolicyTests(unittest.TestCase):
    def test_default_is_cpu_even_when_cuda_exists(self):
        self.assertEqual(select_tts_device(None, True), "cpu")
        self.assertEqual(select_tts_device("", True), "cpu")

    def test_auto_uses_cuda_only_when_available(self):
        self.assertEqual(select_tts_device("auto", True), "cuda")
        self.assertEqual(select_tts_device("auto", False), "cpu")

    def test_explicit_cuda_falls_back_to_cpu_when_unavailable(self):
        self.assertEqual(select_tts_device("cuda", False), "cpu")
        self.assertEqual(select_tts_device("cuda", True), "cuda")

    def test_unknown_values_are_safe_cpu(self):
        self.assertEqual(select_tts_device("gpu-please", True), "cpu")


if __name__ == "__main__":
    unittest.main(verbosity=2)
