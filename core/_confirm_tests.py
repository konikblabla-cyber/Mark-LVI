"""Tests for confirmation cleanup lifecycle callbacks."""
import threading
import unittest
from unittest.mock import patch

from core import confirm


class ConfirmationCleanupTests(unittest.TestCase):
    def setUp(self):
        self._old = (
            confirm._pending, confirm._show_cb,
            confirm._hide_cb, confirm._log_cb,
        )
        with confirm._lock:
            confirm._pending = None
        self.shown = []
        self.hidden = []
        self.logs = []
        confirm.bind(
            lambda title, detail: self.shown.append((title, detail)),
            lambda: self.hidden.append(True),
            lambda message: self.logs.append(message),
        )

    def tearDown(self):
        with confirm._lock:
            confirm._pending = self._old[0]
        confirm.bind(self._old[1], self._old[2], self._old[3])

    def test_cancel_releases_action_owned_state(self):
        cleaned = []
        result = confirm.request(
            "test", "Cancel test", "detail",
            lambda: "should not run",
            on_cancel=lambda: cleaned.append("cancelled"),
        )
        self.assertIn("[CONFIRMATION_PENDING]", result)
        confirm.resolve(False)
        self.assertEqual(cleaned, ["cancelled"])
        self.assertEqual(len(self.hidden), 1)

    def test_expired_confirmation_releases_action_owned_state(self):
        cleaned = []
        with patch.object(confirm.time, "monotonic", side_effect=[100.0, 100.0 + confirm.TIMEOUT_SECONDS + 1]):
            result = confirm.request(
                "test", "Expire test", "detail",
                lambda: "should not run",
                on_cancel=lambda: cleaned.append("expired"),
            )
            self.assertIn("[CONFIRMATION_PENDING]", result)
            confirm.resolve(True)
        self.assertEqual(cleaned, ["expired"])
        self.assertTrue(any("expired" in message.lower() for message in self.logs))

    def test_pending_title_expires_stale_prompt_and_releases_state(self):
        cleaned = []
        with patch.object(confirm.time, "monotonic", side_effect=[
            100.0, 100.0 + confirm.TIMEOUT_SECONDS + 1,
        ]):
            result = confirm.request(
                "test", "Stale prompt", "detail",
                lambda: "should not run",
                on_cancel=lambda: cleaned.append("expired"),
            )
            self.assertIn("[CONFIRMATION_PENDING]", result)
            self.assertEqual(confirm.pending_title(), "")
        self.assertEqual(cleaned, ["expired"])
        self.assertEqual(len(self.hidden), 1)

    def test_unavailable_hud_releases_action_owned_state(self):
        cleaned = []
        confirm.bind(None, None, None)
        result = confirm.request(
            "test", "No HUD", "detail", lambda: "should not run",
            on_cancel=lambda: cleaned.append("unavailable"),
        )
        self.assertIn("not available", result)
        self.assertEqual(cleaned, ["unavailable"])

    def test_failed_hud_display_clears_pending_and_releases_state(self):
        cleaned = []
        def fail_show(_title, _detail):
            raise RuntimeError("HUD unavailable")
        confirm.bind(fail_show, lambda: None, None)
        result = confirm.request(
            "test", "Show failure", "detail", lambda: "should not run",
            on_cancel=lambda: cleaned.append("display_failed"),
        )
        self.assertIn("Nothing was done", result)
        self.assertEqual(cleaned, ["display_failed"])
        self.assertEqual(confirm.pending_title(), "")

    def test_accepted_confirmation_runs_action_without_cleanup(self):
        cleaned = []
        finished = threading.Event()
        result = confirm.request(
            "test", "Accept test", "detail",
            lambda: (finished.set() or "completed"),
            on_cancel=lambda: cleaned.append("incorrect"),
        )
        self.assertIn("[CONFIRMATION_PENDING]", result)
        confirm.resolve(True)
        self.assertTrue(finished.wait(2))
        self.assertEqual(cleaned, [])

    def test_action_exception_releases_action_owned_state(self):
        cleaned = []
        finished = threading.Event()
        def fail():
            finished.set()
            raise RuntimeError("failure")
        result = confirm.request(
            "test", "Worker failure", "detail", fail,
            on_cancel=lambda: cleaned.append("worker_failed"),
        )
        self.assertIn("[CONFIRMATION_PENDING]", result)
        confirm.resolve(True)
        self.assertTrue(finished.wait(2))
        # resolve() catches the worker exception and performs cleanup there.
        for _ in range(100):
            if cleaned:
                break
            finished.wait(0.01)
            threading.Event().wait(0.01)
        self.assertEqual(cleaned, ["worker_failed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
