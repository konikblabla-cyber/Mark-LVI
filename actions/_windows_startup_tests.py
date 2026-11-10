"""Offline tests for opt-in Windows logon startup."""
import subprocess
import unittest
from unittest.mock import patch

from actions import windows_startup


class WindowsStartupTests(unittest.TestCase):
    def test_enable_registers_current_user_limited_task_and_verifies_it(self):
        with patch.object(windows_startup.platform, "system", return_value="Windows"), \
             patch.object(windows_startup, "_launch_command", return_value='"pythonw.exe" "C:\\JARVIS\\main.py"'), \
             patch.object(windows_startup, "_run_schtasks", side_effect=[
                 subprocess.CompletedProcess(["schtasks"], 0, "SUCCESS", ""),
                 subprocess.CompletedProcess(["schtasks"], 0, "TaskName: JARVIS", ""),
             ]) as run:
            result = windows_startup.run({"action": "enable"})
        self.assertIn("will start when", result)
        self.assertEqual(run.call_count, 2)
        args = run.call_args_list[0].args[0]
        self.assertIn("/SC", args)
        self.assertIn("ONLOGON", args)
        self.assertIn("/RL", args)
        self.assertIn("LIMITED", args)
        self.assertEqual(args[args.index("/TR") + 1], '"pythonw.exe" "C:\\JARVIS\\main.py"')

    def test_enable_reports_task_verification_failure(self):
        with patch.object(windows_startup.platform, "system", return_value="Windows"), \
             patch.object(windows_startup, "_launch_command", return_value='"pythonw.exe" "C:\\JARVIS\\main.py"'), \
             patch.object(windows_startup, "_run_schtasks", side_effect=[
                 subprocess.CompletedProcess(["schtasks"], 0, "SUCCESS", ""),
                 subprocess.CompletedProcess(["schtasks"], 1, "", "not found"),
             ]):
            result = windows_startup.run({"action": "enable"})
        self.assertIn("could not verify", result.lower())

    def test_disable_removes_only_the_named_jarvis_task(self):
        with patch.object(windows_startup.platform, "system", return_value="Windows"), \
             patch.object(windows_startup, "_run_schtasks", return_value=subprocess.CompletedProcess(
                 ["schtasks"], 0, "SUCCESS", ""
             )) as run:
            result = windows_startup.run({"action": "disable"})
        self.assertIn("will no longer start", result)
        self.assertEqual(run.call_args.args[0], [
            "/Delete", "/TN", windows_startup._TASK_NAME, "/F",
        ])

    def test_status_reports_registered_or_missing_task(self):
        with patch.object(windows_startup.platform, "system", return_value="Windows"), \
             patch.object(windows_startup, "_run_schtasks", side_effect=[
                 subprocess.CompletedProcess(["schtasks"], 0, "TaskName: JARVIS", ""),
                 subprocess.CompletedProcess(["schtasks"], 1, "", "ERROR: Cannot find"),
             ]):
            self.assertIn("registered", windows_startup.run({"action": "status"}))
            self.assertIn("not registered", windows_startup.run({"action": "status"}))

    def test_action_registers_its_handler(self):
        self.assertTrue(callable(windows_startup.TOOL.get("handler")))

    def test_non_windows_is_explicitly_unsupported(self):
        with patch.object(windows_startup.platform, "system", return_value="Linux"):
            result = windows_startup.run({"action": "enable"})
        self.assertIn("Windows only", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
