"""Offline tests for the restricted Home Assistant light plugin."""
import unittest
from unittest.mock import patch

from plugins import smart_home


class FakeResponse:
    def __init__(self, status_code=200, state="on"):
        self.status_code = status_code
        self._state = state

    def json(self):
        return {"state": self._state}


class SmartHomeTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "base_url": "https://home.example.test",
            "access_token": "secret-token",
            "lights_json": '{"desk":"light.desk","ceiling":"light.living_room"}',
        }
        self.cfg = patch.object(smart_home, "get_plugin_config", return_value=self.config)
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()

    def test_list_shows_only_explicitly_mapped_lights(self):
        result = smart_home.run({"action": "list"})
        self.assertIn("desk (light.desk)", result)
        self.assertIn("ceiling (light.living_room)", result)
        self.assertNotIn("secret-token", result)

    def test_turn_on_posts_only_to_light_service_and_verifies_state(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse()) as post, \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(state="on")) as get:
            result = smart_home.run({
                "action": "set", "device": "desk", "state": "on", "brightness": 120,
            })
        self.assertEqual(result, "desk is now on. Brightness: 120/255.")
        self.assertEqual(post.call_args.args[0], "https://home.example.test/api/services/light/turn_on")
        self.assertEqual(post.call_args.kwargs["json"], {
            "entity_id": "light.desk", "brightness": 120,
        })
        self.assertEqual(get.call_args.args[0], "https://home.example.test/api/states/light.desk")
        self.assertIn("Bearer secret-token", post.call_args.kwargs["headers"]["Authorization"])

    def test_unknown_alias_and_non_light_entities_are_rejected(self):
        with patch.object(smart_home.requests, "post") as post:
            unknown = smart_home.run({"action": "set", "device": "lock", "state": "on"})
        self.assertIn("not configured", unknown)
        self.assertFalse(post.called)

        self.config["lights_json"] = '{"front_door":"lock.front_door"}'
        result = smart_home.run({"action": "list"})
        self.assertIn("light.* entity ID", result)

    def test_invalid_brightness_is_rejected_before_network_access(self):
        with patch.object(smart_home.requests, "post") as post:
            result = smart_home.run({
                "action": "set", "device": "desk", "state": "on", "brightness": 300,
            })
        self.assertIn("1 to 255", result)
        self.assertFalse(post.called)

    def test_off_state_with_brightness_is_rejected(self):
        with patch.object(smart_home.requests, "post") as post:
            result = smart_home.run({
                "action": "set", "device": "desk", "state": "off", "brightness": 100,
            })
        self.assertIn("only be set while turning", result)
        self.assertFalse(post.called)

    def test_http_broker_url_is_rejected_to_protect_access_token(self):
        self.config["base_url"] = "http://home.example.test"
        with patch.object(smart_home.requests, "post") as post:
            result = smart_home.run({"action": "set", "device": "desk", "state": "on"})
        self.assertIn("valid HTTPS", result)
        self.assertFalse(post.called)

    def test_accepted_command_without_matching_state_is_not_reported_as_done(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse()) as post, \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(state="off")):
            result = smart_home.run({"action": "set", "device": "desk", "state": "on"})
        self.assertIn("but the reported state is 'off'", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
