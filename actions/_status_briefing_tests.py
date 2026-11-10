"""Offline tests for the integrated local JARVIS status briefing."""
import unittest
from unittest.mock import patch

from actions import status_briefing


class StatusBriefingTests(unittest.TestCase):
    def test_windows_briefing_combines_existing_subsystems_read_only(self):
        with patch.object(status_briefing.platform, "system", return_value="Windows"), \
             patch("plugins.mission_control.run", return_value="Mission M-1: Build project"), \
             patch("actions.reminder._list_reminders", return_value="Pending reminders: test"), \
             patch("actions.background_monitor.list_monitors", return_value=["Windows updates"]), \
             patch("actions.workspace_mode._read_profiles", return_value={
                 "work": {"name": "Work", "applications": ["Code"]},
             }), \
             patch("plugins.process_watch.list_watchers", return_value="Watching python.exe"), \
             patch("actions.system_monitor.get_system_status", return_value={
                 "cpu_percent": 20.0, "ram_percent": 40.0,
                 "disk": {"free_gb": 100.0, "free_percent": 20.0},
                 "battery": {"percent": 78.0, "plugged": True},
                 "network_adapters_up": 2,
                 "top_memory_processes": [{"name": "Code.exe", "memory_mb": 1000}],
                 "gpu_percent": 35.0,
                 "uptime": "2h 10m", "cpu_temp_c": 50.0,
             }):
            report = status_briefing.build_status_briefing()
        self.assertIn("Mission M-1", report)
        self.assertIn("Pending reminders", report)
        self.assertIn("Windows updates", report)
        self.assertIn("Work", report)
        self.assertIn("Watching python.exe", report)
        self.assertIn("battery 78% charging", report)
        self.assertIn("2 network adapter(s) up", report)
        self.assertIn("Code.exe 1000 MB", report)
        self.assertIn("PC HEALTH", report)
        self.assertIn("read-only", report)

    def test_windows_briefing_flags_critical_health(self):
        with patch.object(status_briefing.platform, "system", return_value="Windows"), \
             patch("plugins.mission_control.run", return_value="No missions saved."), \
             patch("actions.reminder._list_reminders", return_value="No reminders"), \
             patch("actions.background_monitor.list_monitors", return_value=[]), \
             patch("actions.workspace_mode._read_profiles", return_value={}), \
             patch("actions.system_monitor.get_system_status", return_value={
                 "cpu_percent": 96.0, "ram_percent": 94.0,
                 "disk": {"free_gb": 2.0, "free_percent": 1.0},
                 "uptime": "1d", "cpu_temp_c": 90.0,
             }):
            report = status_briefing.build_status_briefing()
        self.assertIn("CPU high", report)
        self.assertIn("RAM high", report)
        self.assertIn("system drive low", report)
        self.assertIn("CPU temperature high", report)

    def test_non_windows_is_explicitly_unsupported(self):
        with patch.object(status_briefing.platform, "system", return_value="Linux"):
            report = status_briefing.build_status_briefing()
        self.assertIn("Windows only", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
