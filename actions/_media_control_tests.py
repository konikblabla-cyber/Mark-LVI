"""Offline tests for Windows global media transport actions."""
import unittest
from unittest.mock import Mock

from actions.media_control import perform_media_action


class MediaControlTests(unittest.TestCase):
    def test_play_pause_sends_the_combined_windows_media_key(self):
        sender = Mock()
        result = perform_media_action("play_pause", platform_name="Windows", send_key=sender)
        self.assertIn("play/pause", result)
        sender.assert_called_once_with(0xB3)

    def test_next_previous_and_stop_have_distinct_keys(self):
        for action, expected in (("next", 0xB0), ("previous", 0xB1), ("stop", 0xB2)):
            with self.subTest(action=action):
                sender = Mock()
                result = perform_media_action(action, platform_name="Windows", send_key=sender)
                self.assertIn("Sent", result)
                sender.assert_called_once_with(expected)

    def test_unknown_action_is_rejected_without_sending_a_key(self):
        sender = Mock()
        result = perform_media_action("delete_playlist", platform_name="Windows", send_key=sender)
        self.assertIn("Unsupported", result)
        sender.assert_not_called()

    def test_non_windows_without_test_sender_is_refused(self):
        result = perform_media_action("next", platform_name="Linux")
        self.assertIn("Windows only", result)

    def test_sender_errors_are_reported_without_crashing(self):
        def fail(_vk):
            raise OSError("no media key service")
        result = perform_media_action("next", platform_name="Windows", send_key=fail)
        self.assertIn("could not send", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
