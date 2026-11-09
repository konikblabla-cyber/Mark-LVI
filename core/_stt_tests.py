"""Offline tests for the Whisper GPU placement policy."""
import unittest

from core.stt import _select_whisper_device


class WhisperDevicePolicyTests(unittest.TestCase):
    def test_default_policy_keeps_cuda_free_for_games(self):
        self.assertEqual(_select_whisper_device(None, True), ("cpu", "int8"))
        self.assertEqual(_select_whisper_device("", True), ("cpu", "int8"))

    def test_auto_uses_cuda_only_when_available(self):
        self.assertEqual(_select_whisper_device("auto", True), ("cuda", "float16"))
        self.assertEqual(_select_whisper_device("auto", False), ("cpu", "int8"))

    def test_explicit_cuda_falls_back_without_cuda(self):
        self.assertEqual(_select_whisper_device("cuda", False), ("cpu", "int8"))
        self.assertEqual(_select_whisper_device("cuda", True), ("cuda", "float16"))

    def test_unknown_preference_is_safe_cpu(self):
        self.assertEqual(_select_whisper_device("unexpected", True), ("cpu", "int8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
