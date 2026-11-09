"""Least-prompt autonomy and one-turn authorization tests."""
import unittest

from core.autonomy_policy import (
    consume_explicit_send_authorization,
    new_turn_authorization,
    requires_checkbox_confirmation,
    requires_ui_confirmation,
)


class AutonomyPolicyTests(unittest.TestCase):
    def test_routine_and_reversible_controls_never_prompt(self):
        for target in ("Save", "Created", "Confirm", "Closed", "Remove filter",
                       "Open settings", "Expand folder", "Remember this device"):
            with self.subTest(target=target):
                self.assertFalse(requires_ui_confirmation(target))

    def test_unrequested_external_actions_prompt(self):
        for target in ("Send", "Submit", "Publish", "Share", "Upload", "Delete"):
            with self.subTest(target=target):
                self.assertTrue(requires_ui_confirmation(target))

    def test_explicit_targeted_user_request_authorizes_one_bounded_external_action(self):
        self.assertFalse(requires_ui_confirmation(
            "Wyślij", user_request="Wyślij wiadomość do Alexa"
        ))
        self.assertFalse(requires_ui_confirmation(
            "Publish", user_request="Publish the release notes"
        ))
        self.assertFalse(requires_ui_confirmation(
            "Delete", user_request="Delete this selected file"
        ))

    def test_authorization_does_not_spread_to_another_target(self):
        self.assertTrue(requires_ui_confirmation(
            "Publish", user_request="Send a message to Alex"
        ))
        self.assertTrue(requires_ui_confirmation(
            "Send", user_request="Open the settings page"
        ))

    def test_critical_financial_and_permanent_actions_always_prompt(self):
        for target, request in (
            ("Pay now", "Pay now"),
            ("Purchase", "Purchase this item"),
            ("Transfer funds", "Transfer funds"),
            ("Delete account", "Delete account"),
            ("Permanently erase", "Permanently erase these files"),
            ("Factory reset", "Factory reset this PC"),
            ("Zamknij konto", "Zamknij konto"),
        ):
            with self.subTest(target=target):
                self.assertTrue(requires_ui_confirmation(target, user_request=request))

    def test_direct_send_authorization_is_recipient_scoped_and_one_shot(self):
        scope = new_turn_authorization("Wyślij wiadomość do Alex: będę o 18:30")
        self.assertTrue(consume_explicit_send_authorization(scope, "Alex", "będę o 18:30"))
        self.assertFalse(consume_explicit_send_authorization(scope, "Alex", "będę o 18:30"))
        self.assertFalse(consume_explicit_send_authorization(scope, "Barbara", "będę o 18:30"))

    def test_turn_authorization_expires(self):
        from unittest.mock import patch
        with patch("core.autonomy_policy.time.monotonic", side_effect=[100.0, 191.0]):
            scope = new_turn_authorization("Send a message to Alex")
            self.assertEqual(scope["user_request"], "Send a message to Alex")
            self.assertFalse(consume_explicit_send_authorization(scope, "Alex", "Send a message to Alex"))

    def test_send_authorization_rejects_model_invented_message_content(self):
        scope = new_turn_authorization("Send a message to Alex: I will arrive at 18:30.")
        self.assertFalse(consume_explicit_send_authorization(
            scope, "Alex", "I will be there later."
        ))

    def test_send_authorization_rejects_negated_or_unspecified_requests(self):
        for request in (
            "Nie wysyłaj wiadomości do Alexa",
            "Do not send a message to Alex",
            "Open WhatsApp for Alex",
            "Send a message to someone",
            "Do not click Delete",
            "Nie klikaj Wyślij",
        ):
            with self.subTest(request=request):
                scope = new_turn_authorization(request)
                self.assertFalse(consume_explicit_send_authorization(scope, "Alex"))

    def test_opt_out_is_always_allowed_without_confirmation(self):
        for label in ("Allow tracking", "Share location", "Accept cookies",
                      "Allow notifications"):
            with self.subTest(label=label):
                self.assertFalse(requires_checkbox_confirmation(label, False))

    def test_unrequested_opt_in_prompts_but_requested_setting_change_does_not(self):
        self.assertTrue(requires_checkbox_confirmation("Allow tracking", True))
        self.assertFalse(requires_checkbox_confirmation(
            "Lokalizacja", True,
            new_turn_authorization("Włącz lokalizację"),
        ))
        self.assertFalse(requires_checkbox_confirmation(
            "Accept all cookies", True,
            new_turn_authorization("Akceptuj cookies"),
        ))
        self.assertFalse(requires_checkbox_confirmation("Reject analytics", True))
        self.assertFalse(requires_checkbox_confirmation("Do not track", True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
