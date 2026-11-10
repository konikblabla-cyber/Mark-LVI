"""Offline tests for local desktop activity sensing.

Run with: python -m core._desktop_context_tests
No Windows APIs, model calls, network access or credentials are required.
"""
import unittest

from core.desktop_context import (
    _clean_process_name,
    _idle_seconds_from_ticks,
    desktop_is_idle,
    should_allow_proactive_interrupt,
    should_run_proactive_checkin,
    _is_fullscreen_rect,
    _is_fullscreen_monitor_rect,
    _rect_within_primary_display,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
