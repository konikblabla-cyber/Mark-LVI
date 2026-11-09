"""Tests that outbound messages cannot start before HUD approval."""
import unittest
from unittest.mock import patch

from actions import send_message as action


class OutboundMessageConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.params = {
            "receiver": "Alex",
            "platform": "WhatsApp",
            "message_text": "I will arrive at 18:30.",
        }

    def test_message_is_parked_behind_hud_confirmation(self):
        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action.confirm, "request", return_value="[CONFIRMATION_PENDING]") as request, \
             patch.object(action.threading, "Thread") as thread:
            result = action.send_message(self.params)
        self.assertIn("[CONFIRMATION_PENDING]", result)
        request.assert_called_once()
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["key"], "send_message")
        self.assertIn("Alex", kwargs["title"])
        self.assertIn("WhatsApp", kwargs["detail"])
        self.assertIn("I will arrive at 18:30.", kwargs["detail"])
        thread.assert_not_called()

    def test_send_worker_starts_only_when_confirmation_callback_runs(self):
        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action.confirm, "request") as request, \
             patch.object(action.threading, "Thread") as thread:
            action.send_message(self.params)
            request.call_args.kwargs["run"]()
        thread.assert_called_once()
        kwargs = thread.call_args.kwargs
        self.assertIs(kwargs["target"], action._send_and_report)
        self.assertEqual(kwargs["args"], (None, "WhatsApp", "Alex", "I will arrive at 18:30."))
        self.assertTrue(kwargs["daemon"])

    def test_explicit_user_send_to_named_recipient_skips_second_confirmation(self):
        from core.autonomy_policy import new_turn_authorization
        scope = new_turn_authorization("Wyślij wiadomość do Alexa: będę o 18:30")
        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action, "_queue_send", return_value="queued") as queue, \
             patch.object(action.confirm, "request") as request:
            result = action.send_message(self.params, session_memory=scope)
        self.assertEqual(result, "queued")
        queue.assert_called_once_with(None, "WhatsApp", "Alex", "I will arrive at 18:30.")
        request.assert_not_called()

    def test_explicit_send_authorization_is_consumed_once(self):
        from core.autonomy_policy import new_turn_authorization
        scope = new_turn_authorization("Send Alex a message")
        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action, "_queue_send", return_value="queued"), \
             patch.object(action.confirm, "request", return_value="[CONFIRMATION_PENDING]") as request:
            action.send_message(self.params, session_memory=scope)
            result = action.send_message(self.params, session_memory=scope)
        self.assertIn("[CONFIRMATION_PENDING]", result)
        request.assert_called_once()

    def test_explicit_send_to_different_recipient_does_not_authorize(self):
        from core.autonomy_policy import new_turn_authorization
        scope = new_turn_authorization("Send a message to Alex")
        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action.confirm, "request", return_value="[CONFIRMATION_PENDING]") as request:
            result = action.send_message(
                {**self.params, "receiver": "Barbara"}, session_memory=scope
            )
        self.assertIn("[CONFIRMATION_PENDING]", result)
        request.assert_called_once()

    def test_missing_hud_confirmation_refuses_without_starting_worker(self):

        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action.confirm, "request",
                          return_value="I cannot confirm this right now; nothing was done."), \
             patch.object(action.threading, "Thread") as thread:
            result = action.send_message(self.params)
        self.assertIn("nothing was done", result)
        thread.assert_not_called()

    def test_missing_recipient_or_message_never_requests_confirmation(self):
        with patch.object(action, "_PYAUTOGUI", True), \
             patch.object(action.confirm, "request") as request:
            missing_recipient = action.send_message({"message_text": "hello"})
            missing_message = action.send_message({"receiver": "Alex"})
        self.assertIn("no recipient", missing_recipient)
        self.assertIn("no message text", missing_message)
        request.assert_not_called()

    def test_missing_desktop_dependency_fails_before_confirmation(self):
        with patch.object(action, "_PYAUTOGUI", False), \
             patch.object(action.confirm, "request") as request:
            result = action.send_message(self.params)
        self.assertIn("not installed", result)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
