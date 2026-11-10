"""Offline tests for Windows reminder listing and cancellation."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import reminder


class ReminderManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scripts_patch = patch.object(reminder, "_scripts_dir", return_value=self.root)
        self.scripts_patch.start()
        self.reminder_id = "JARVISReminder_20261224_180000"
        self.script = self.root / f"{self.reminder_id}.py"
        self.script.write_text("print('reminder')", encoding="utf-8")
        self.entries = {
            self.reminder_id: {
                "date": "2026-12-24",
                "time": "18:00",
                "message": "Christmas reminder",
                "script": self.script.name,
            }
        }
        reminder._write_registry(self.entries)

    def tearDown(self):
        self.scripts_patch.stop()
        self.temp.cleanup()

    def test_list_shows_only_pending_reminders(self):
        result = reminder._list_reminders()
        self.assertIn(self.reminder_id, result)
        self.assertIn("Christmas reminder", result)

    def test_list_prunes_fired_reminder_after_its_script_self_deletes(self):
        self.script.unlink()
        result = reminder._list_reminders()
        self.assertIn("no pending", result.lower())
        self.assertEqual(reminder._read_registry(), {})

    def test_cancel_deletes_only_the_exact_registered_task_and_script(self):
        with patch.object(
            reminder.subprocess, "run",
            return_value=subprocess.CompletedProcess(["schtasks"], 0, "", ""),
        ) as run:
            result = reminder._cancel_windows_reminder(self.reminder_id)
        self.assertIn("Cancelled reminder", result)
        self.assertEqual(run.call_args.args[0], [
            "schtasks", "/Delete", "/TN", self.reminder_id, "/F",
        ])
        self.assertFalse(self.script.exists())
        self.assertEqual(reminder._read_registry(), {})

    def test_invalid_identifier_never_reaches_task_scheduler(self):
        with patch.object(reminder.subprocess, "run") as run:
            result = reminder._cancel_windows_reminder("JARVISReminder_../../task")
        self.assertIn("invalid", result.lower())
        self.assertFalse(run.called)
        self.assertTrue(self.script.exists())
        self.assertEqual(len(reminder._read_registry()), 1)

    def test_scheduler_failure_preserves_registry_and_script(self):
        with patch.object(
            reminder.subprocess, "run",
            return_value=subprocess.CompletedProcess(
                ["schtasks"], 1, "", "Access denied"
            ),
        ) as run:
            result = reminder._cancel_windows_reminder(self.reminder_id)
        self.assertIn("could not cancel", result.lower())
        self.assertTrue(self.script.exists())
        self.assertIn(self.reminder_id, reminder._read_registry())

    def test_cancel_action_is_exposed_by_the_tool(self):
        self.assertIn("cancel", reminder.TOOL["parameters"]["properties"]["action"]["description"])
        self.assertIn("reminder_id", reminder.TOOL["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
