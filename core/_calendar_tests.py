"""Offline tests for the opt-in Google Calendar plugin. No account or network required."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import google_calendar as calendar


class CalendarInputTests(unittest.TestCase):
    def test_event_payload_uses_configured_timezone_for_naive_times(self):
        with patch.object(calendar, "_config", return_value={"timezone": "Europe/Warsaw"}):
            payload = calendar._event_payload({
                "title": "Test event",
                "start": "2026-11-09T16:00:00",
                "end": "2026-11-09T16:45:00",
            })
        self.assertEqual(payload["summary"], "Test event")
        self.assertEqual(payload["start"]["timeZone"], "Europe/Warsaw")
        self.assertEqual(payload["end"]["timeZone"], "Europe/Warsaw")

    def test_event_payload_rejects_invalid_or_reversed_times(self):
        with patch.object(calendar, "_config", return_value={"timezone": "Europe/Warsaw"}):
            with self.assertRaises(ValueError):
                calendar._event_payload({
                    "title": "Bad event",
                    "start": "not-a-date",
                    "end": "2026-11-09T16:45:00",
                })
            with self.assertRaises(ValueError):
                calendar._event_payload({
                    "title": "Backwards event",
                    "start": "2026-11-09T17:00:00",
                    "end": "2026-11-09T16:00:00",
                })

    def test_event_payload_rejects_mixed_timezone_awareness(self):
        with patch.object(calendar, "_config", return_value={"timezone": "Europe/Warsaw"}):
            with self.assertRaises(ValueError):
                calendar._event_payload({
                    "title": "Mixed timezone",
                    "start": "2026-11-09T16:00:00+01:00",
                    "end": "2026-11-09T16:45:00",
                })


class CalendarSafetyTests(unittest.TestCase):
    def test_local_daily_request_cap_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            with patch.object(calendar, "_USAGE_PATH", path), \
                 patch.object(calendar, "_MAX_DAILY_REQUESTS", 2), \
                 patch.object(calendar, "_today_key", return_value="2026-11-08"):
                self.assertEqual(calendar._reserve_daily_request(), 1)
                self.assertEqual(calendar._reserve_daily_request(), 2)
                with self.assertRaisesRegex(RuntimeError, "limit reached"):
                    calendar._reserve_daily_request()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["count"], 2)

    def test_corrupt_request_ledger_does_not_reset_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(calendar, "_USAGE_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "unreadable"):
                    calendar._reserve_daily_request()
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_create_event_waits_for_hud_confirmation(self):
        from core import confirm
        captured = {}
        parameters = {
            "title": "Dentist",
            "start": "2026-11-09T16:00:00",
            "end": "2026-11-09T16:30:00",
        }

        def request(key, title, detail, run):
            captured["run"] = run
            captured["detail"] = detail
            return "[CONFIRMATION_PENDING]"

        with patch.object(calendar, "_config", return_value={"timezone": "Europe/Warsaw"}), \
             patch.object(confirm, "pending_title", return_value=""), \
             patch.object(confirm, "request", side_effect=request), \
             patch.object(calendar, "_access_token", return_value="fake-token"), \
             patch.object(calendar, "_api_request", return_value={
                 "summary": "Dentist",
                 "start": {"dateTime": "2026-11-09T16:00:00"},
             }) as api:
            result = calendar._create_event(parameters)
            self.assertIn("CONFIRMATION_PENDING", result)
            api.assert_not_called()
            self.assertIn("Nothing is sent until you confirm", captured["detail"])
            completed = captured["run"]()
            self.assertIn("Created calendar event", completed)
            api.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
