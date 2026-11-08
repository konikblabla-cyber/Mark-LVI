"""Offline tests for local desktop activity sensing.

Run with: python -m core._desktop_context_tests
No Windows APIs, model calls, network access or credentials are required.
"""
import unittest

from core.desktop_context import (
    _clean_process_name,
    _idle_seconds_from_ticks,
    desktop_is_idle,
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

    def test_missing_windows_sensor_preserves_other_platform_behaviour(self):
        self.assertIsNone(desktop_is_idle({"available": False}))
        self.assertFalse(desktop_is_idle({
            "available": True, "idle_seconds": None,
        }))


if __name__ == "__main__":
    unittest.main(verbosity=2)
