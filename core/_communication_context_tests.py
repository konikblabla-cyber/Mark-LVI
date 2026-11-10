"""Offline tests for communication-app microphone privacy gating."""
import unittest

from core.communication_context import (
    is_communication_app,
    normalize_process_name,
    should_forward_microphone,
)


class CommunicationContextTests(unittest.TestCase):
    def test_process_names_are_normalized(self):
        self.assertEqual(
            normalize_process_name(r"C:\\Program Files\\Discord\\Discord.exe"),
            "discord",
        )
        self.assertEqual(normalize_process_name("MS-TEAMS.EXE"), "ms-teams")

    def test_known_chat_and_call_apps_are_detected(self):
        for name in (
            "Discord.exe", "DiscordPTB.exe", "Teams.exe", "ms-teams.exe",
            "Zoom.exe", "PhoneExperienceHost.exe", "WhatsApp.exe",
            "Telegram.exe", "Webex.exe",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_communication_app(name))

    def test_unknown_apps_are_not_misclassified(self):
        self.assertFalse(is_communication_app("notepad.exe"))
        self.assertFalse(is_communication_app("chrome.exe"))
        self.assertFalse(is_communication_app(""))

    def test_microphone_is_blocked_in_call_apps_without_explicit_address(self):
        self.assertFalse(should_forward_microphone(True, False, False))

    def test_wake_phrase_opens_one_request(self):
        self.assertTrue(should_forward_microphone(True, True, False))

    def test_push_to_talk_is_an_explicit_override(self):
        self.assertTrue(should_forward_microphone(True, False, True))

    def test_normal_desktop_behaviour_is_unchanged(self):
        self.assertTrue(should_forward_microphone(False, False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
