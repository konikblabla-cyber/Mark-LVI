"""Offline tests for Windows system health reporting and alerts."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from actions import system_monitor


class SystemHealthTests(unittest.TestCase):
    def test_status_includes_storage_battery_network_and_top_processes(self):
        memory = SimpleNamespace(percent=62.0, used=6 * 1024**3, total=16 * 1024**3)
        with patch.object(system_monitor.psutil, "cpu_percent", return_value=21.5), \
             patch.object(system_monitor.psutil, "virtual_memory", return_value=memory), \
             patch.object(system_monitor.psutil, "boot_time", return_value=100.0), \
             patch.object(system_monitor.psutil, "pids", return_value=[1, 2, 3]), \
             patch.object(system_monitor.time, "time", return_value=3700.0), \
             patch.object(system_monitor, "_get_cpu_temp", return_value=54.0), \
             patch.object(system_monitor, "_get_gpu_usage", return_value=33.0), \
             patch.object(system_monitor, "_system_disk_usage", return_value={
                 "total_gb": 500.0, "free_gb": 120.0, "free_percent": 24.0,
             }), \
             patch.object(system_monitor, "_battery_info", return_value={
                 "percent": 76.0, "plugged": True,
             }), \
             patch.object(system_monitor, "_network_adapters_up", return_value=2), \
             patch.object(system_monitor, "_top_memory_processes", return_value=[
                 {"name": "Code.exe", "memory_mb": 1200.0},
             ]):
            status = system_monitor.get_system_status()

        self.assertEqual(status["cpu_percent"], 21.5)
        self.assertEqual(status["disk"]["free_percent"], 24.0)
        self.assertEqual(status["battery"]["percent"], 76.0)
        self.assertEqual(status["network_adapters_up"], 2)
        self.assertEqual(status["top_memory_processes"][0]["name"], "Code.exe")
        self.assertEqual(status["process_count"], 3)

    def test_low_disk_space_triggers_non_destructive_alert(self):
        monitor = system_monitor.SystemMonitor()
        with patch.object(system_monitor.psutil, "cpu_percent", return_value=5.0), \
             patch.object(system_monitor.psutil, "virtual_memory", return_value=SimpleNamespace(percent=40.0)), \
             patch.object(system_monitor, "_get_cpu_temp", return_value=-1.0), \
             patch.object(system_monitor, "_get_gpu_usage", return_value=-1.0), \
             patch.object(system_monitor, "_system_disk_usage", return_value={
                 "free_percent": 4.0, "free_gb": 8.0,
             }), \
             patch.object(system_monitor, "_battery_info", return_value=None), \
             patch.object(system_monitor.time, "monotonic", return_value=1000.0):
            alert = monitor.check()
        self.assertIn("only 4.0% free", alert)
        self.assertIn("do not delete anything automatically", alert)

    def test_low_unplugged_battery_triggers_alert(self):
        monitor = system_monitor.SystemMonitor()
        with patch.object(system_monitor.psutil, "cpu_percent", return_value=5.0), \
             patch.object(system_monitor.psutil, "virtual_memory", return_value=SimpleNamespace(percent=40.0)), \
             patch.object(system_monitor, "_get_cpu_temp", return_value=-1.0), \
             patch.object(system_monitor, "_get_gpu_usage", return_value=-1.0), \
             patch.object(system_monitor, "_system_disk_usage", return_value=None), \
             patch.object(system_monitor, "_battery_info", return_value={
                 "percent": 9.0, "plugged": False,
             }), \
             patch.object(system_monitor.time, "monotonic", return_value=1000.0):
            alert = monitor.check()
        self.assertIn("battery is low (9%)", alert)

    def test_plugged_in_battery_does_not_trigger_low_battery_alert(self):
        monitor = system_monitor.SystemMonitor()
        with patch.object(system_monitor.psutil, "cpu_percent", return_value=5.0), \
             patch.object(system_monitor.psutil, "virtual_memory", return_value=SimpleNamespace(percent=40.0)), \
             patch.object(system_monitor, "_get_cpu_temp", return_value=-1.0), \
             patch.object(system_monitor, "_get_gpu_usage", return_value=-1.0), \
             patch.object(system_monitor, "_system_disk_usage", return_value=None), \
             patch.object(system_monitor, "_battery_info", return_value={
                 "percent": 5.0, "plugged": True,
             }), \
             patch.object(system_monitor.time, "monotonic", return_value=1000.0):
            alert = monitor.check()
        self.assertIsNone(alert)

    def test_diagnose_computer_reports_health_without_changing_state(self):
        snapshot = {
            "cpu_percent": 92.0, "ram_percent": 65.0,
            "ram_used_gb": 10.4, "ram_total_gb": 16.0,
            "cpu_temp_c": 55.0, "gpu_percent": 42.0,
            "disk": {"total_gb": 500.0, "free_gb": 80.0, "free_percent": 16.0},
            "battery": {"percent": 70.0, "plugged": True},
            "uptime": "3h 20m", "process_count": 120,
            "top_memory_processes": [{"name": "Code.exe", "memory_mb": 1400.0}],
        }
        with patch.object(system_monitor, "_OS", "Windows"), \
             patch.object(system_monitor, "get_system_status", return_value=snapshot):
            report = system_monitor.diagnose_computer({})
        self.assertIn("CPU 92%", report)
        self.assertIn("CPU is heavily loaded", report)
        self.assertIn("Code.exe", report)
        self.assertIn("read-only", report)
        self.assertIn("no process was stopped", report)

    def test_diagnose_computer_is_windows_only(self):
        with patch.object(system_monitor, "_OS", "Linux"):
            report = system_monitor.diagnose_computer({})
        self.assertIn("Windows only", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
