"""Offline tests for communication-app microphone privacy gating."""
import unittest

from core.communication_context import (
    communication_apps_from_sessions,
    is_communication_app,
    is_communication_context,
    normalize_process_name,
    should_auto_wake,
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

    def test_only_active_audio_sessions_count_as_background_calls(self):
        class Process:
            def __init__(self, name):
                self._name = name
            def name(self):
                return self._name

        class Session:
            def __init__(self, state, name):
                self.State = state
                self.Process = Process(name)

        sessions = [
            Session(1, "Discord.exe"),
            Session(0, "Teams.exe"),
            Session(1, "spotify.exe"),
        ]
        self.assertEqual(
            communication_apps_from_sessions(sessions),
            {"discord"},
        )

    def test_expired_and_processless_sessions_are_ignored(self):
        class Session:
            State = 2
            Process = None

        self.assertEqual(communication_apps_from_sessions([Session()]), set())

    def test_open_chat_app_alone_does_not_count_as_background_call(self):
        self.assertFalse(is_communication_context("opera.exe", set()))

    def test_focused_chat_app_without_call_audio_does_not_block_commands(self):
        self.assertFalse(is_communication_context("Discord.exe", set()))

    def test_active_background_call_is_private_while_game_is_foreground(self):
        self.assertTrue(is_communication_context("game.exe", {"discord"}))

    def test_unknown_background_audio_does_not_trigger_privacy_gate(self):
        self.assertFalse(is_communication_context("game.exe", {"musicplayer"}))

    def test_audio_api_failure_falls_back_to_foreground_app(self):
        self.assertTrue(is_communication_context("Teams.exe", None))
        self.assertFalse(is_communication_context("opera.exe", None))

    def test_active_call_detection_does_not_need_chat_to_be_foreground(self):
        self.assertTrue(is_communication_context("opera.exe", {"teams"}))

    def test_fresh_desktop_input_auto_wakes_outside_calls(self):
        self.assertTrue(should_auto_wake(0.0, False))
        self.assertTrue(should_auto_wake(12.5, False))
        self.assertFalse(should_auto_wake(16.0, False))
        self.assertFalse(should_auto_wake(None, False))

    def test_desktop_activity_never_auto_wakes_inside_a_call(self):
        self.assertFalse(should_auto_wake(0.1, True))

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
