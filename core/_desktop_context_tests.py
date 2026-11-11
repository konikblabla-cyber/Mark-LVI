"""Offline tests for local desktop activity sensing.

Run with: python -m core._desktop_context_tests
No Windows APIs, model calls, network access or credentials are required.
"""
import ctypes
import unittest
from unittest.mock import Mock, patch

from core.desktop_context import (
    _clean_process_name,
    _idle_seconds_from_ticks,
    desktop_is_idle,
    should_allow_proactive_interrupt,
    should_run_proactive_checkin,
    _is_fullscreen_rect,
    _is_fullscreen_monitor_rect,
    _rect_within_primary_display,
    get_foreground_window_identity,
)


class DesktopContextTests(unittest.TestCase):
    def test_idle_time_uses_wrapping_dword_arithmetic(self):
        self.assertAlmostEqual(_idle_seconds_from_ticks(1000, 61000), 60.0)
        self.assertAlmostEqual(
            _idle_seconds_from_ticks(0xFFFFFF00, 0x000002E8), 1.0
        )

    def test_process_name_is_only_a_short_basename(self):
        self.assertEqual(
            _clean_process_name(r"C:\Games\ExampleGame.exe"),
            "ExampleGame.exe",
        )
        self.assertEqual(_clean_process_name(""), "")
        self.assertLessEqual(len(_clean_process_name("x" * 500)), 80)

    def test_active_desktop_blocks_proactive_check(self):
        self.assertFalse(desktop_is_idle({
            "available": True, "idle_seconds": 45.0,
        }))

    def test_idle_desktop_allows_proactive_check(self):
        self.assertTrue(desktop_is_idle({
            "available": True, "idle_seconds": 120.0,
        }))

    def test_fullscreen_window_suppresses_proactive_check(self):
        self.assertFalse(desktop_is_idle({
            "available": True, "idle_seconds": 600.0, "fullscreen": True,
        }))

    def test_proactive_interrupt_requires_positive_idle_confirmation(self):
        self.assertTrue(should_allow_proactive_interrupt({
            "available": True, "idle_seconds": 120.0,
            "fullscreen": False, "fullscreen_known": True,
        }))
        self.assertFalse(should_allow_proactive_interrupt({
            "available": True, "idle_seconds": 5.0,
            "fullscreen": False, "fullscreen_known": True,
        }))
        self.assertFalse(should_allow_proactive_interrupt({"available": False}))
        self.assertFalse(should_allow_proactive_interrupt({
            "available": True, "idle_seconds": 120.0,
            "fullscreen": False, "fullscreen_known": False,
        }))
        self.assertFalse(should_allow_proactive_interrupt({
            "available": True, "idle_seconds": None,
            "fullscreen": False, "fullscreen_known": True,
        }))
    def test_proactive_checkin_can_run_while_microphone_is_asleep(self):
        context = {
            "available": True, "idle_seconds": 1800.0,
            "fullscreen": False, "fullscreen_known": True,
        }
        self.assertTrue(should_run_proactive_checkin(True, False, False, context))

    def test_proactive_checkin_is_blocked_without_a_session_or_during_calls(self):
        context = {
            "available": True, "idle_seconds": 1800.0,
            "fullscreen": False, "fullscreen_known": True,
        }
        self.assertFalse(should_run_proactive_checkin(False, False, False, context))
        self.assertFalse(should_run_proactive_checkin(True, True, False, context))
        self.assertFalse(should_run_proactive_checkin(True, False, True, context))

    def test_proactive_checkin_still_requires_confirmed_idle_and_window_geometry(self):
        self.assertFalse(should_run_proactive_checkin(True, False, False, {
            "available": True, "idle_seconds": 1800.0,
            "fullscreen": False, "fullscreen_known": False,
        }))
        self.assertFalse(should_run_proactive_checkin(True, False, False, {
            "available": True, "idle_seconds": 1.0,
            "fullscreen": False, "fullscreen_known": True,
        }))
    def test_fullscreen_geometry_detects_borderless_fullscreen(self):
        self.assertTrue(_is_fullscreen_rect(0, 0, 1920, 1080, 1920, 1080))
        self.assertFalse(_is_fullscreen_rect(100, 100, 1200, 800, 1920, 1080))

    def test_fullscreen_geometry_uses_the_actual_monitor_origin(self):
        # A secondary monitor can have negative coordinates in Windows' virtual desktop.
        self.assertTrue(_is_fullscreen_monitor_rect(
            -1920, 0, 0, 1080, -1920, 0, 0, 1080,
        ))
        self.assertFalse(_is_fullscreen_monitor_rect(
            -1800, 100, -100, 900, -1920, 0, 0, 1080,
        ))

    def test_primary_display_fallback_never_guesses_for_secondary_monitors(self):
        self.assertTrue(_rect_within_primary_display(
            0, 0, 1920, 1080, 1920, 1080,
        ))
        self.assertFalse(_rect_within_primary_display(
            -1920, 0, 0, 1080, 1920, 1080,
        ))
        self.assertFalse(_rect_within_primary_display(
            1920, 0, 3840, 1080, 1920, 1080,
        ))

    def test_missing_windows_sensor_preserves_other_platform_behaviour(self):
        self.assertIsNone(desktop_is_idle({"available": False}))
        self.assertFalse(desktop_is_idle({
            "available": True, "idle_seconds": None,
        }))



    def test_foreground_identity_returns_exact_hwnd_and_process_id_without_window_text(self):
        fake_user32 = Mock()
        fake_user32.GetForegroundWindow.return_value = 1234

        def set_pid(hwnd, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = 55
            return 1

        fake_user32.GetWindowThreadProcessId.side_effect = set_pid
        with patch("core.desktop_context.os.name", "nt"), \
             patch("ctypes.WinDLL", return_value=fake_user32, create=True):
            identity = get_foreground_window_identity()
        self.assertEqual(identity, (1234, 55))
        fake_user32.GetForegroundWindow.assert_called_once_with()
        fake_user32.GetWindowThreadProcessId.assert_called_once()

    def test_foreground_identity_fails_closed_when_windows_returns_no_hwnd_or_pid(self):
        fake_user32 = Mock()
        fake_user32.GetForegroundWindow.return_value = None
        with patch("core.desktop_context.os.name", "nt"), \
             patch("ctypes.WinDLL", return_value=fake_user32, create=True):
            self.assertIsNone(get_foreground_window_identity())
        fake_user32.GetWindowThreadProcessId.assert_not_called()

if __name__ == "__main__":
    unittest.main(verbosity=2)
