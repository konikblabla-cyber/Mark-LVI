"""Offline tests for safe external-editor launching."""
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from actions.dev_agent import _classify_error, _has_error, _open_vscode, _run_project
from actions.code_helper import _run_file
from core.code_checks import _walk, detect_stack


class DevAgentLaunchSafetyTests(unittest.TestCase):
    def test_project_scan_does_not_follow_symlinked_files_or_directories(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            external = Path(outside)
            (external / "secret.py").write_text("TOKEN = 'private'", encoding="utf-8")
            (root / "real.py").write_text("VALUE = 1", encoding="utf-8")
            try:
                (root / "linked.py").symlink_to(external / "secret.py")
                (root / "linked_dir").symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            found = {p.relative_to(root).as_posix() for p in _walk(root)}
            self.assertEqual(found, {"real.py"})

    def test_stack_detection_ignores_symlinked_project_metadata(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            package = Path(outside) / "package.json"
            package.write_text('{"dependencies":{"dangerous":"1.0"}}', encoding="utf-8")
            try:
                (root / "package.json").symlink_to(package)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this platform")
            stack = detect_stack(root)
            self.assertEqual(stack["frameworks"], [])

    def test_nonzero_exit_is_not_reported_as_success(self):
        output = "Process exited with code 7."
        self.assertTrue(_has_error(output, "python main.py"))
        self.assertEqual(_classify_error(output), "runtime_error")

    def test_timeout_is_not_reported_as_success(self):
        output = "Timed out after 30s — process was stopped."
        self.assertTrue(_has_error(output, "python main.py"))
        self.assertEqual(_classify_error(output), "execution_limit")

    def test_output_limit_is_not_reported_as_success_or_repaired_as_code(self):
        output = "Output truncated at the 1 MB safety limit; process stopped."
        self.assertTrue(_has_error(output, "python main.py"))
        self.assertEqual(_classify_error(output), "execution_limit")

    def test_timeout_is_classified_as_execution_limit(self):
        self.assertEqual(_classify_error("Timed out after 30s."), "execution_limit")

    def test_empty_successful_output_remains_valid(self):
        self.assertFalse(_has_error("", "python main.py"))

    def test_unknown_process_status_is_not_success(self):
        output = "Process status unknown; termination could not be confirmed."
        self.assertTrue(_has_error(output, "python main.py"))
        self.assertEqual(_classify_error(output), "runtime_error")

    def test_project_runner_does_not_claim_success_without_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            result = SimpleNamespace(stdout="", stderr="", returncode=None,
                                     timed_out=False, output_limited=False)
            with patch("actions.dev_agent.run_bounded_process", return_value=result), \
                 patch("builtins.print"):
                self.assertIn("status unknown", _run_project("python main.py", Path(temp)))

    def test_code_helper_does_not_claim_success_without_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "main.py"
            script.write_text("pass", encoding="utf-8")
            result = SimpleNamespace(stdout="", stderr="", returncode=None,
                                     timed_out=False, output_limited=False)
            with patch("actions.code_helper.run_bounded_process", return_value=result):
                self.assertIn("status unknown", _run_file(script, [], 5))

    def test_vscode_launch_does_not_use_a_shell_for_user_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = str(Path(temp) / "Code.exe")
            project = Path(temp) / "workspace & echo INJECTED"
            with patch("actions.dev_agent.shutil.which", return_value=executable), \
                 patch("actions.dev_agent.os.path.isfile", return_value=True), \
                 patch("actions.dev_agent.subprocess.Popen") as popen, \
                 patch("actions.dev_agent.time.sleep"):
                self.assertTrue(_open_vscode(project))

            args, kwargs = popen.call_args
            self.assertEqual(args[0], [executable, str(project)])
            self.assertFalse(kwargs["shell"])

    def test_ignores_cmd_shims_and_does_not_fall_back_to_a_shell(self):
        with patch("actions.dev_agent.shutil.which", return_value=r"C:\VSCode\code.cmd"), \
             patch("actions.dev_agent.subprocess.Popen") as popen:
            self.assertFalse(_open_vscode(Path("workspace")))
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
