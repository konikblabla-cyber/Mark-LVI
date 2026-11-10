"""Offline tests for safe external-editor launching."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions.dev_agent import _classify_error, _has_error, _open_vscode


class DevAgentLaunchSafetyTests(unittest.TestCase):
    def test_nonzero_exit_is_not_reported_as_success(self):
        output = "Process exited with code 7."
        self.assertTrue(_has_error(output, "python main.py"))
        self.assertEqual(_classify_error(output), "runtime_error")

    def test_timeout_is_not_reported_as_success(self):
        output = "Timed out after 30s — process was stopped."
        self.assertTrue(_has_error(output, "python main.py"))
        self.assertEqual(_classify_error(output), "runtime_error")

    def test_output_limit_is_not_reported_as_success(self):
        output = "Output truncated at the 1 MB safety limit; process stopped."
        self.assertTrue(_has_error(output, "python main.py"))

    def test_empty_successful_output_remains_valid(self):
        self.assertFalse(_has_error("", "python main.py"))

    def test_vscode_launch_does_not_use_a_shell_for_user_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "Code.exe"
            executable.write_text("mock executable", encoding="utf-8")
            project = Path(temp) / "workspace & echo INJECTED"
            with patch("actions.dev_agent.shutil.which", return_value=str(executable)), \
                 patch("actions.dev_agent.subprocess.Popen") as popen, \
                 patch("actions.dev_agent.time.sleep"):
                self.assertTrue(_open_vscode(project))

            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(executable), str(project)])
            self.assertFalse(kwargs["shell"])

    def test_ignores_cmd_shims_and_does_not_fall_back_to_a_shell(self):
        with patch("actions.dev_agent.shutil.which", return_value=r"C:\VSCode\code.cmd"), \
             patch("actions.dev_agent.subprocess.Popen") as popen:
            self.assertFalse(_open_vscode(Path("workspace")))
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
