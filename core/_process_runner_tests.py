"""Offline tests for bounded process execution."""
import sys
import tempfile
import unittest
from pathlib import Path

from core.process_runner import run_bounded_process


class ProcessRunnerTests(unittest.TestCase):
    def test_captures_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_bounded_process(
                [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
                cwd=Path(temp), timeout=5,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("out", result.stdout)
        self.assertIn("err", result.stderr)
        self.assertFalse(result.timed_out)

    def test_kills_process_when_output_limit_is_reached(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_bounded_process(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 5000000)"],
                cwd=Path(temp), timeout=10, output_limit=4096,
            )
        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 4096)
        self.assertIsNotNone(result.returncode)

    def test_kills_process_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_bounded_process(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=Path(temp), timeout=0.2,
            )
        self.assertTrue(result.timed_out)
        self.assertIsNotNone(result.returncode)

    def test_supports_explicit_shell_for_prevalidated_platform_commands(self):
        import os
        if os.name == "nt":
            self.skipTest("POSIX shell invocation test")
        with tempfile.TemporaryDirectory() as temp:
            result = run_bounded_process(
                "printf safe", cwd=Path(temp), timeout=5, shell=True
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "safe")

    def test_requires_valid_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                run_bounded_process([], cwd=Path(temp), timeout=1)
            with self.assertRaises(ValueError):
                run_bounded_process([sys.executable], cwd=Path(temp), timeout=0)


if __name__ == "__main__":
    unittest.main()
