"""Offline tests for sensitive-field blocking and browser confirmation rules."""
import unittest

from core.browser_safety import (
    is_sensitive_field_descriptor,
    requires_browser_confirmation,
)


class BrowserSafetyClassifierTests(unittest.TestCase):
    def test_sensitive_field_names_and_html_metadata_are_blocked(self):
        for value in (
            "password", "current-password", "input[name=pin_code]",
            "autocomplete=one-time-code", "Credit card number",
            "CVV", "IBAN", "security_code", "Hasło", "kod jednorazowy", "kod weryfikacyjny", "numer karty", "karta płatnicza",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_sensitive_field_descriptor(value))

    def test_ordinary_fields_are_not_blocked(self):
        for value in ("Search", "First name", "Email subject", "City", "Message"):
            with self.subTest(value=value):
                self.assertFalse(is_sensitive_field_descriptor(value))

    def test_irreversible_browser_targets_require_confirmation(self):
        for target in ("Send", "Submit order", "Pay now", "Delete account",
                       "Publish post", "Cancel subscription", "Transfer funds", "Wyślij", "Wyslij", "Kup teraz", "Zapłać", "Usuń konto", "Opublikuj", "Przenieś środki", "Zamknij konto"):
            with self.subTest(target=target):
                self.assertTrue(requires_browser_confirmation("smart_click", target))

    def test_safe_browser_actions_do_not_require_confirmation(self):
        for target in ("Open settings", "Next tab", "Search", "Read more"):
            with self.subTest(target=target):
                self.assertFalse(requires_browser_confirmation("click", target))

    def test_non_click_actions_are_not_classified_as_submission(self):
        self.assertFalse(requires_browser_confirmation("type", "Send"))
        self.assertFalse(requires_browser_confirmation("press", "Enter"))
        self.assertFalse(requires_browser_confirmation("go_to", "checkout"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
