"""Offline tests for the optional, bounded wake-word installer."""
import subprocess
import unittest
from unittest.mock import patch

from core import wake_word


class WakeWordInstallerTests(unittest.TestCase):
    def test_pip_install_timeout_is_reported_without_hanging(self):
        timeout = subprocess.TimeoutExpired(["python", "-m", "pip"], 300)
        with patch.object(wake_word, "is_installed", return_value=False), \
             patch.object(wake_word.subprocess, "run", side_effect=timeout) as run:
            ok, message = wake_word.install_and_download(logger=lambda _msg: None)
        self.assertFalse(ok)
        self.assertIn("setup error", message)
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_model_download_timeout_is_reported_without_hanging(self):
        timeout = subprocess.TimeoutExpired(["python", "-c", "download"], 300)
        with patch.object(wake_word, "is_installed", return_value=True), \
             patch.object(wake_word.subprocess, "run", side_effect=timeout) as run:
            ok, message = wake_word.install_and_download(logger=lambda _msg: None)
        self.assertFalse(ok)
        self.assertIn("model download timed out", message)
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_model_download_process_failure_is_reported(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="model host unavailable")
        with patch.object(wake_word, "is_installed", return_value=True), \
             patch.object(wake_word.subprocess, "run", return_value=failed) as run:
            ok, message = wake_word.install_and_download(logger=lambda _msg: None)
        self.assertFalse(ok)
        self.assertIn("model download failed: model host unavailable", message)
        self.assertIn("download_models", run.call_args.args[0][-1])

    def test_model_download_success_is_verified_by_local_model_files(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(wake_word, "is_installed", return_value=True), \
             patch.object(wake_word.subprocess, "run", return_value=completed) as run, \
             patch.object(wake_word, "is_ready", return_value=True):
            ok, message = wake_word.install_and_download(logger=lambda _msg: None)
        self.assertTrue(ok)
        self.assertEqual(message, "Wake word installed and ready.")
        self.assertEqual(run.call_args.kwargs["timeout"], 300)
    def test_pip_failure_does_not_attempt_model_download(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="network failed")
        with patch.object(wake_word, "is_installed", return_value=False), \
             patch.object(wake_word.subprocess, "run", return_value=failed) as run:
            ok, message = wake_word.install_and_download(logger=lambda _msg: None)
        self.assertFalse(ok)
        self.assertIn("pip install failed", message)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
