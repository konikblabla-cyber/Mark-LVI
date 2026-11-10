"""Offline tests for bounded process execution."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
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

    def test_child_process_does_not_receive_common_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env.update({
                "JARVIS_SAFE_TEST_VALUE": "preserved",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "openai-secret",
                "GITHUB_TOKEN": "github-secret",
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
                "DATABASE_URL": "postgres://user:password@example.invalid/db",
                "NPM_TOKEN": "npm-secret",
                "SSH_AUTH_SOCK": "private-agent-socket",
            })
            code = (
                "import json,os; keys=['JARVIS_SAFE_TEST_VALUE','GEMINI_API_KEY',"
                "'OPENAI_API_KEY','GITHUB_TOKEN','AWS_SECRET_ACCESS_KEY',"
                "'DATABASE_URL','NPM_TOKEN','SSH_AUTH_SOCK']; "
                "print(json.dumps({k:os.environ.get(k) for k in keys}))"
            )
            result = run_bounded_process(
                [sys.executable, "-c", code], cwd=Path(temp), timeout=5, env=env,
            )
        self.assertEqual(result.returncode, 0)
        values = json.loads(result.stdout)
        self.assertEqual(values["JARVIS_SAFE_TEST_VALUE"], "preserved")
        for key in (
            "GEMINI_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN",
            "AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "NPM_TOKEN", "SSH_AUTH_SOCK",
        ):
            with self.subTest(key=key):
                self.assertIsNone(values[key])

    def test_default_child_environment_also_filters_secrets(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"JARVIS_TEST_SECRET_TOKEN": "hidden", "JARVIS_TEST_SAFE": "visible"},
        ):
            code = (
                "import os; print(os.environ.get('JARVIS_TEST_SECRET_TOKEN','')); "
                "print(os.environ.get('JARVIS_TEST_SAFE',''))"
            )
            result = run_bounded_process(
                [sys.executable, "-c", code], cwd=Path(temp), timeout=5,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["", "visible"])


if __name__ == "__main__":
    unittest.main()
