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
        match = type("Match", (), {"label": "LibreOffice Writer", "pid": 4242})()
        launcher = lambda _name: True
        before = {100}
        with patch.object(open_app, "_SYSTEM", "Linux"), \
             patch.object(open_app, "_LAUNCHERS", {"Linux": launcher}), \
             patch.object(open_app.appidentity, "wait_for_launch", return_value=match) as wait, \
             patch.object(open_app.appidentity, "focus", return_value=True), \
             patch.object(open_app.appidentity, "is_running", return_value=True), \
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



class WindowsLauncherTests(unittest.TestCase):
    def test_opera_known_install_path_is_resolved_for_per_user_install(self):
        expected = r"C:\Users\example\AppData\Local\Programs\Opera\opera.exe"
        with patch.object(open_app, "_SYSTEM", "Windows"), \
             patch.object(open_app.appidentity, "launcher_name", return_value="opera"), \
             patch.object(open_app.shutil, "which", return_value=None), \
             patch.dict(open_app.os.environ, {
                 "LOCALAPPDATA": r"C:\Users\example\AppData\Local",
                 "APPDATA": r"C:\Users\example\AppData\Roaming",
                 "PROGRAMFILES": r"C:\Program Files",
                 "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
             }, clear=False), \
             patch.object(open_app.os.path, "isfile", side_effect=lambda p: str(p) == expected):
            self.assertEqual(open_app._windows_executable_path("Opera"), expected)

    def test_windows_launch_uses_resolved_executable_without_a_shell(self):
        executable = r"C:\Program Files\Opera\opera.exe"
        with patch.object(open_app, "_windows_executable_path", return_value=executable), \
             patch.object(open_app, "_windows_start_menu_shortcut") as shortcut, \
             patch.object(open_app.subprocess, "Popen") as popen:
            self.assertTrue(open_app._launch_windows("Opera"))
        popen.assert_called_once_with(
            [executable],
            stdout=open_app.subprocess.DEVNULL,
            stderr=open_app.subprocess.DEVNULL,
            shell=False,
        )
        shortcut.assert_not_called()

    def test_ambiguous_start_menu_shortcuts_are_not_opened(self):
        with patch.object(open_app, "_windows_executable_path", return_value=None), \
             patch.object(open_app, "_windows_start_menu_shortcut", return_value=(None, True)), \
             patch.object(open_app, "_desktop_search") as search:
            self.assertFalse(open_app._launch_windows("Opera"))
        search.assert_not_called()

    def test_new_process_is_not_reported_open_until_focus_and_alive_are_verified(self):
        match = type("Match", (), {"label": "opera", "pid": 88})()
        with patch.object(open_app, "_SYSTEM", "Windows"), \
             patch.object(open_app, "_LAUNCHERS", {"Windows": lambda _name: True}), \
             patch.object(open_app.appidentity, "wait_for_launch", return_value=match), \
             patch.object(open_app.appidentity, "focus", return_value=True), \
             patch.object(open_app.appidentity, "is_running", return_value=True), \
             patch.object(open_app, "_log") as log, \
             patch.object(open_app, "_say") as say:
            open_app._launch_and_verify(None, "Opera", "opera", {1})
        self.assertTrue(any("open and in the foreground" in call.args[1] for call in log.call_args_list))
        say.assert_not_called()

    def test_process_without_foreground_window_is_reported_unverified(self):
        match = type("Match", (), {"label": "opera", "pid": 88})()
        with patch.object(open_app, "_SYSTEM", "Windows"), \
             patch.object(open_app, "_LAUNCHERS", {"Windows": lambda _name: True}), \
             patch.object(open_app.appidentity, "wait_for_launch", return_value=match), \
             patch.object(open_app.appidentity, "focus", return_value=False), \
             patch.object(open_app.appidentity, "is_running", return_value=True), \
             patch.object(open_app, "_log"), \
             patch.object(open_app, "_say") as say:
            open_app._launch_and_verify(None, "Opera", "opera", {1})
        say.assert_called_once()
        self.assertIn("could not verify its window", say.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
