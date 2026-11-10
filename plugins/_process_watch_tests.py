"""Offline tests for persistent Windows process watchers."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import process_watch


class ProcessWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(process_watch, "_PATH", Path(self.temp.name) / "watchers.json")
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_start_exit_watch_requires_process_to_be_running(self):
        with patch.object(process_watch, "_running_process_names", return_value=set()):
            result = process_watch.add_watcher("python.exe", "exit")
        self.assertIn("not running", result.lower())
        self.assertEqual(process_watch._read()["watchers"], [])

    def test_start_event_watch_triggers_once_when_process_appears(self):
        with patch.object(process_watch, "_running_process_names", return_value=set()):
            result = process_watch.add_watcher("python.exe", "start")
        self.assertIn("Watching python.exe", result)
        with patch.object(process_watch, "_running_process_names", return_value={"python"}):
            alerts = process_watch.check_all()
        self.assertEqual(len(alerts), 1)
        self.assertIn("has started", alerts[0]["message"])
        self.assertEqual(process_watch.check_all(), [])
        self.assertEqual(process_watch.acknowledge_alerts([alerts[0]["id"]]), 1)
        self.assertEqual(process_watch._read()["watchers"], [])

    def test_exit_event_is_persisted_until_acknowledged(self):
        with patch.object(process_watch, "_running_process_names", return_value={"python"}):
            result = process_watch.add_watcher("python.exe", "exit")
        self.assertIn("Watching python.exe", result)
        with patch.object(process_watch, "_running_process_names", return_value=set()):
            alerts = process_watch.check_all()
            alerts_again = process_watch.check_all()
        self.assertEqual(alerts, alerts_again)
        self.assertIn("has finished or closed", alerts[0]["message"])
        stored = process_watch._read()["watchers"][0]
        self.assertTrue(stored["pending"])
        self.assertEqual(process_watch.acknowledge_alerts([alerts[0]["id"]]), 1)
        self.assertEqual(process_watch._read()["watchers"], [])

    def test_process_name_cannot_be_a_path_or_command_line(self):
        self.assertEqual(process_watch._normalise_process_name(r"C:\Apps\python.exe"), "")
        self.assertEqual(process_watch._normalise_process_name("python.exe --malicious"), "")

    def test_duplicate_watch_is_not_added(self):
        with patch.object(process_watch, "_running_process_names", return_value={"python"}):
            process_watch.add_watcher("python.exe", "exit")
            result = process_watch.add_watcher("python", "exit")
        self.assertIn("Already watching", result)
        self.assertEqual(len(process_watch._read()["watchers"]), 1)

    def test_stop_by_id_or_name_removes_watcher(self):
        with patch.object(process_watch, "_running_process_names", return_value=set()):
            process_watch.add_watcher("python.exe", "start")
        watcher_id = process_watch._read()["watchers"][0]["id"]
        result = process_watch.stop_watcher(watcher_id=watcher_id)
        self.assertIn("Stopped 1", result)
        self.assertEqual(process_watch._read()["watchers"], [])

    def test_corrupt_store_is_not_overwritten(self):
        process_watch._PATH.write_text("{broken", encoding="utf-8")
        with patch.object(process_watch, "_running_process_names", return_value=set()):
            result = process_watch.add_watcher("python.exe", "start")
        self.assertIn("Could not start", result)
        self.assertEqual(process_watch._PATH.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main(verbosity=2)
