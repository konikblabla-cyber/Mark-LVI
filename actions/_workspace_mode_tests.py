"""Offline tests for safe Windows workspace profiles."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import workspace_mode


class WorkspaceModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "workspace_profiles.json"
        self.path_patch = patch.object(workspace_mode, "_store_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.os_patch = patch.object(workspace_mode.platform, "system", return_value="Windows")
        self.os_patch.start()
        self.addCleanup(self.os_patch.stop)

    def test_save_and_list_profiles(self):
        result = workspace_mode.run({
            "action": "save", "profile_name": "Work",
            "applications": ["Opera", "Visual Studio Code", "Discord"],
        })
        self.assertIn("Saved workspace", result)
        listing = workspace_mode.run({"action": "list"})
        self.assertIn("Work", listing)
        self.assertIn("Visual Studio Code", listing)

    def test_rejects_shell_syntax_and_command_arguments(self):
        result = workspace_mode.run({
            "action": "save", "profile_name": "Unsafe",
            "applications": ["notepad.exe & whoami"],
        })
        self.assertIn("invalid", result.lower())
        self.assertFalse(self.path.exists())

    def test_caps_profiles_and_applications(self):
        result = workspace_mode.run({
            "action": "save", "profile_name": "Too many",
            "applications": [f"App {i}" for i in range(9)],
        })
        self.assertIn("at most 8", result)

    def test_activate_uses_existing_open_app_action(self):
        workspace_mode.run({
            "action": "save", "profile_name": "Work",
            "applications": ["Opera", "Code"],
        })
        with patch("actions.open_app.open_app", side_effect=[
            "status=started app=Opera", "status=started app=Code"
        ]) as launch:
            result = workspace_mode.run({"action": "activate", "profile_name": "work"})
        self.assertEqual(launch.call_count, 2)
        self.assertIn("not proof", result)

    def test_delete_profile(self):
        workspace_mode.run({
            "action": "save", "profile_name": "Study", "applications": ["Notepad"]
        })
        result = workspace_mode.run({"action": "delete", "profile_name": "Study"})
        self.assertIn("Deleted", result)
        self.assertNotIn("Study", workspace_mode.run({"action": "list"}))

    def test_non_windows_is_explicitly_unsupported(self):
        with patch.object(workspace_mode.platform, "system", return_value="Linux"):
            result = workspace_mode.run({"action": "list"})
        self.assertIn("Windows only", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
