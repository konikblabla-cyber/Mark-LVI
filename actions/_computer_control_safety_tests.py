"""Offline regression tests for desktop-control safety boundaries."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        with patch.object(control.platform, "system", return_value="Windows"), \
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
        with patch.object(control.platform, "system", return_value="Windows"), \
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
    def test_smart_type_does_not_clear_active_content_by_default(self):
        with patch.object(control, "_input_window_guard", return_value=""), \
             patch.object(control, "_smart_type", return_value="typed") as execute, \
             patch.object(control.confirm, "request") as confirm_request:
            result = control.computer_control({
                "action": "smart_type", "text": "new text", "target_app": "notepad.exe",
            })
        self.assertEqual(result, "typed")
        execute.assert_called_once_with("new text", clear_first=False)
        confirm_request.assert_not_called()

    def test_smart_type_replace_requires_confirmation_and_rechecks_target(self):
        request = {}
        blocked = "status=failed action=computer_control reason=foreground changed"
        with patch.object(control, "_input_window_guard", side_effect=["", blocked]) as guard, \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_smart_type") as execute:
            result = control.computer_control({
                "action": "smart_type", "text": "replace", "clear_first": True,
                "target_app": "notepad.exe",
            })
            self.assertIn("[CONFIRMATION_PENDING]", result)
            confirmed_result = request["run"]()
        self.assertEqual(confirmed_result, blocked)
        self.assertEqual(guard.call_args_list[0].args, ("notepad.exe",))
        self.assertEqual(guard.call_args_list[1].args, ("notepad.exe",))
        execute.assert_not_called()

    def test_clear_field_requires_confirmation_and_rechecks_target(self):
        request = {}
        blocked = "status=failed action=computer_control reason=foreground changed"
        with patch.object(control, "_input_window_guard", side_effect=["", blocked]) as guard, \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_clear_field") as execute:
            result = control.computer_control({
                "action": "clear_field", "target_app": "notepad.exe",
            })
            self.assertIn("[CONFIRMATION_PENDING]", result)
            confirmed_result = request["run"]()
        self.assertEqual(confirmed_result, blocked)
        self.assertEqual(guard.call_args_list[0].args, ("notepad.exe",))
        self.assertEqual(guard.call_args_list[1].args, ("notepad.exe",))
        execute.assert_not_called()
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
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_type") as execute:
            result = control.computer_control({"action": "type", "text": "unsafe"})
        self.assertIn("target_app is required", result)
        execute.assert_not_called()

    def test_foreground_guard_uses_real_host_os_not_config_override(self):
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_get_os", return_value="mac"), \
             patch("core.desktop_context.get_desktop_context",
                   return_value={"available": True, "foreground_app": "chrome.exe"}), \
             patch.object(control.appidentity, "own_exes", return_value=set()), \
             patch.object(control, "_type") as execute:
            result = control.computer_control({
                "action": "type", "text": "unsafe", "target_app": "notepad.exe",
            })
        self.assertIn("does not match", result)
        execute.assert_not_called()
    def test_input_refuses_when_foreground_does_not_match_target(self):
        with patch.object(control.platform, "system", return_value="Windows"), \
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
        with patch.object(control.platform, "system", return_value="Windows"), \
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



    def test_verified_visual_click_pins_window_and_checks_exact_postcondition(self):
        before = {
            "handle": 7, "process_id": 55, "bounds": (10, 20, 90, 70), "records": [],
        }
        after = {
            "handle": 7, "process_id": 55, "bounds": (10, 20, 90, 70), "records": [],
        }
        desktop = type("Screen", (), {"size": lambda self: (100, 80)})()
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", return_value="") as guard, \
             patch.object(control, "_screen_find", return_value=(20, 30)) as locate, \
             patch.object(control, "_click", return_value="Clicked (20, 30)") as click, \
             patch.object(control, "pyautogui", desktop), \
             patch("actions.ui_interaction._snapshot", side_effect=[before, after]) as snapshot, \
             patch("actions.ui_interaction._has_exact_expected_text", return_value=False), \
             patch("actions.ui_interaction._wait_for_expected_result", return_value=True):
            result = control._screen_click_verified_execute(
                "gear icon", "Settings", "settings.exe", 7, 55,
            )
        self.assertIn("status=done action=computer_control", result)
        self.assertIn("verified=true", result)
        self.assertEqual(guard.call_count, 3)
        locate.assert_called_once_with(
            "gear icon", "settings.exe", (10, 20, 90, 70),
        )
        snapshot.assert_any_call()
        snapshot.assert_any_call(7, 55)
        click.assert_called_once_with(x=20, y=30)

    def test_verified_visual_click_never_clicks_if_expected_result_is_already_visible(self):
        before = {"handle": 7, "process_id": 55, "bounds": (10, 20, 90, 70), "records": []}
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", return_value=""), \
             patch("actions.ui_interaction._snapshot", return_value=before), \
             patch("actions.ui_interaction._has_exact_expected_text", return_value=True), \
             patch.object(control, "_screen_find") as locate, \
             patch.object(control, "_click") as click:
            result = control._screen_click_verified_execute(
                "gear icon", "Settings", "settings.exe",
            )
        self.assertIn("status=unverified", result)
        locate.assert_not_called()
        click.assert_not_called()

    def test_verified_visual_click_rechecks_foreground_after_slow_image_analysis(self):
        before = {"handle": 7, "process_id": 55, "bounds": (10, 20, 90, 70), "records": []}
        blocked = "status=failed action=computer_control reason=foreground_changed"
        desktop = type("Screen", (), {"size": lambda self: (100, 80)})()
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", side_effect=["", blocked]) as guard, \
             patch.object(control, "_screen_find", return_value=(20, 30)), \
             patch.object(control, "pyautogui", desktop), \
             patch("actions.ui_interaction._snapshot", return_value=before), \
             patch("actions.ui_interaction._has_exact_expected_text", return_value=False), \
             patch.object(control, "_click") as click:
            result = control._screen_click_verified_execute(
                "gear icon", "Settings", "settings.exe",
            )
        self.assertEqual(result, blocked)
        self.assertEqual(guard.call_count, 2)
        click.assert_not_called()

    def test_unverified_visual_click_is_not_retried(self):
        before = {"handle": 7, "process_id": 55, "bounds": (10, 20, 90, 70), "records": []}
        after = {"handle": 7, "process_id": 55, "bounds": (10, 20, 90, 70), "records": []}
        desktop = type("Screen", (), {"size": lambda self: (100, 80)})()
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", return_value=""), \
             patch.object(control, "_screen_find", return_value=(20, 30)) as locate, \
             patch.object(control, "_click", return_value="Clicked (20, 30)") as click, \
             patch.object(control, "pyautogui", desktop), \
             patch("actions.ui_interaction._snapshot", side_effect=[before, after]), \
             patch("actions.ui_interaction._has_exact_expected_text", return_value=False), \
             patch("actions.ui_interaction._wait_for_expected_result", return_value=False):
            result = control._screen_click_verified_execute(
                "gear icon", "Settings", "settings.exe",
            )
        self.assertIn("status=unverified", result)
        self.assertIn("Do not retry automatically", result)
        locate.assert_called_once()
        click.assert_called_once()

    def test_critical_visual_click_waits_for_real_hud_confirmation(self):
        request = {}
        with patch.object(control, "_input_window_guard", return_value=""), \
             patch.object(control.confirm, "request",
                          side_effect=lambda **kwargs: request.update(kwargs) or "[CONFIRMATION_PENDING]"), \
             patch.object(control, "_screen_click_verified_execute") as execute:
            result = control.computer_control({
                "action": "screen_click_verified",
                "description": "Delete account button",
                "expected_result": "Account deleted",
                "target_app": "settings.exe",
            })
        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertIn("Delete account", request["detail"])
        execute.assert_not_called()



    def test_verified_visual_click_refuses_sensitive_targets_before_capture(self):
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", return_value=""), \
             patch("actions.ui_interaction._snapshot") as snapshot, \
             patch.object(control, "_screen_find") as locate, \
             patch.object(control, "_click") as click:
            result = control._screen_click_verified_execute(
                "Password field", "Password settings", "settings.exe",
            )
        self.assertIn("status=failed", result)
        self.assertIn("credentials", result)
        snapshot.assert_not_called()
        locate.assert_not_called()
        click.assert_not_called()


    def test_visible_window_region_is_clipped_to_primary_screen(self):
        self.assertEqual(
            control._visible_window_region((10, 20, 110, 120), (100, 80)),
            (10, 20, 90, 60),
        )
        self.assertEqual(
            control._visible_window_region((-20, -5, 60, 40), (100, 80)),
            (0, 0, 60, 40),
        )
        self.assertIsNone(
            control._visible_window_region((110, 0, 200, 70), (100, 80)),
        )
        self.assertIsNone(
            control._visible_window_region((20, 20, 20, 60), (100, 80)),
        )


    def test_verified_visual_click_fails_closed_when_window_bounds_are_missing(self):
        before = {"handle": 7, "process_id": 55, "records": []}
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", return_value=""), \
             patch("actions.ui_interaction._snapshot", return_value=before), \
             patch.object(control, "_screen_find") as locate, \
             patch.object(control, "_click") as click:
            result = control._screen_click_verified_execute(
                "gear icon", "Settings", "settings.exe",
            )
        self.assertIn("status=failed", result)
        self.assertIn("no full-desktop screenshot was sent", result)
        locate.assert_not_called()
        click.assert_not_called()


    def test_screen_find_crops_target_window_and_restores_desktop_coordinates(self):
        class FakeImage:
            def save(self, stream, format=None):
                stream.write(b"window-crop")

        desktop = SimpleNamespace(
            size=lambda: (100, 80),
            screenshot=Mock(return_value=FakeImage()),
        )
        fake_google = types.ModuleType("google")
        fake_google.__path__ = []
        fake_genai = types.ModuleType("google.genai")
        fake_genai.types = SimpleNamespace(
            Part=SimpleNamespace(from_bytes=lambda **kwargs: ("image", kwargs["data"])),
        )
        fake_google.genai = fake_genai
        modules = {
            "google": fake_google,
            "google.genai": fake_genai,
        }
        with patch.object(control, "_get_api_key", return_value="test-key"), \
             patch.object(control, "_require_pyautogui"), \
             patch.object(control, "pyautogui", desktop), \
             patch.dict(sys.modules, modules), \
             patch(
                 "core.gemini.call",
                 return_value=SimpleNamespace(text="20,30"),
             ) as gemini_call:
            result = control._screen_find(
                "gear icon", "settings.exe", (10, 20, 90, 70),
            )
        self.assertEqual(result, (30, 50))
        desktop.screenshot.assert_called_once_with(region=(10, 20, 80, 50))
        prompt = gemini_call.call_args.args[0][1]
        self.assertIn("80×50 pixels", prompt)
        self.assertIn("crop of the target application window", prompt)

    def test_screen_find_does_not_capture_full_desktop_when_target_is_offscreen(self):
        desktop = SimpleNamespace(
            size=lambda: (100, 80),
            screenshot=Mock(),
        )
        with patch.object(control, "_get_api_key", return_value="test-key"), \
             patch.object(control, "_require_pyautogui"), \
             patch.object(control, "pyautogui", desktop), \
             patch.object(control, "_safe_print"):
            result = control._screen_find(
                "gear icon", "settings.exe", (120, 20, 220, 70),
            )
        self.assertIsNone(result)
        desktop.screenshot.assert_not_called()


    def test_visual_click_refuses_when_planned_window_handle_or_process_changed(self):
        before = {
            "handle": 8, "process_id": 55, "bounds": (10, 20, 90, 70), "records": [],
        }
        with patch.object(control.platform, "system", return_value="Windows"), \
             patch.object(control, "_input_window_guard", return_value=""), \
             patch("actions.ui_interaction._snapshot", return_value=before), \
             patch.object(control, "_screen_find") as locate, \
             patch.object(control, "_click") as click:
            result = control._screen_click_verified_execute(
                "gear icon", "Settings", "settings.exe", 7, 55,
            )
        self.assertIn("status=failed", result)
        self.assertIn("exact window selected by the mission planner", result)
        locate.assert_not_called()
        click.assert_not_called()


    def test_visual_click_dispatch_forwards_exact_planned_window_identity(self):
        parameters = {
            "action": "screen_click_verified",
            "description": "gear icon",
            "expected_result": "Privacy panel",
            "target_app": "settings.exe",
            "target_window_handle": 7,
            "target_process_id": 55,
        }
        with patch.object(control, "_input_window_guard", return_value=""), \
             patch.object(control, "_screen_click_verified_execute",
                          return_value="status=done action=computer_control verified=true") as execute:
            result = control.computer_control(parameters)
        self.assertIn("status=done", result)
        execute.assert_called_once_with(
            "gear icon", "Privacy panel", "settings.exe", 7, 55,
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
