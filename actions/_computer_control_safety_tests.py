"""Offline regression tests for desktop-control safety boundaries."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from actions import computer_control as control


class _FakeDesktop:
    def __init__(self, size=(100, 80)):
        self._size = size
        self.clicks = []
        self.moves = []
        self.drags = []

    def size(self):
        return self._size

    def click(self, *args, **kwargs):
        self.clicks.append((args, kwargs))

    def moveTo(self, *args, **kwargs):
        self.moves.append((args, kwargs))

    def dragTo(self, *args, **kwargs):
        self.drags.append((args, kwargs))


class ComputerControlSafetyTests(unittest.TestCase):
    def test_focus_window_never_interpolates_user_text_into_shell(self):
        match = SimpleNamespace(pid=42, name="editor.exe", score=100,
                                is_self=False, titles=["Editor"], label="editor")
        hostile = 'Editor"); Start-Process calc; #'
        with patch.object(control.appidentity, "running", return_value=[match]) as running, \
             patch.object(control.appidentity, "focus", return_value=True) as focus, \
             patch.object(control.subprocess, "run") as subprocess_run:
            result = control._focus_window(hostile)
        running.assert_called_once_with(hostile)
        focus.assert_called_once_with(match)
        subprocess_run.assert_not_called()
        self.assertIn("status=done", result)
        self.assertIn("action=computer_control", result)

    def test_focus_window_asks_when_top_matches_are_ambiguous(self):
        first = SimpleNamespace(pid=10, name="editor.exe", score=65,
                                is_self=False, titles=["one"], label="editor")
        second = SimpleNamespace(pid=11, name="editor.exe", score=65,
                                 is_self=False, titles=["two"], label="editor")
        with patch.object(control.appidentity, "running", return_value=[first, second]), \
             patch.object(control.appidentity, "focus") as focus:
            result = control._focus_window("editor")
        self.assertIn("status=ask", result)
        focus.assert_not_called()

    def test_focus_window_refuses_empty_or_missing_targets(self):
        with patch.object(control.appidentity, "running", return_value=[]) as running:
            result = control._focus_window("")
        self.assertIn("status=failed", result)
        running.assert_not_called()
        with patch.object(control.appidentity, "running", return_value=[]):
            result = control._focus_window("missing-app")
        self.assertIn("status=failed", result)

    def test_click_requires_explicit_in_bounds_coordinates(self):
        desktop = _FakeDesktop()
        with patch.object(control, "_PYAUTOGUI", True), \
             patch.object(control, "pyautogui", desktop):
            result = control._click()
        self.assertIn("status=failed", result)
        self.assertEqual(desktop.clicks, [])

    def test_click_rejects_out_of_bounds_and_invalid_buttons(self):
        desktop = _FakeDesktop()
        with patch.object(control, "_PYAUTOGUI", True), \
             patch.object(control, "pyautogui", desktop):
            out = control._click(100, 20)
            button = control._click(10, 20, button="launch")
        self.assertIn("status=failed", out)
        self.assertIn("status=failed", button)
        self.assertEqual(desktop.clicks, [])

    def test_click_accepts_only_an_in_bounds_target(self):
        desktop = _FakeDesktop()
        with patch.object(control, "_PYAUTOGUI", True), \
             patch.object(control, "pyautogui", desktop):
            result = control._click(99, 79)
        self.assertIn("Clicked (99, 79)", result)
        self.assertEqual(len(desktop.clicks), 1)

    def test_disruptive_hotkey_waits_for_hud_confirmation(self):
        request = {}
        with patch.object(control, "_input_window_guard", return_value=""), \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_hotkey") as execute:
            result = control.computer_control({"action": "hotkey", "keys": "alt+f4"})
            self.assertIn("[CONFIRMATION_PENDING]", result)
            execute.assert_not_called()
            request["run"]()
            execute.assert_called_once_with("alt", "f4")

    def test_shift_delete_and_run_dialog_wait_for_confirmation(self):
        for shortcut in ("shift+delete", "win+r"):
            with self.subTest(shortcut=shortcut):
                request = {}
                with patch.object(control, "_input_window_guard", return_value=""), \
                     patch.object(control.confirm, "request",
                                  side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
                     patch.object(control, "_hotkey") as execute:
                    result = control.computer_control({
                        "action": "hotkey", "keys": shortcut,
                    })
                    self.assertIn("[CONFIRMATION_PENDING]", result)
                    execute.assert_not_called()
                    request["run"]()
                    execute.assert_called_once()

    def test_screen_click_rechecks_target_after_slow_screen_analysis(self):
        blocked = "status=failed action=computer_control reason=foreground changed"
        with patch.object(control, "_get_os", return_value="windows"), \
             patch.object(control, "_input_window_guard", side_effect=["", blocked]) as guard, \
             patch.object(control, "_screen_find", return_value=(20, 30)), \
             patch.object(control, "_click") as click:
            result = control.computer_control({
                "action": "screen_click", "description": "Save button",
                "target_app": "notepad.exe",
            })
        self.assertEqual(result, blocked)
        self.assertEqual(guard.call_count, 2)
        click.assert_not_called()

    def test_confirmed_hotkey_rechecks_foreground_before_execution(self):
        request = {}
        blocked = "status=failed action=computer_control reason=foreground changed"
        with patch.object(control, "_input_window_guard", side_effect=["", blocked]) as guard, \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_hotkey") as execute:
            result = control.computer_control({"action": "hotkey", "keys": "alt+f4"})
            self.assertIn("[CONFIRMATION_PENDING]", result)
            confirmed_result = request["run"]()
        self.assertEqual(confirmed_result, blocked)
        self.assertEqual(guard.call_count, 2)
        execute.assert_not_called()

    def test_input_refuses_to_type_into_jarvis_foreground_window(self):
        app = Path(sys.executable).name
        with patch.object(control, "_get_os", return_value="windows"), \
             patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": app}), \
             patch.object(control.appidentity, "own_exes",
                          return_value={sys.executable}), \
             patch.object(control, "_type") as execute:
            result = control.computer_control({"action": "type", "text": "unsafe", "target_app": app})
        self.assertIn("status=failed", result)
        self.assertIn("JARVIS is the active window", result)
        execute.assert_not_called()

    def test_input_fails_closed_when_foreground_process_is_unknown(self):
        with patch.object(control, "_get_os", return_value="windows"), \
             patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": ""}), \
             patch.object(control, "_type") as execute:
            result = control.computer_control({"action": "type", "text": "unsafe", "target_app": "notepad.exe"})
        self.assertIn("status=failed", result)
        self.assertIn("no input was sent", result)
        execute.assert_not_called()

    def test_console_print_sanitizes_unencodable_unicode(self):
        class Console:
            encoding = "cp1252"

            def __init__(self):
                self.value = ""

            def write(self, value):
                self.value += value

        console = Console()
        with patch.object(control.sys, "stdout", console):
            control._safe_print("direction ▶")
        self.assertIn(r"\u25b6", console.value)

    def test_delete_key_waits_for_hud_confirmation(self):
        request = {}
        with patch.object(control, "_input_window_guard", return_value=""), \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_press") as execute:
            result = control.computer_control({"action": "press", "key": "delete"})
            self.assertIn("[CONFIRMATION_PENDING]", result)
            execute.assert_not_called()
            request["run"]()
            execute.assert_called_once_with("delete")

    def test_smart_type_falls_back_to_keyboard_when_clipboard_backend_fails(self):
        gui = type("Gui", (), {"typewrite": Mock()})()
        clipboard = type("Clipboard", (), {"copy": Mock(side_effect=RuntimeError("no backend"))})()
        with patch.object(control, "_require_pyautogui"), \
             patch.object(control, "_require_pyperclip", return_value=True), \
             patch.object(control, "pyautogui", gui), \
             patch.object(control, "pyperclip", clipboard), \
             patch.object(control.time, "sleep"):
            result = control._smart_type("a long piece of text", clear_first=False)
        self.assertIn("Smart-typed:", result)
        gui.typewrite.assert_called_once_with("a long piece of text", interval=0.04)

    def test_clipboard_read_never_claims_success_without_a_backend(self):
        with patch.object(control, "_require_pyperclip", return_value=False):
            result = control._clipboard_get()
        self.assertIn("status=failed action=computer_control", result)
        self.assertIn("clipboard reading is unavailable", result)
        self.assertNotIn("copied", result.lower())

    def test_clipboard_paste_falls_back_to_typed_text_when_copy_fails(self):
        gui = type("Gui", (), {"typewrite": Mock()})()
        clipboard = type("Clipboard", (), {"copy": Mock(side_effect=RuntimeError("no backend"))})()
        with patch.object(control, "_require_pyautogui"), \
             patch.object(control, "_require_pyperclip", return_value=True), \
             patch.object(control, "pyautogui", gui), \
             patch.object(control, "pyperclip", clipboard), \
             patch.object(control.time, "sleep"), \
             patch.object(control, "_get_os", return_value="windows"):
            result = control._clipboard_paste("hello clipboard")
        self.assertIn("Typed instead of clipboard paste", result)
        gui.typewrite.assert_called_once_with("hello clipboard", interval=0.04)
    def test_foreground_app_reports_only_the_active_process_name(self):
        with patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": "notepad.exe"}):
            result = control.computer_control({"action": "foreground_app"})
        self.assertIn("status=done action=computer_control", result)
        self.assertIn("foreground_app=notepad.exe", result)
        self.assertIn("verified=true", result)

    def test_foreground_app_fails_when_process_identity_is_unavailable(self):
        with patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": ""}):
            result = control.computer_control({"action": "foreground_app"})
        self.assertIn("status=failed action=computer_control", result)
        self.assertIn("could not be identified", result)
    def test_input_requires_an_explicit_target_application(self):
        with patch.object(control, "_get_os", return_value="windows"), \
             patch.object(control, "_type") as execute:
            result = control.computer_control({"action": "type", "text": "unsafe"})
        self.assertIn("target_app is required", result)
        execute.assert_not_called()

    def test_input_refuses_when_foreground_does_not_match_target(self):
        with patch.object(control, "_get_os", return_value="windows"), \
             patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": "chrome.exe"}), \
             patch.object(control.appidentity, "own_exes", return_value=set()), \
             patch.object(control, "_type") as execute:
            result = control.computer_control({
                "action": "type", "text": "unsafe", "target_app": "notepad.exe",
            })
        self.assertIn("does not match", result)
        execute.assert_not_called()

    def test_input_runs_only_when_exact_target_is_foreground(self):
        with patch.object(control, "_get_os", return_value="windows"), \
             patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": "notepad.exe"}), \
             patch.object(control.appidentity, "own_exes", return_value=set()), \
             patch.object(control, "_type", return_value="typed") as execute:
            result = control.computer_control({
                "action": "type", "text": "safe", "target_app": "Notepad",
            })
        self.assertEqual(result, "typed")
        execute.assert_called_once_with("safe")

    def test_confirmed_hotkey_rechecks_the_same_target_application(self):
        request = {}
        blocked = "status=failed action=computer_control reason=foreground changed"
        with patch.object(control, "_input_window_guard", side_effect=["", blocked]) as guard, \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_hotkey") as execute:
            result = control.computer_control({
                "action": "hotkey", "keys": "alt+f4", "target_app": "notepad.exe",
            })
            self.assertIn("[CONFIRMATION_PENDING]", result)
            confirmed_result = request["run"]()
        self.assertEqual(confirmed_result, blocked)
        self.assertEqual(guard.call_args_list[0].args, ("notepad.exe",))
        self.assertEqual(guard.call_args_list[1].args, ("notepad.exe",))
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
