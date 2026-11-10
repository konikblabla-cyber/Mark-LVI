"""Offline tests for safe cross-platform application launching."""
import unittest
from unittest.mock import patch

from actions import open_app


class LinuxLauncherTests(unittest.TestCase):
    def test_linux_alias_with_arguments_uses_resolved_executable_and_argv(self):
        def which(name):
            return "/usr/bin/libreoffice" if name == "libreoffice" else None
        with patch.object(open_app.shutil, "which", side_effect=which), \
             patch.object(open_app.subprocess, "Popen") as popen:
            self.assertTrue(open_app._launch_linux("libreoffice --writer"))
        popen.assert_called_once_with(
            ["/usr/bin/libreoffice", "--writer"],
            stdout=open_app.subprocess.DEVNULL,
            stderr=open_app.subprocess.DEVNULL,
        )

    def test_linux_command_alias_uses_its_executable_for_process_verification(self):
        with patch.object(open_app, "_SYSTEM", "Linux"), \
             patch.object(open_app.shutil, "which", return_value="/usr/bin/libreoffice"):
            name = open_app._verification_name("word", "libreoffice --writer")
        self.assertEqual(name, "libreoffice")

    def test_linux_office_alias_detects_existing_shared_libreoffice_process(self):
        existing = type("Match", (), {"label": "LibreOffice Writer"})()
        with patch.object(open_app, "_SYSTEM", "Linux"), \
             patch.object(open_app.appidentity, "launcher_name", return_value="libreoffice --writer"), \
             patch.object(open_app.shutil, "which", return_value="/usr/bin/libreoffice"), \
             patch.object(open_app.appidentity, "running", side_effect=[[], [existing]]) as running, \
             patch.object(open_app.appidentity, "focus", return_value=True):
            result = open_app.open_app({"app_name": "word"})
        self.assertIn("already_running=true", result)
        self.assertEqual(running.call_args_list[0].args, ("word",))
        self.assertEqual(running.call_args_list[1].args, ("libreoffice",))

    def test_linux_office_launch_verifies_by_executable_identity(self):
        match = type("Match", (), {"label": "LibreOffice Writer"})()
        launcher = lambda _name: True
        before = {100}
        with patch.object(open_app, "_SYSTEM", "Linux"), \
             patch.object(open_app, "_LAUNCHERS", {"Linux": launcher}), \
             patch.object(open_app.appidentity, "wait_for_launch", return_value=match) as wait, \
             patch.object(open_app, "_log"):
            open_app._launch_and_verify(None, "word", "libreoffice --writer", before, "libreoffice")
        wait.assert_called_once_with("libreoffice", before, timeout=open_app._LAUNCH_TIMEOUT)
    def test_linux_alias_arguments_are_never_shell_interpreted(self):
        def which(name):
            return "/usr/bin/libreoffice" if name == "libreoffice" else None
        with patch.object(open_app.shutil, "which", side_effect=which), \
             patch.object(open_app.subprocess, "Popen") as popen:
            self.assertTrue(open_app._launch_linux("libreoffice --writer; touch marker"))
        args = popen.call_args.args[0]
        self.assertEqual(args, ["/usr/bin/libreoffice", "--writer;", "touch", "marker"])
        self.assertIsInstance(args, list)
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_invalid_quoted_launcher_is_not_executed(self):
        with patch.object(open_app.shutil, "which", return_value="/usr/bin/libreoffice"):
            self.assertIsNone(open_app._linux_launcher_command('libreoffice "unterminated'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
