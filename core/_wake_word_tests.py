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
