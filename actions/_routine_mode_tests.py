"""Offline tests for JARVIS routines."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import routine_mode


class RoutineModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(routine_mode, "_path", return_value=Path(self.temp.name) / "routines.json")
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_save_and_list_routine_with_apps_and_home_devices(self):
        result = routine_mode.run({
            "action": "save", "routine_name": "Cinema",
            "applications": ["Opera", "VLC"],
            "home_actions": [
                {"device": "lights", "state": "on", "brightness": 100},
                {"device": "climate", "temperature": 22},
            ],
        })
        self.assertIn("Saved routine", result)
        listing = routine_mode.run({"action": "list"})
        self.assertIn("Cinema: 2 app(s), 2 smart-home action(s)", listing)
        stored = json.loads(routine_mode._path().read_text(encoding="utf-8"))
        self.assertEqual(stored["routines"]["cinema"]["applications"], ["Opera", "VLC"])
        self.assertEqual(stored["routines"]["cinema"]["home_actions"][1]["temperature"], 22.0)

    def test_rejects_shell_commands_and_unsafe_device_actions(self):
        result = routine_mode.run({
            "action": "save", "routine_name": "Unsafe",
            "applications": ["cmd.exe & whoami"],
        })
        self.assertIn("plain application names", result)
        result = routine_mode.run({
            "action": "save", "routine_name": "Bad device",
            "home_actions": [{"device": "../lock", "state": "on"}],
        })
        self.assertIn("smart-home action is invalid", result)

    def test_activation_uses_verified_app_launcher_and_only_enabled_allowlisted_home_actions(self):
        routine_mode.run({
            "action": "save", "routine_name": "Work",
            "applications": ["Code"],
            "home_actions": [{"device": "desk_light", "state": "on"}],
        })
        with patch("actions.open_app.open_app", return_value="status=started app=Code") as launch, \
             patch("memory.config_manager.get_plugin_enabled", return_value=True), \
             patch("plugins.smart_home._settings", return_value=(
                 "https://home.test", "token", {"desk_light": "light.desk"},
             )), \
             patch("plugins.smart_home.run", return_value="desk_light is now on.") as home:
            result = routine_mode.run({"action": "activate", "routine_name": "work"})
        launch.assert_called_once_with({"app_name": "Code"}, player=None)
        home.assert_called_once_with({"action": "set", "device": "desk_light", "state": "on"}, player=None)
        self.assertIn("status=started", result)
        self.assertIn("desk_light is now on", result)

    def test_home_action_is_skipped_when_plugin_disabled(self):
        routine_mode.run({
            "action": "save", "routine_name": "Work",
            "home_actions": [{"device": "desk_light", "state": "on"}],
        })
        with patch("memory.config_manager.get_plugin_enabled", return_value=False), \
             patch("plugins.smart_home.run") as home:
            result = routine_mode.run({"action": "activate", "routine_name": "Work"})
        self.assertIn("plugin is disabled", result)
        self.assertFalse(home.called)

    def test_only_allowed_aliases_are_sent_to_home_assistant(self):
        routine_mode.run({
            "action": "save", "routine_name": "Cinema",
            "home_actions": [{"device": "desk_light", "state": "on"}],
        })
        with patch("memory.config_manager.get_plugin_enabled", return_value=True), \
             patch("plugins.smart_home._settings", return_value=(
                 "https://home.test", "token", {"living_room": "light.living_room"},
             )), \
             patch("plugins.smart_home.run") as home:
            result = routine_mode.run({"action": "activate", "routine_name": "Cinema"})
        self.assertIn("alias is not currently allowed", result)
        self.assertFalse(home.called)

    def test_delete_and_corrupt_store_protection(self):
        routine_mode.run({
            "action": "save", "routine_name": "Study", "applications": ["Code"],
        })
        self.assertIn("Deleted routine", routine_mode.run({"action": "delete", "routine_name": "Study"}))
        routine_mode._path().write_text("{broken", encoding="utf-8")
        result = routine_mode.run({"action": "save", "routine_name": "Bad", "applications": ["Code"]})
        self.assertIn("Could not read routine storage", result)
        self.assertEqual(routine_mode._path().read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main(verbosity=2)
