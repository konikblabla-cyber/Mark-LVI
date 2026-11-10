"""Offline tests for allowlisted Home Assistant device control."""
import unittest
from unittest.mock import patch

from plugins import smart_home


class FakeResponse:
    def __init__(self, status_code=200, state="on", attributes=None):
        self.status_code = status_code
        self._state = state
        self._attributes = attributes or {}

    def json(self):
        return {"state": self._state, "attributes": self._attributes}


class SmartHomeTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "base_url": "https://home.example.test",
            "access_token": "secret-token",
            "entities_json": (
                '{"desk":"light.desk","ceiling":"light.living_room",'
                '"plug":"switch.pc_plug","fan":"fan.office","climate":"climate.office"}'
            ),
        }
        self.cfg = patch.object(smart_home, "get_plugin_config", return_value=self.config)
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()

    def test_list_shows_only_explicitly_mapped_supported_entities(self):
        result = smart_home.run({"action": "list"})
        self.assertIn("desk (light.desk)", result)
        self.assertIn("plug (switch.pc_plug)", result)
        self.assertIn("fan (fan.office)", result)
        self.assertIn("climate (climate.office)", result)
        self.assertNotIn("secret-token", result)

    def test_turn_on_light_posts_and_verifies_brightness(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse()) as post, \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(
                 attributes={"brightness": 120},
             )) as get:
            result = smart_home.run({
                "action": "set", "device": "desk", "state": "on", "brightness": 120,
            })
        self.assertEqual(result, "desk is now on. Brightness: 120/255.")
        self.assertEqual(post.call_args.args[0], "https://home.example.test/api/services/light/turn_on")
        self.assertEqual(post.call_args.kwargs["json"], {
            "entity_id": "light.desk", "brightness": 120,
        })
        self.assertEqual(get.call_args.args[0], "https://home.example.test/api/states/light.desk")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertIn("Bearer secret-token", post.call_args.kwargs["headers"]["Authorization"])

    def test_switch_turn_on_uses_only_switch_service(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse()) as post, \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(state="on")):
            result = smart_home.run({"action": "set", "device": "plug", "state": "on"})
        self.assertEqual(result, "plug is now on.")
        self.assertEqual(post.call_args.args[0], "https://home.example.test/api/services/switch/turn_on")
        self.assertEqual(post.call_args.kwargs["json"], {"entity_id": "switch.pc_plug"})

    def test_fan_percentage_is_verified(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse()) as post, \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(
                 attributes={"percentage": 40},
             )):
            result = smart_home.run({
                "action": "set", "device": "fan", "state": "on", "percentage": 40,
            })
        self.assertEqual(result, "fan is now on. Fan speed: 40%.")
        self.assertEqual(post.call_args.args[0], "https://home.example.test/api/services/fan/set_percentage")
        self.assertEqual(post.call_args.kwargs["json"], {
            "entity_id": "fan.office", "percentage": 40,
        })

    def test_climate_target_is_set_and_verified_without_changing_hvac_mode(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse(state="heat")) as post, \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(
                 state="heat", attributes={"temperature": 22.5},
             )):
            result = smart_home.run({
                "action": "set", "device": "climate", "temperature": 22.5,
            })
        self.assertEqual(result, "climate target temperature is now 22.5°C.")
        self.assertEqual(post.call_args.args[0], "https://home.example.test/api/services/climate/set_temperature")
        self.assertEqual(post.call_args.kwargs["json"], {
            "entity_id": "climate.office", "temperature": 22.5,
        })

    def test_unknown_alias_and_non_allowed_entity_domain_are_rejected(self):
        with patch.object(smart_home.requests, "post") as post:
            unknown = smart_home.run({"action": "set", "device": "lock", "state": "on"})
        self.assertIn("not configured", unknown)
        self.assertFalse(post.called)

        self.config["entities_json"] = '{"front_door":"lock.front_door"}'
        result = smart_home.run({"action": "list"})
        self.assertIn("entity ID", result)

    def test_invalid_brightness_percentage_and_temperature_are_rejected(self):
        cases = [
            {"device": "desk", "state": "on", "brightness": 300},
            {"device": "fan", "state": "on", "percentage": 101},
            {"device": "climate", "temperature": 40},
            {"device": "plug", "state": "on", "temperature": 20},
        ]
        for args in cases:
            with self.subTest(args=args), patch.object(smart_home.requests, "post") as post:
                result = smart_home.run({"action": "set", **args})
                self.assertTrue(result)
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

    def test_climate_target_mismatch_is_not_reported_as_done(self):
        with patch.object(smart_home.requests, "post", return_value=FakeResponse(state="heat")), \
             patch.object(smart_home.requests, "get", return_value=FakeResponse(
                 state="heat", attributes={"temperature": 18.0},
             )):
            result = smart_home.run({"action": "set", "device": "climate", "temperature": 22})
        self.assertIn("target temperature does not match", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
