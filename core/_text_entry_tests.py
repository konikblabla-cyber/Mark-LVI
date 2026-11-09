"""Offline tests for safe local text-field entry."""
import unittest
from unittest.mock import patch

from actions import text_entry
from core.action_loader import _validate


class TextEntryTests(unittest.TestCase):
    @staticmethod
    def _field(label="Search query"):
        return {
            "role": "Edit",
            "name": label,
            "automation_id": "edit_1",
            "runtime_id": (1, 2, 3),
            "rect": (10, 10, 200, 40),
        }

    @staticmethod
    def _snapshot(*fields):
        return {"handle": 101, "process_id": 4321, "fields": list(fields)}

    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(text_entry, "text_entry.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "fill_text_field")
        self.assertEqual(
            set(record.parameters["required"]),
            {"field_label", "text"},
        )

    def test_request_requires_exact_label_and_nonempty_text(self):
        for params in (
            {},
            {"field_label": "Search query"},
            {"text": "hello"},
            {"field_label": " ", "text": "hello"},
            {"field_label": "Search query", "text": ""},
            {"field_label": "Search query", "text": "bad\x00text"},
            {"field_label": "Search query", "text": "x" * 4001},
        ):
            with self.subTest(params=str(params)[:80]):
                label, value, error = text_entry._validate_request(params)
                self.assertIsNone(label)
                self.assertIsNone(value)
                self.assertTrue(error)

    def test_sensitive_fields_are_rejected_before_any_ui_inspection(self):
        for label in (
            "Password", "One-time code", "PIN", "OTP code",
            "Credit card number", "CVV", "API key", "Access token",
            "Bank account", "Hasło",
        ):
            with self.subTest(label=label):
                result = text_entry.fill_text_field({
                    "field_label": label, "text": "do not enter",
                })
                self.assertTrue(
                    result.startswith("status=failed action=fill_text_field "),
                    result,
                )

    def test_sensitive_label_classifier_uses_words_not_substrings(self):
        self.assertTrue(text_entry._is_sensitive_label("Enter PIN"))
        self.assertTrue(text_entry._is_sensitive_label("Your API key"))
        self.assertFalse(text_entry._is_sensitive_label("Shipping address"))

    def test_value_normalisation_only_changes_line_endings(self):
        self.assertEqual(
            text_entry._normalise_value("one\r\ntwo\rthree"),
            "one\ntwo\nthree",
        )

    def test_field_matching_is_exact_and_case_insensitive(self):
        fields = [
            self._field("Search query"),
            self._field("Search query advanced"),
            self._field("Other"),
        ]
        matches = text_entry._matching_fields(fields, " search   QUERY ")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["name"], "Search query")

    def test_missing_or_ambiguous_field_never_calls_uia_set(self):
        with patch.object(text_entry.ui, "_run_uia") as run:
            missing = text_entry._fill_text_field(
                "Search query", "hello", self._snapshot(self._field("Other")),
            )
            duplicate = text_entry._fill_text_field(
                "Search query", "hello",
                self._snapshot(self._field(), self._field()),
            )
        self.assertTrue(missing.startswith("status=failed "), missing)
        self.assertTrue(duplicate.startswith("status=ask "), duplicate)
        run.assert_not_called()

    def test_verified_entry_does_not_return_or_log_the_entered_text(self):
        secretish_text = "private draft text that must not appear in output"
        snapshot = self._snapshot(self._field())
        with patch.object(
            text_entry.ui, "_run_uia",
            return_value=(True, ("verified", ""), None),
        ) as run:
            result = text_entry._fill_text_field(
                "Search query", secretish_text, snapshot,
            )
        self.assertTrue(result.startswith("status=done action=fill_text_field "), result)
        self.assertIn("verified=true", result)
        self.assertNotIn(secretish_text, result)
        self.assertEqual(run.call_args.kwargs["exclusive_lock"], text_entry.ui._INVOKE_LOCK)

    def test_already_matching_value_is_reported_without_claiming_a_new_write(self):
        with patch.object(
            text_entry.ui, "_run_uia",
            return_value=(True, ("already_set", "already present"), None),
        ):
            result = text_entry._fill_text_field(
                "Search query", "hello", self._snapshot(self._field()),
            )
        self.assertIn("already_set=true", result)
        self.assertNotIn("hello", result)

    def test_uncertain_or_timed_out_entry_is_never_reported_as_done(self):
        with patch.object(
            text_entry.ui, "_run_uia",
            return_value=(True, ("uncertain", "ValuePattern raised RuntimeError"), None),
        ):
            uncertain = text_entry._fill_text_field(
                "Search query", "hello", self._snapshot(self._field()),
            )
        with patch.object(
            text_entry.ui, "_run_uia",
            return_value=(False, None, None),
        ):
            timed_out = text_entry._fill_text_field(
                "Search query", "hello", self._snapshot(self._field()),
            )
        self.assertTrue(uncertain.startswith("status=unverified "), uncertain)
        self.assertTrue(timed_out.startswith("status=unverified "), timed_out)
        self.assertNotIn("hello", uncertain)
        self.assertNotIn("hello", timed_out)

    def test_invalid_or_sensitive_request_never_inspects_desktop(self):
        with patch.object(text_entry, "_snapshot") as snapshot:
            result = text_entry.fill_text_field({
                "field_label": "Password", "text": "secret",
            })
        self.assertTrue(result.startswith("status=failed "), result)
        snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
