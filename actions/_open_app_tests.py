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
